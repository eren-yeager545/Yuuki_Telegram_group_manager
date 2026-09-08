import logging
import os
import httpx
from typing import List, Dict, Optional, Any
from .base import AIProvider, AIResponse

logger = logging.getLogger(__name__)


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
            raise ValueError("Gemini request requires at least one non-empty message")

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

        http_timeout = httpx.Timeout(
            connect=10.0,
            read=60.0,
            write=10.0,
            pool=10.0
        )

        async with httpx.AsyncClient(timeout=http_timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.ReadTimeout as rt:
                logger.warning(f"Gemini API read timeout for model '{self.model}'")
                raise rt
            except httpx.ConnectTimeout as ct:
                logger.warning(f"Gemini API connect timeout for model '{self.model}'")
                raise ct
            except httpx.TimeoutException as te:
                logger.warning(f"Gemini API timeout for model '{self.model}': {type(te).__name__}")
                raise te
            except httpx.RequestError as re:
                logger.warning(f"Gemini API request error for model '{self.model}': {type(re).__name__}")
                raise re

            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    candidate = candidates[0]
                    finish_reason = candidate.get("finishReason")
                    if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"):
                        logger.warning(f"Gemini API generation blocked due to finishReason: {finish_reason}")
                        raise ValueError(f"Gemini response content blocked due to finishReason '{finish_reason}'")

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
                raise ValueError("Gemini returned invalid or empty content response structure")

            err_detail = ""
            try:
                err_json = resp.json()
                err_detail = err_json.get("error", {}).get("message", "")
            except Exception:
                err_detail = resp.text[:200]

            if api_key and api_key in err_detail:
                err_detail = err_detail.replace(api_key, "[REDACTED]")

            status = resp.status_code
            if status == 400:
                log_msg = f"Gemini API 400 Bad Request (model='{self.model}'): {err_detail}"
            elif status in (401, 403):
                log_msg = f"Gemini API {status} Authentication Error: {err_detail}"
            elif status == 404:
                log_msg = f"Gemini API 404 Model/Endpoint Not Found (model='{self.model}'): {err_detail}"
            elif status == 429:
                log_msg = f"Gemini API 429 Rate Limit Exceeded: {err_detail}"
            elif status >= 500:
                log_msg = f"Gemini API {status} Temporary Server Error: {err_detail}"
            else:
                log_msg = f"Gemini API {status} Error: {err_detail}"

            logger.warning(log_msg)

            raise httpx.HTTPStatusError(
                f"Gemini API Error Status {status}: {err_detail or log_msg}",
                request=resp.request,
                response=resp
            )
