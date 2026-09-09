import logging
import httpx
from typing import List, Dict, Optional, Any
from .base import AIProvider, AIResponse, AIProviderError, ProviderErrorCode

logger = logging.getLogger(__name__)


def _redact_key(text: str, secret: str) -> str:
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


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
                error_code=ProviderErrorCode.BAD_REQUEST,
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
                logger.warning(err_msg)
                raise AIProviderError(
                    message=err_msg,
                    error_code=ProviderErrorCode.TIMEOUT,
                    suggested_cooldown=30.0,
                    raw_error=te
                ) from te
            except httpx.RequestError as re:
                err_msg = f"Groq API network error for model '{self.model}': {type(re).__name__}"
                logger.warning(err_msg)
                raise AIProviderError(
                    message=err_msg,
                    error_code=ProviderErrorCode.NETWORK_ERROR,
                    suggested_cooldown=30.0,
                    raw_error=re
                ) from re

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as je:
                    raise AIProviderError(
                        message=f"Groq response is not valid JSON: {je}",
                        error_code=ProviderErrorCode.SERVER_ERROR,
                        suggested_cooldown=30.0
                    ) from je

                choices = data.get("choices", [])
                if choices and isinstance(choices, list) and isinstance(choices[0], dict) and "message" in choices[0]:
                    text = choices[0]["message"].get("content", "").strip()
                    if text:
                        usage = data.get("usage")
                        return AIResponse(text=text, provider=self.name, model=self.model, usage=usage)
                raise AIProviderError(
                    message="Groq returned invalid or empty content response structure",
                    error_code=ProviderErrorCode.SERVER_ERROR,
                    suggested_cooldown=30.0
                )

            err_body = resp.text[:200]
            err_body = _redact_key(err_body, api_key)
            status = resp.status_code

            if status == 400:
                code = ProviderErrorCode.BAD_REQUEST
                cooldown = 0.0
                log_msg = f"Groq API 400 Bad Request (model='{self.model}'): {err_body}"
            elif status in (401, 403):
                code = ProviderErrorCode.AUTH_ERROR
                cooldown = 3600.0
                log_msg = f"Groq API {status} Authentication Error: {err_body}"
            elif status == 404:
                code = ProviderErrorCode.NOT_FOUND
                cooldown = 3600.0
                log_msg = f"Groq API 404 Model/Endpoint Not Found (model='{self.model}'): {err_body}"
            elif status == 429:
                code = ProviderErrorCode.RATE_LIMIT
                cooldown = 120.0
                log_msg = f"Groq API 429 Rate Limit Exceeded: {err_body}"
            elif status >= 500:
                code = ProviderErrorCode.SERVER_ERROR
                cooldown = 30.0
                log_msg = f"Groq API {status} Temporary Server Error: {err_body}"
            else:
                code = ProviderErrorCode.UNKNOWN
                cooldown = 60.0
                log_msg = f"Groq API {status} Error: {err_body}"

            logger.warning(log_msg)

            raise AIProviderError(
                message=f"Groq API Error Status {status}: {err_body}",
                status_code=status,
                error_code=code,
                suggested_cooldown=cooldown,
                raw_error=resp
            )
