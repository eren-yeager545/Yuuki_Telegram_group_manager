import asyncio
import logging
import os
import httpx
from typing import List, Dict, Optional, Any
from .base import AIProvider, AIResponse

logger = logging.getLogger(__name__)


def _redact_secrets(text: str, secret: str) -> str:
    """Safely redact secret string from log/error output."""
    if not secret or not text:
        return text
    return text.replace(secret, "[REDACTED]")


class OpenRouterProvider(AIProvider):
    name: str = "openrouter"

    def __init__(self, api_keys: List[str], model: Optional[str] = None):
        super().__init__(api_keys)
        self.model = (model or os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b-it:free")).strip() or "google/gemma-4-31b-it:free"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        """Reuse or create an async HTTP client session."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=timeout)
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
        timeout: float = 20.0,
        **kwargs
    ) -> AIResponse:
        url = "https://openrouter.ai/api/v1/chat/completions"

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
            raise ValueError("OpenRouter request requires at least one non-empty message")

        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "provider": {
                "allow_fallbacks": True
            }
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("SUPPORT_GROUP_URL", "https://github.com/eren-yeager545/Yuuki_Telegram_group_manager"),
            "X-Title": "Yuuki Telegram Bot"
        }

        http_timeout = httpx.Timeout(
            connect=10.0,
            read=60.0,
            write=10.0,
            pool=10.0
        ) if not isinstance(timeout, httpx.Timeout) else timeout

        client = await self._get_client(http_timeout)

        retry_count = 0
        max_retries = 1

        while True:
            try:
                resp = await client.post(url, headers=headers, json=payload, timeout=http_timeout)
            except httpx.ReadTimeout as rt:
                logger.warning(f"OpenRouter API read timeout for model '{self.model}'")
                raise rt
            except httpx.ConnectTimeout as ct:
                logger.warning(f"OpenRouter API connect timeout for model '{self.model}'")
                raise ct
            except httpx.TimeoutException as te:
                logger.warning(f"OpenRouter API timeout for model '{self.model}': {type(te).__name__}")
                raise te
            except httpx.RequestError as re:
                logger.warning(f"OpenRouter API request error for model '{self.model}': {type(re).__name__}")
                raise re

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as je:
                    logger.warning(f"OpenRouter API returned invalid JSON response for model '{self.model}'")
                    raise ValueError(f"OpenRouter response is not valid JSON: {je}")

                if isinstance(data, dict):
                    choices = data.get("choices", [])
                    if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                        choice = choices[0]
                        message_obj = choice.get("message")
                        if isinstance(message_obj, dict):
                            content_text = (message_obj.get("content") or "").strip()
                            if content_text:
                                usage = data.get("usage")
                                return AIResponse(
                                    text=content_text,
                                    provider=self.name,
                                    model=self.model,
                                    usage=usage
                                )
                raise ValueError("OpenRouter returned invalid or empty content response structure")

            # Extract headers safely
            retry_after_hdr = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            request_id = resp.headers.get("x-request-id") or resp.headers.get("X-Request-ID")
            ratelimit_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower().startswith("x-ratelimit-")
            }

            retry_after_sec = None
            if retry_after_hdr:
                try:
                    retry_after_sec = float(retry_after_hdr)
                except (ValueError, TypeError):
                    retry_after_sec = None

            # Handle 429 limited retry if Retry-After is specified and small
            if resp.status_code == 429 and retry_count < max_retries and retry_after_sec is not None and retry_after_sec <= 3.0:
                retry_count += 1
                logger.info(
                    f"OpenRouter 429 received with Retry-After={retry_after_sec}s. Backing off before retry {retry_count}/{max_retries}..."
                )
                await asyncio.sleep(retry_after_sec)
                continue

            # Parse full error response
            err_json: Dict[str, Any] = {}
            err_message = ""
            err_code = None
            metadata: Dict[str, Any] = {}
            provider_name = None
            raw_err = None

            try:
                parsed_body = resp.json()
                if isinstance(parsed_body, dict):
                    err_json = parsed_body
                    err_obj = parsed_body.get("error")
                    if isinstance(err_obj, dict):
                        err_message = err_obj.get("message", "") or str(err_obj)
                        err_code = err_obj.get("code")
                        if isinstance(err_obj.get("metadata"), dict):
                            metadata = err_obj.get("metadata", {})
                            provider_name = metadata.get("provider_name")
                            raw_err = metadata.get("raw")
                    elif isinstance(err_obj, str):
                        err_message = err_obj
                    else:
                        err_message = str(parsed_body)
            except Exception:
                err_message = resp.text[:200]

            status = resp.status_code
            is_upstream_429 = False
            if status == 429:
                # Distinguish upstream provider 429 vs account/key 429
                if provider_name or raw_err or "provider" in err_message.lower():
                    is_upstream_429 = True

            # Format descriptive error detail
            detail_components = []
            if err_message:
                detail_components.append(err_message)
            if provider_name:
                detail_components.append(f"upstream_provider='{provider_name}'")
            if metadata:
                detail_components.append(f"metadata={metadata}")
            if request_id:
                detail_components.append(f"request_id='{request_id}'")
            if retry_after_hdr:
                detail_components.append(f"retry_after='{retry_after_hdr}'")
            if ratelimit_headers:
                detail_components.append(f"ratelimit_headers={ratelimit_headers}")

            err_detail = " | ".join(detail_components) if detail_components else f"Status {status}"
            err_detail = _redact_secrets(err_detail, api_key)

            if status == 400:
                log_msg = f"OpenRouter API 400 Bad Request (model='{self.model}'): {err_detail}"
            elif status in (401, 403):
                log_msg = f"OpenRouter API {status} Authentication Error: {err_detail}"
            elif status == 404:
                log_msg = f"OpenRouter API 404 Model/Endpoint Not Found (model='{self.model}'): {err_detail}"
            elif status == 429:
                if is_upstream_429:
                    log_msg = (
                        f"OpenRouter API 429 Rate Limit Exceeded (Upstream Provider Error for model='{self.model}'): "
                        f"{err_detail} | full_json={err_json}"
                    )
                else:
                    log_msg = (
                        f"OpenRouter API 429 Rate Limit Exceeded (Account/Key Limit for model='{self.model}'): "
                        f"{err_detail} | full_json={err_json}"
                    )
            elif status in (500, 502, 503, 504):
                log_msg = f"OpenRouter API {status} Temporary Server Error: {err_detail}"
            else:
                log_msg = f"OpenRouter API {status} Error: {err_detail}"

            log_msg = _redact_secrets(log_msg, api_key)
            logger.warning(log_msg)

            raise httpx.HTTPStatusError(
                f"OpenRouter API Error Status {status}: {err_detail}",
                request=resp.request,
                response=resp
            )
