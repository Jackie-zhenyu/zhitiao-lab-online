"""Qwen 的 OpenAI 兼容 tools 适配；不计算指标，不执行工具，不自行重试。"""

import math
import logging
from typing import Protocol

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import ValidationError

from agent.config import LLMSettings
from agent.models import ModelReply, ToolCall


def _discard_sdk_record(record):
    # SDK DEBUG 可包含请求正文、响应头或原始异常；仅保留应用自己的固定执行记录。
    return False


for _logger_name in ("openai._base_client", "openai._client", "openai._response", "openai._legacy_response"):
    logging.getLogger(_logger_name).addFilter(_discard_sdk_record)


_ERROR_MESSAGES = {
    "configuration": "模型配置不可用。",
    "timeout": "模型请求超时。",
    "connection": "无法连接模型服务。",
    "authentication": "模型服务拒绝认证或当前账号无权访问。",
    "rate_limit": "模型服务限流，请稍后再试。",
    "server_error": "模型服务暂时不可用。",
    "request_rejected": "模型服务拒绝请求，请检查地域、模型能力及配置。",
    "redirect_blocked": "模型服务返回重定向，已阻止继续发送凭据。",
    "invalid_response": "模型响应结构无效。",
    "provider_error": "模型请求失败。",
}


class ProviderError(Exception):
    """只有固定错误代码与重试标记，禁止携带 SDK 原始异常或响应。"""

    def __init__(self, code: str, retryable: bool = False):
        self.code = code if code in _ERROR_MESSAGES else "provider_error"
        self.retryable = bool(retryable)
        super().__init__(_ERROR_MESSAGES[self.code])


class ModelProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], *, timeout_s: float) -> ModelReply: ...


class QwenProvider:
    def __init__(self, settings: LLMSettings):
        if not settings.ready:
            raise ProviderError("configuration")
        self.settings = settings

    def complete(self, messages: list[dict], tools: list[dict], *, timeout_s: float) -> ModelReply:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ProviderError("timeout")
        timeout = min(timeout_s, self.settings.request_timeout_s)
        try:
            # 默认 OpenAI HTTP 客户端会跟随重定向；这里显式禁止。
            # 每轮独立关闭连接；不继承代理/OPENAI_* 环境配置，不发起探测请求。
            with httpx.Client(follow_redirects=False, timeout=timeout, trust_env=False) as transport:
                with OpenAI(
                    api_key=self.settings.api_key.get_secret_value(),
                    base_url=self.settings.base_url, max_retries=0, timeout=timeout,
                    http_client=transport, organization="", project="", admin_api_key="", webhook_secret="",
                ) as client:
                    response = client.chat.completions.create(
                        model=self.settings.model, messages=messages, tools=tools,
                        tool_choice="auto", stream=False, max_tokens=self.settings.max_output_tokens,
                        extra_body={"enable_thinking": False}, timeout=timeout,
                    )
            if not response.choices or len(response.choices) != 1:
                raise ProviderError("invalid_response")
            choice = response.choices[0]
            if choice.finish_reason not in ("stop", "tool_calls"):
                raise ProviderError("invalid_response")
            message = choice.message
            calls = []
            for call in message.tool_calls or []:
                if call.type != "function":
                    raise ProviderError("invalid_response")
                calls.append(ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments))
            if message.content is None and not calls:
                raise ProviderError("invalid_response")
            return ModelReply(content=message.content, tool_calls=calls)
        except ProviderError:
            raise
        except APITimeoutError:
            raise ProviderError("timeout", retryable=True) from None
        except APIConnectionError:
            raise ProviderError("connection", retryable=True) from None
        except APIStatusError as error:
            status = error.status_code
            if status in (401, 403):
                code, retryable = "authentication", False
            elif status == 429:
                code, retryable = "rate_limit", True
            elif status in (408, 409):
                code, retryable = "timeout" if status == 408 else "server_error", True
            elif status >= 500:
                code, retryable = "server_error", True
            elif 300 <= status < 400:
                code, retryable = "redirect_blocked", False
            else:
                code, retryable = "request_rejected", False
            raise ProviderError(code, retryable) from None
        except (ValidationError, AttributeError, TypeError, ValueError, IndexError):
            raise ProviderError("invalid_response") from None
        except Exception:
            raise ProviderError("provider_error") from None
