"""LLM 提供商基础接口。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRequest:
    """来自 LLM 的工具调用请求。"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """来自 LLM 提供商的响应。"""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1 etc.
    thinking_blocks: list[dict] | None = None  # Anthropic extended thinking

    @property
    def has_tool_calls(self) -> bool:
        """检查响应是否包含工具调用。"""
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """
    LLM 提供商的抽象基类。

    实现应该处理每个提供商 API 的具体细节，同时保持一致的接口。
    """

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base

    @staticmethod
    def _sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """替换导致提供商 400 错误的空文本内容。

        当 MCP 工具返回空值时可能出现空内容。大多数提供商拒绝空字符串内容或列表内容中的空文本块。
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")

            if isinstance(content, str) and not content:
                clean = dict(msg)
                clean["content"] = None if (msg.get("role") == "assistant" and msg.get("tool_calls")) else "(empty)"
                result.append(clean)
                continue

            if isinstance(content, list):
                filtered = [
                    item for item in content
                    if not (
                        isinstance(item, dict)
                        and item.get("type") in ("text", "input_text", "output_text")
                        and not item.get("text")
                    )
                ]
                if len(filtered) != len(content):
                    clean = dict(msg)
                    if filtered:
                        clean["content"] = filtered
                    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                        clean["content"] = None
                    else:
                        clean["content"] = "(empty)"
                    result.append(clean)
                    continue

            if isinstance(content, dict):
                clean = dict(msg)
                clean["content"] = [content]
                result.append(clean)
                continue

            result.append(msg)
        return result

    @staticmethod
    def _sanitize_request_messages(
        messages: list[dict[str, Any]],
        allowed_keys: frozenset[str],
    ) -> list[dict[str, Any]]:
        """Keep only provider-safe message keys and normalize assistant content."""
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed_keys}
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """
        发送聊天完成请求。

        参数：
            messages: 带有 'role' 和 'content' 的消息字典列表
            tools: 可选的工具定义列表
            model: 模型标识符（提供商特定）
            max_tokens: 响应中的最大 token 数
            temperature: 采样温度

        返回：
            包含内容和/或工具调用的 LLMResponse
        """
        pass

    @abstractmethod
    def get_default_model(self) -> str:
        """获取此提供商的默认模型。"""
        pass
