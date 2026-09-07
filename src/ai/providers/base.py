from dataclasses import dataclass
from typing import List, Dict, Optional, Any


@dataclass
class AIResponse:
    text: str
    provider: str
    model: str
    usage: Optional[Dict[str, Any]] = None


class AIProvider:
    """Base interface for all AI providers."""

    name: str = "base"

    def __init__(self, api_keys: List[str]):
        self.api_keys = api_keys

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 150,
        timeout: float = 20.0,
        **kwargs
    ) -> AIResponse:
        raise NotImplementedError("Subclasses must implement generate_response")
