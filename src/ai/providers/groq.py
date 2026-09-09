import logging
import httpx
from typing import List, Dict, Optional, Any
from .base import (
    AIProvider,
    AIResponse,
    AIProviderError,
    ProviderErrorCode,
    DEFAULT_QUOTA_COOLDOWN,
    DEFAULT_RATE_LIMIT_COOLDOWN,
    DEFAULT_AUTH_ERROR_COOLDOWN,
    DEFAULT_SERVER_ERROR_COOLDOWN,
    DEFAULT_MODEL_UNAVAILABLE_COOLDOWN,
    DEFAULT_TIMEOUT_COOLDOWN,
)

logger = logging.getLogger(__name__)


def _redact_key(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


def _classify_groq_error(status: int, err_detail: str) -> tuple[ProviderErrorCode, float]:
    detail_lower = err_detail.lower()

    if "quota" in detail_lower or "resource_exhausted" in detail_lower or "rate limit" in detail_lower and "daily" in detail_lower:
        return ProviderErrorCode.QUOTA_EXCEEDED, DEFAULT_QUOTA_COOLDOWN

    if status == 429:
        return ProviderErrorCode.RATE_LIMIT, DEFAULT_RATE_LIMIT_COOLDOWN

    if status == 400:
        return ProviderErrorCode.INVALID_REQUEST, 0.0

    if status in (401, 403):
        return ProviderErrorCode.AUTHENTICATION_ERROR, DEFAULT_AUTH_ERROR_COOLDOWN

    if status == 404:
        return ProviderErrorCode.MODEL_UNAVAILABLE, DEFAULT_MODEL_UNAVAILABLE_COOLDOWN

    if status >= 500:
        return ProviderErrorCode.SERVER_ERROR, DEFAULT_SERVER_ERROR_COOLDOWN

    return ProviderErrorCode.UNKNOWN, 60.0


class GroqProvider(AIProvider):
    name: str = "groq"

    def __init__(self, api_keys: List[str], model: str = "llama-3.3-70b-versatile"):
        super().__init__(api_keys)
        self.model = model

    async def generate_response_with_key(
        self,
        api_key: str,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 150,
        timeout: float = 20.0,
        **kwargs
    ) -> AIResponse:
        url = "https://api.groq.com/openai/v1/chat/completions"

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = "assistant" if msg.get("role") in ("assistant", "model", "bot") else "user"
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if msg.get("name") and role == "user":
                content = f"[{msg['name']}]: {content}"
            formatted_messages.append({"role": role, "content": content})

        if not formatted_messages or (len(formatted_messages) == 1 and formatted_messages[0].get("role") == "system"):
            raise AIProviderError(
                message="Groq request requires at least one non-empty message",
                error_code=ProviderErrorCode.INVALID_REQUEST,
                suggested_cooldown=0.0
            )

        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "temperature": 0.7
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        req_timeout = float(timeout) if timeout and float(timeout) > 0 else 10.0
        http_timeout = httpx.Timeout(
            connect=5.0,
            read=min(req_timeout, 10.0),
            write=5.0,
            pool=5.0
        )

        async with httpx.AsyncClient(timeout=http_timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as te:
                err_msg = f"Groq API timeout for model '{self.model}': {type(te).__name__}"
                logger.warning(_redact_key(err_msg, api_key))
                raise AIProviderError(
                    message=err_msg,
                    error_code=ProviderErrorCode.TIMEOUT,
                    suggested_cooldown=DEFAULT_TIMEOUT_COOLDOWN,
                    raw_error=te
                ) from te
            except httpx.RequestError as re:
                err_msg = f"Groq API network error for model '{self.model}': {type(re).__name__}"
                logger.warning(_redact_key(err_msg, api_key))
                raise AIProviderError(
                    message=err_msg,
                    error_code=ProviderErrorCode.NETWORK_ERROR,
                    suggested_cooldown=DEFAULT_TIMEOUT_COOLDOWN,
                    raw_error=re
                ) from re

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as je:
                    raise AIProviderError(
                        message=f"Groq response is not valid JSON: {je}",
                        error_code=ProviderErrorCode.SERVER_ERROR,
                        suggested_cooldown=DEFAULT_SERVER_ERROR_COOLDOWN
                    ) from je

                if isinstance(data, dict):
                    choices = data.get("choices")
                    if isinstance(choices, list) and len(choices) > 0:
                        first_choice = choices[0]
                        if isinstance(first_choice, dict):
                            message_obj = first_choice.get("message")
                            if isinstance(message_obj, dict):
                                content_val = message_obj.get("content")
                                if content_val is not None:
                                    text = str(content_val).strip()
                                    if text:
                                        usage = data.get("usage")
                                        return AIResponse(text=text, provider=self.name, model=self.model, usage=usage)

                raise AIProviderError(
                    message="Groq returned invalid or empty content response structure",
                    error_code=ProviderErrorCode.EMPTY_RESPONSE,
                    suggested_cooldown=DEFAULT_SERVER_ERROR_COOLDOWN
                )

            err_body = getattr(resp, "text", "") or ""
            err_body = err_body[:200]
            err_body = _redact_key(err_body, api_key)
            status = resp.status_code

            code, cooldown = _classify_groq_error(status, err_body)
            log_msg = f"Groq API Error Status {status} ({code.value}, model='{self.model}'): {err_body}"
            logger.warning(_redact_key(log_msg, api_key))

            raise AIProviderError(
                message=f"Groq API Error Status {status}: {err_body}",
                status_code=status,
                error_code=code,
                suggested_cooldown=cooldown,
                raw_error=resp
            )
