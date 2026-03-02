"""用于多提供商支持的 LiteLLM 提供商实现。"""

import json
import json_repair
import os
import secrets
import string
from typing import Any

import litellm
from litellm import acompletion

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.registry import find_by_model, find_gateway


# 标准 OpenAI 聊天完成消息键，加上支持推理的模型（Kimi k2.5、DeepSeek-R1 等）的 reasoning_content
_ALLOWED_MSG_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"})
_ALNUM = string.ascii_letters + string.digits

def _short_tool_id() -> str:
    """生成与所有提供商兼容的 9 字符字母数字 ID（包括 Mistral）。"""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


class LiteLLMProvider(LLMProvider):
    """
    使用 LiteLLM 进行多提供商支持的 LLM 提供商。

    通过统一接口支持 OpenRouter、Anthropic、OpenAI、Gemini、MiniMax 和许多其他提供商。
    提供商特定的逻辑由注册表驱动（参见 providers/registry.py）——这里不需要 if-elif 链。
    """
    
    def __init__(
        self, 
        api_key: str | None = None, 
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        
        # 检测网关 / 本地部署
        # provider_name（来自配置键）是主要信号；
        # api_key / api_base 用于自动检测回退
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # 配置环境变量
        if api_key:
            self._setup_env(api_key, api_base, default_model)
        
        if api_base:
            litellm.api_base = api_base
        
        # 禁用 LiteLLM 日志噪音
        litellm.suppress_debug_info = True
        # 为提供商丢弃不支持的参数（例如，gpt-5 拒绝某些参数）
        litellm.drop_params = True
    
    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """根据检测到的提供商设置环境变量。"""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return
        if not spec.env_key:
            # 仅 OAuth/提供商规范（例如：openai_codex）
            return

        # 网关/本地覆盖现有环境变量；标准提供商不覆盖
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # 解析 env_extras 占位符：
        #   {api_key}  → 用户的 API 密钥
        #   {api_base} → 用户的 api_base，回退到 spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)
    
    def _resolve_model(self, model: str) -> str:
        """通过应用提供商/网关前缀来解析模型名称。"""
        if self._gateway:
            # 网关模式：应用网关前缀，跳过提供商特定前缀
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # 标准模式：为已知提供商自动添加前缀
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            model = self._canonicalize_explicit_prefix(model, spec.name, spec.litellm_prefix)
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    @staticmethod
    def _canonicalize_explicit_prefix(model: str, spec_name: str, canonical_prefix: str) -> str:
        """规范化显式提供商前缀，如 `github-copilot/...`"""
        if "/" not in model:
            return model
        prefix, remainder = model.split("/", 1)
        if prefix.lower().replace("-", "_") != spec_name:
            return model
        return f"{canonical_prefix}/{remainder}"
    
    def _supports_cache_control(self, model: str) -> bool:
        """当提供商支持内容块上的 cache_control 时返回 True。"""
        if self._gateway is not None:
            return self._gateway.supports_prompt_caching
        spec = find_by_model(model)
        return spec is not None and spec.supports_prompt_caching

    def _apply_cache_control(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """返回注入了 cache_control 的消息和工具的副本。"""
        new_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg["content"]
                if isinstance(content, str):
                    new_content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
                else:
                    new_content = list(content)
                    new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
                new_messages.append({**msg, "content": new_content})
            else:
                new_messages.append(msg)

        new_tools = tools
        if tools:
            new_tools = list(tools)
            new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}

        return new_messages, new_tools

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """应用来自注册表的模型特定参数覆盖。"""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return
    
    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """去除非标准键并确保助手消息具有 content 键。"""
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in _ALLOWED_MSG_KEYS}
            # 严格的提供商需要 "content"，即使助手只有 tool_calls
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        通过 LiteLLM 发送聊天完成请求。

        参数：
            messages: 带有 'role' 和 'content' 的消息字典列表
            tools: OpenAI 格式的可选工具定义列表
            model: 模型标识符（例如，'anthropic/claude-sonnet-4-5'）
            max_tokens: 响应中的最大 token 数
            temperature: 采样温度

        返回：
            包含内容和/或工具调用的 LLMResponse
        """
        original_model = model or self.default_model
        model = self._resolve_model(original_model)

        if self._supports_cache_control(original_model):
            messages, tools = self._apply_cache_control(messages, tools)

        # 将 max_tokens 限制为至少 1 —— 负值或零值会导致
        # LiteLLM 以 "max_tokens must be at least 1" 拒绝请求
        max_tokens = max(1, max_tokens)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # 应用模型特定的覆盖（例如 kimi-k2.5 温度）
        self._apply_model_overrides(model, kwargs)

        # 直接传递 api_key —— 比仅使用环境变量更可靠
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # 为自定义端点传递 api_base
        if self.api_base:
            kwargs["api_base"] = self.api_base

        # 传递额外头部（例如 AiHubMix 的 APP-Code）
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        
        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            # 将错误作为内容返回以进行优雅处理
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """将 LiteLLM 响应解析为我们的标准格式。"""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # 如果需要，从 JSON 字符串解析参数
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json_repair.loads(args)

                tool_calls.append(ToolCallRequest(
                    id=_short_tool_id(),
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        reasoning_content = getattr(message, "reasoning_content", None) or None

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )
    
    def get_default_model(self) -> str:
        """获取默认模型。"""
        return self.default_model
