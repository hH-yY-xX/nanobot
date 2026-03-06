"""RESTful API channel for HTTP-based message handling."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from aiohttp import web
from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import ApiConfig


class ApiChannel(BaseChannel):
    """
    RESTful API channel using aiohttp.

    Provides HTTP endpoints for sending messages and receiving responses,
    following the same channel -> messagebus -> loop flow as other channels.

    Endpoints:
        POST /api/v1/chat - Send a message and receive the response
        GET /api/v1/health - Health check endpoint
    """

    name = "api"

    def __init__(self, config: ApiConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: ApiConfig = config
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        # Track pending requests: request_id -> (asyncio.Event, response)
        self._pending_requests: dict[str, tuple[asyncio.Event, OutboundMessage | None]] = {}

    async def start(self) -> None:
        """Start the HTTP server."""
        self._running = True

        self._app = web.Application()
        self._app.router.add_post("/api/v1/chat", self._handle_chat)
        self._app.router.add_get("/api/v1/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(
            self._runner,
            self.config.host,
            self.config.port,
        )
        await self._site.start()

        logger.info(
            "API channel started on http://{}:{}",
            self.config.host,
            self.config.port,
        )

        # Start the response listener
        await self._listen_responses()

    async def stop(self) -> None:
        """Stop the HTTP server."""
        self._running = False

        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

        # Cancel all pending requests
        for request_id, (event, _) in self._pending_requests.items():
            event.set()
        self._pending_requests.clear()

        logger.info("API channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """
        Handle outbound message by matching it to a pending request.

        This is called by the ChannelManager when a response is ready.
        """
        # Find matching pending request by chat_id (which contains request_id)
        request_id = msg.chat_id
        if request_id in self._pending_requests:
            event, _ = self._pending_requests[request_id]
            self._pending_requests[request_id] = (event, msg)
            event.set()

    async def _listen_responses(self) -> None:
        """Keep the channel running (responses handled via send())."""
        while self._running:
            await asyncio.sleep(1.0)

    def _check_auth(self, request: web.Request) -> bool:
        """Check API key authentication if configured."""
        if not self.config.api_key:
            return True  # No auth required

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return token == self.config.api_key

        # Also check X-API-Key header
        api_key = request.headers.get("X-API-Key", "")
        return api_key == self.config.api_key

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok",
            "channel": self.name,
            "running": self._running,
        })

    async def _handle_chat(self, request: web.Request) -> web.Response:
        """
        Handle chat message request.

        Request body:
        {
            "message": "Hello, how are you?",
            "sender_id": "user123",        # Optional, defaults to "api_user"
            "session_id": "custom_session" # Optional, defaults to request_id
        }

        Response:
        {
            "response": "I'm doing well, thank you!",
            "request_id": "uuid",
            "channel": "api"
        }
        """
        # Check authentication
        if not self._check_auth(request):
            return web.json_response(
                {"error": "Unauthorized", "detail": "Invalid or missing API key"},
                status=401,
            )

        # Parse request body
        try:
            body = await request.json()
        except Exception as e:
            return web.json_response(
                {"error": "Bad Request", "detail": f"Invalid JSON: {e}"},
                status=400,
            )

        message = body.get("message")
        if not message:
            return web.json_response(
                {"error": "Bad Request", "detail": "Missing 'message' field"},
                status=400,
            )

        sender_id = body.get("sender_id", "api_user")
        request_id = str(uuid.uuid4())
        session_id = body.get("session_id", request_id)

        # Check if sender is allowed
        if not self.is_allowed(sender_id):
            return web.json_response(
                {"error": "Forbidden", "detail": f"Sender '{sender_id}' not allowed"},
                status=403,
            )

        # Create event for waiting on response
        response_event = asyncio.Event()
        self._pending_requests[request_id] = (response_event, None)

        try:
            # Create and publish inbound message
            msg = InboundMessage(
                channel=self.name,
                sender_id=str(sender_id),
                chat_id=request_id,  # Use request_id as chat_id for routing
                content=message,
                metadata={"request_id": request_id},
                session_key_override=f"api:{session_id}",  # Custom session key
            )

            await self.bus.publish_inbound(msg)

            # Wait for response with timeout
            try:
                await asyncio.wait_for(
                    response_event.wait(),
                    timeout=self.config.timeout,
                )
            except asyncio.TimeoutError:
                return web.json_response(
                    {
                        "error": "Timeout",
                        "detail": f"Request timed out after {self.config.timeout}s",
                        "request_id": request_id,
                    },
                    status=504,
                )

            # Get the response
            _, response = self._pending_requests.get(request_id, (None, None))

            if response is None:
                return web.json_response(
                    {
                        "error": "Internal Error",
                        "detail": "No response received",
                        "request_id": request_id,
                    },
                    status=500,
                )

            return web.json_response({
                "response": response.content,
                "request_id": request_id,
                "channel": self.name,
                "session_id": session_id,
            })

        finally:
            # Clean up pending request
            self._pending_requests.pop(request_id, None)
