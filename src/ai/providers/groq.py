import logging
import httpx
from typing import List, Dict, Optional, Any
from .base import AIProvider, AIResponse

logger = logging.getLogger(__name__)


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
            content = msg.get("content", "")
            if msg.get("name") and role == "user":
                content = f"[{msg['name']}]: {content}"
            formatted_messages.append({"role": role, "content": content})

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

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices and "message" in choices[0]:
                    text = choices[0]["message"].get("content", "").strip()
                    usage = data.get("usage")
                    return AIResponse(text=text, provider=self.name, model=self.model, usage=usage)
                raise ValueError("Groq returned invalid choices structure")
            else:
                error_body = resp.text[:200]
                raise httpx.HTTPStatusError(
                    f"Groq API Error Status {resp.status_code}: {error_body}",
                    request=resp.request,
                    response=resp
                )
