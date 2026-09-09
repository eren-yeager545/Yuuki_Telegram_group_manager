import logging
import os
import httpx
from typing import List, Dict, Optional, Any
from .base import AIProvider, AIResponse, AIProviderError, ProviderErrorCode

logger = logging.getLogger(__name__)


def _redact_key(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


class GeminiProvider(AIProvider):
    name: str = "gemini"

    def __init__(self, api_keys: List[str], model: Optional[str] = None):
        super().__init__(api_keys)
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

    async def generate_response_with_key(
        self,
        api_key: str,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 150,
        timeout: float = 20.0,
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
                error_code=ProviderErrorCode.BAD_REQUEST,
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

        # Fast connection and read timeouts to prevent frozen Telegram updates
        req_timeout = float(timeout) if timeout and float(timeout) > 0 else 10.0
        http_timeout = httpx.Timeout(
            connect=5.0,
            read=min(req_timeout, 10.0),
            write=5.0,
            pool=5.0
        )

        async with httpx.AsyncClient(timeout=http_timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as te:
                err_msg = f"Gemini API timeout for model '{self.model}': {type(te).__name__}"
                logger.warning(err_msg)
                raise AIProviderError(
                    message=err_msg,
                    error_code=ProviderErrorCode.TIMEOUT,
                    suggested_cooldown=30.0,
                    raw_error=te
                ) from te
            except httpx.RequestError as re:
                err_msg = f"Gemini API network error for model '{self.model}': {type(re).__name__}"
                logger.warning(err_msg)
                raise AIProviderError(
                    message=err_msg,
                    error_code=ProviderErrorCode.NETWORK_ERROR,
                    suggested_cooldown=30.0,
                    raw_error=re
                ) from re

            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    candidate = candidates[0]
                    finish_reason = candidate.get("finishReason")
                    if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"):
                        logger.warning(f"Gemini API generation blocked due to finishReason: {finish_reason}")
                        raise AIProviderError(
                            message=f"Gemini response content blocked due to finishReason '{finish_reason}'",
                            error_code=ProviderErrorCode.BAD_REQUEST,
                            suggested_cooldown=0.0
                        )

                    content = candidate.get("content", {})
                    parts = content.get("parts", [])
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
                    error_code=ProviderErrorCode.SERVER_ERROR,
                    suggested_cooldown=30.0
                )

            err_detail = ""
            try:
                err_json = resp.json()
                err_detail = err_json.get("error", {}).get("message", "")
            except Exception:
                err_detail = resp.text[:200]

            err_detail = _redact_key(err_detail, api_key)
            status = resp.status_code

            if status == 400:
                code = ProviderErrorCode.BAD_REQUEST
                cooldown = 0.0
                log_msg = f"Gemini API 400 Bad Request (model='{self.model}'): {err_detail}"
            elif status in (401, 403):
                code = ProviderErrorCode.AUTH_ERROR
                cooldown = 3600.0
                log_msg = f"Gemini API {status} Authentication Error: {err_detail}"
            elif status == 404:
                code = ProviderErrorCode.NOT_FOUND
                cooldown = 3600.0
                log_msg = f"Gemini API 404 Model/Endpoint Not Found (model='{self.model}'): {err_detail}"
            elif status == 429:
                code = ProviderErrorCode.RATE_LIMIT
                cooldown = 120.0
                log_msg = f"Gemini API 429 Rate Limit Exceeded: {err_detail}"
            elif status >= 500:
                code = ProviderErrorCode.SERVER_ERROR
                cooldown = 30.0
                log_msg = f"Gemini API {status} Temporary Server Error: {err_detail}"
            else:
                code = ProviderErrorCode.UNKNOWN
                cooldown = 60.0
                log_msg = f"Gemini API {status} Error: {err_detail}"

            logger.warning(log_msg)

            raise AIProviderError(
                message=f"Gemini API Error Status {status}: {err_detail or log_msg}",
                status_code=status,
                error_code=code,
                suggested_cooldown=cooldown,
                raw_error=resp
            )
