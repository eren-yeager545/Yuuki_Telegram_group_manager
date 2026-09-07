import logging
import httpx
from typing import List, Dict, Optional, Any
from .base import AIProvider, AIResponse

logger = logging.getLogger(__name__)


class GeminiProvider(AIProvider):
    name: str = "gemini"

    def __init__(self, api_keys: List[str], model: str = "gemini-1.5-flash"):
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
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={api_key}"

        contents = []
        if system_prompt:
            contents.append({
                "role": "user",
                "parts": [{"text": f"System Directive: {system_prompt}"}]
            })
            contents.append({
                "role": "model",
                "parts": [{"text": "Understood desu~ 🌸"}]
            })

        for msg in messages:
            role = "model" if msg.get("role") in ("assistant", "model", "bot") else "user"
            content_text = msg.get("content", "")
            if msg.get("name") and role == "user":
                content_text = f"[{msg['name']}]: {content_text}"
            contents.append({
                "role": role,
                "parts": [{"text": content_text}]
            })

        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.7
            }
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    parts = candidates[0]["content"].get("parts", [])
                    if parts and "text" in parts[0]:
                        text = parts[0]["text"].strip()
                        usage = data.get("usageMetadata")
                        return AIResponse(text=text, provider=self.name, model=self.model, usage=usage)
                raise ValueError("Gemini returned invalid or empty content response structure")
            else:
                error_body = resp.text[:200]
                raise httpx.HTTPStatusError(
                    f"Gemini API Error Status {resp.status_code}: {error_body}",
                    request=resp.request,
                    response=resp
                )
