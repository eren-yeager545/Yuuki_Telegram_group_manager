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

    if "quota" in detail_lower or "resource_exhausted" in detail_lower or ("rate limit" in detail_lower and "daily" in detail_lower):
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


def _extract_content_text(content_val: Any) -> str:
    """Safely extract textual content from string or structured content representations."""
    if content_val is None:
        return ""
    if isinstance(content_val, str):
        return content_val.strip()
    if isinstance(content_val, list):
        parts = []
        for item in content_val:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif "text" in item and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts).strip()
    return str(content_val).strip()


class GroqProvider(AIProvider):
    name: str = "groq"

    def __init__(self, api_keys: List[str], model: str = "openai/gpt-oss-120b"):
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
                    raw_text = getattr(resp, "text", "") or ""
                    preview = _redact_key(raw_text[:200], api_key)
                    logger.warning(
                        f"Groq invalid JSON in 200 response: status=200 preview={preview}"
                    )
                    raise AIProviderError(
                        message=f"Groq response is not valid JSON: {je}",
                        status_code=200,
                        error_code=ProviderErrorCode.SERVER_ERROR,
                        suggested_cooldown=0.0
                    ) from je

                if isinstance(data, dict):
                    if "error" in data and data["error"]:
                        err_obj = data["error"]
                        err_msg = ""
                        if isinstance(err_obj, dict):
                            err_msg = err_obj.get("message", "") or str(err_obj)
                        else:
                            err_msg = str(err_obj)
                        err_msg = _redact_key(err_msg, api_key)
                        code, cooldown = _classify_groq_error(200, err_msg)
                        logger.warning(
                            f"Groq JSON Error in 200 Response: code={code.value} msg={err_msg}"
                        )
                        raise AIProviderError(
                            message=f"Groq Provider Error: {err_msg}",
                            status_code=200,
                            error_code=code,
                            suggested_cooldown=cooldown,
                            raw_error=data
                        )

                    choices = data.get("choices")
                    if isinstance(choices, list) and len(choices) > 0:
                        first_choice = choices[0]
                        if isinstance(first_choice, dict):
                            message_obj = first_choice.get("message")
                            if isinstance(message_obj, dict):
                                content_val = message_obj.get("content")
                                content_text = _extract_content_text(content_val)
                                if content_text:
                                    usage = data.get("usage")
                                    return AIResponse(
                                        text=content_text,
                                        provider=self.name,
                                        model=self.model,
                                        usage=usage
                                    )

                resp_headers = getattr(resp, "headers", None) or {}
                request_id = resp_headers.get("x-request-id") or resp_headers.get("x-groq-request-id") or resp_headers.get("req_id")
                finish_reason = None
                choices_len = 0
                if isinstance(data, dict):
                    choices = data.get("choices")
                    if isinstance(choices, list):
                        choices_len = len(choices)
                        if choices_len > 0 and isinstance(choices[0], dict):
                            finish_reason = choices[0].get("finish_reason")

                raw_text = getattr(resp, "text", "") or ""
                preview = _redact_key(raw_text[:200], api_key)
                logger.warning(
                    f"Groq empty/invalid response: status=200 request_id={request_id} finish_reason={finish_reason} choices_count={choices_len} preview={preview}"
                )

                raise AIProviderError(
                    message="Groq returned invalid or empty content response structure",
                    status_code=200,
                    error_code=ProviderErrorCode.EMPTY_RESPONSE,
                    suggested_cooldown=0.0,
                    raw_error=data if 'data' in locals() and isinstance(data, dict) else None
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

    async def close(self) -> None:
        pass
