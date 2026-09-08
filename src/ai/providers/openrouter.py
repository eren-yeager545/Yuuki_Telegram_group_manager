import logging
import os
import httpx
from typing import List, Dict, Optional, Any
from .base import AIProvider, AIResponse

logger = logging.getLogger(__name__)


class OpenRouterProvider(AIProvider):
    name: str = "openrouter"

    def __init__(self, api_keys: List[str], model: Optional[str] = None):
        super().__init__(api_keys)
        self.model = (model or os.getenv("OPENROUTER_MODEL", "google/gemma-4-31b")).strip() or "google/gemma-4-31b"

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
            "temperature": 0.7
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

        async with httpx.AsyncClient(timeout=http_timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
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

            err_detail = ""
            try:
                err_json = resp.json()
                if isinstance(err_json, dict):
                    err_obj = err_json.get("error")
                    if isinstance(err_obj, dict):
                        err_detail = err_obj.get("message", "") or str(err_obj)
                    elif isinstance(err_obj, str):
                        err_detail = err_obj
                    else:
                        err_detail = str(err_json)
            except Exception:
                err_detail = resp.text[:200]

            if api_key and api_key in err_detail:
                err_detail = err_detail.replace(api_key, "[REDACTED]")

            status = resp.status_code
            if status == 400:
                log_msg = f"OpenRouter API 400 Bad Request (model='{self.model}'): {err_detail}"
            elif status in (401, 403):
                log_msg = f"OpenRouter API {status} Authentication Error: {err_detail}"
            elif status == 404:
                log_msg = f"OpenRouter API 404 Model/Endpoint Not Found (model='{self.model}'): {err_detail}"
            elif status == 429:
                log_msg = f"OpenRouter API 429 Rate Limit Exceeded: {err_detail}"
            elif status in (500, 502, 503, 504):
                log_msg = f"OpenRouter API {status} Temporary Server Error: {err_detail}"
            else:
                log_msg = f"OpenRouter API {status} Error: {err_detail}"

            logger.warning(log_msg)

            raise httpx.HTTPStatusError(
                f"OpenRouter API Error Status {status}: {err_detail or log_msg}",
                request=resp.request,
                response=resp
            )
