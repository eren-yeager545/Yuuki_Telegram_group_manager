from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional, Any


class ProviderErrorCode(str, Enum):
    BAD_REQUEST = "bad_request"
    AUTH_ERROR = "auth_error"
    NOT_FOUND = "not_found"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


@dataclass
class AIProviderError(Exception):
    message: str
    status_code: Optional[int] = None
    error_code: ProviderErrorCode = ProviderErrorCode.UNKNOWN
    suggested_cooldown: float = 300.0
    retry_after: Optional[float] = None
    is_upstream_error: bool = False
    upstream_provider: Optional[str] = None
    raw_error: Optional[Any] = None

    def __str__(self) -> str:
        return self.message


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
