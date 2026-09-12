import logging
import os
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

# Configurable constants for intentional Gemini timeouts
DEFAULT_GEMINI_READ_TIMEOUT = 10.0
DEFAULT_GEMINI_CONNECT_TIMEOUT = 5.0


def _redact_key(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


def _classify_gemini_error(status: int, err_detail: str) -> tuple[ProviderErrorCode, float]:
    """
    Classifies Gemini API errors based on status code and message content.
    Detects rate-limit vs quota exhaustion vs auth vs server errors.
    """
    detail_lower = err_detail.lower()

    # Check for specific quota exhaustion indicators
    quota_keywords = [
        "resource_exhausted",
        "quota exceeded",
        "generate_content_free_tier_requests",
        "free_tier",
        "daily quota",
        "quota_exceeded",
        "per_day",
        "exceeded your current quota"
    ]
    is_quota = any(kw in detail_lower for kw in quota_keywords)

    if is_quota or (status == 429 and "quota" in detail_lower):
        return ProviderErrorCode.QUOTA_EXCEEDED, DEFAULT_QUOTA_COOLDOWN

    if status == 429:
        return ProviderErrorCode.RATE_LIMIT, DEFAULT_RATE_LIMIT_COOLDOWN

    if status == 400:
        if "invalid" in detail_lower and "key" in detail_lower:
            return ProviderErrorCode.AUTHENTICATION_ERROR, DEFAULT_AUTH_ERROR_COOLDOWN
        return ProviderErrorCode.INVALID_REQUEST, 0.0

    if status in (401, 403):
        return ProviderErrorCode.AUTHENTICATION_ERROR, DEFAULT_AUTH_ERROR_COOLDOWN

    if status == 404:
        return ProviderErrorCode.MODEL_UNAVAILABLE, DEFAULT_MODEL_UNAVAILABLE_COOLDOWN

    if status >= 500:
        return ProviderErrorCode.SERVER_ERROR, DEFAULT_SERVER_ERROR_COOLDOWN

    return ProviderErrorCode.UNKNOWN, 60.0


class GeminiProvider(AIProvider):
    name: str = "gemini"

    def __init__(self, api_keys: List[str], model: Optional[str] = None):
        super().__init__(api_keys)
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Reuse or create an async HTTP client session."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client if open."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def generate_response_with_key(
        self,
        api_key: str,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 150,
        timeout: float = 10.0,
        **kwargs
    ) -> AIResponse:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

        payload: Dict[str, Any] = {}

        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        contents = []
        for msg in messages:
            role = "model" if msg.get("role") in ("assistant", "model", "bot") else "user"
            content_text = (msg.get("content") or "").strip()
            if not content_text:
                continue
            if msg.get("name") and role == "user":
                content_text = f"[{msg['name']}]: {content_text}"

            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"][0]["text"] += f"\n{content_text}"
            else:
                contents.append({
                    "role": role,
                    "parts": [{"text": content_text}]
                })

        while contents and contents[0]["role"] == "model":
            contents.pop(0)

        if not contents:
            raise AIProviderError(
                message="Gemini request requires at least one non-empty message",
                error_code=ProviderErrorCode.INVALID_REQUEST,
                suggested_cooldown=0.0
            )

        payload["contents"] = contents
        payload["generationConfig"] = {
            "maxOutputTokens": max_tokens,
            "temperature": 0.7,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }

        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json"
        }

        req_timeout = float(timeout) if timeout and float(timeout) > 0 else DEFAULT_GEMINI_READ_TIMEOUT
        read_timeout = min(req_timeout, DEFAULT_GEMINI_READ_TIMEOUT)
        http_timeout = httpx.Timeout(
            connect=DEFAULT_GEMINI_CONNECT_TIMEOUT,
            read=read_timeout,
            write=5.0,
            pool=5.0
        )

        client = await self._get_client()
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=http_timeout)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as te:
            err_msg = f"Gemini API timeout | provider={self.name} model='{self.model}' timeout={read_timeout}s: {type(te).__name__}"
            logger.warning(_redact_key(err_msg, api_key))
            raise AIProviderError(
                message=err_msg,
                error_code=ProviderErrorCode.TIMEOUT,
                suggested_cooldown=DEFAULT_TIMEOUT_COOLDOWN,
                raw_error=te
            ) from te
        except httpx.RequestError as re:
            err_msg = f"Gemini API network error | provider={self.name} model='{self.model}': {type(re).__name__}"
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
                    message=f"Gemini response is not valid JSON: {je}",
                    error_code=ProviderErrorCode.SERVER_ERROR,
                    suggested_cooldown=DEFAULT_SERVER_ERROR_COOLDOWN
                ) from je

            if isinstance(data, dict):
                candidates = data.get("candidates", [])
                if isinstance(candidates, list) and candidates:
                    candidate = candidates[0]
                    if isinstance(candidate, dict):
                        finish_reason = candidate.get("finishReason")
                        if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"):
                            logger.warning(f"Gemini API generation blocked due to finishReason: {finish_reason}")
                            raise AIProviderError(
                                message=f"Gemini response content blocked due to finishReason '{finish_reason}'",
                                error_code=ProviderErrorCode.INVALID_REQUEST,
                                suggested_cooldown=0.0
                            )

                        content = candidate.get("content", {})
                        if isinstance(content, dict):
                            parts = content.get("parts", [])
                            if isinstance(parts, list):
                                text_parts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
                                full_text = "".join(text_parts).strip()
                                if full_text:
                                    usage = data.get("usageMetadata")
                                    return AIResponse(
                                        text=full_text,
                                        provider=self.name,
                                        model=self.model,
                                        usage=usage
                                    )

            raise AIProviderError(
                message="Gemini returned invalid or empty content response structure",
                error_code=ProviderErrorCode.EMPTY_RESPONSE,
                suggested_cooldown=DEFAULT_SERVER_ERROR_COOLDOWN
            )

        err_detail = ""
        try:
            err_json = resp.json()
            if isinstance(err_json, dict):
                err_obj = err_json.get("error", {})
                if isinstance(err_obj, dict):
                    err_detail = err_obj.get("message", "") or str(err_obj)
                elif isinstance(err_obj, str):
                    err_detail = err_obj
                else:
                    err_detail = str(err_json)
        except Exception:
            err_detail = resp.text[:200]

        err_detail = _redact_key(err_detail, api_key)
        status = resp.status_code

        code, cooldown = _classify_gemini_error(status, err_detail)
        log_msg = f"Gemini API Error Status {status} ({code.value}, model='{self.model}'): {err_detail}"
        logger.warning(log_msg)

        raise AIProviderError(
            message=f"Gemini API Error Status {status}: {err_detail or log_msg}",
            status_code=status,
            error_code=code,
            suggested_cooldown=cooldown,
            raw_error=resp
        )
