from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional, Any

# Configurable default cooldown durations in seconds
DEFAULT_QUOTA_COOLDOWN = 3600.0          # 1 hour for quota exhaustion
DEFAULT_RATE_LIMIT_COOLDOWN = 300.0       # 5 minutes for rate limit
DEFAULT_AUTH_ERROR_COOLDOWN = 86400.0     # 24 hours for invalid/unauthorized keys
DEFAULT_SERVER_ERROR_COOLDOWN = 30.0      # 30 seconds for temporary 5xx
DEFAULT_MODEL_UNAVAILABLE_COOLDOWN = 1800.0  # 30 minutes for unavailable model
DEFAULT_TIMEOUT_COOLDOWN = 30.0           # 30 seconds for timeout / network error


class ProviderErrorCode(str, Enum):
    BAD_REQUEST = "bad_request"
    INVALID_REQUEST = "invalid_request"
    AUTH_ERROR = "auth_error"
    AUTHENTICATION_ERROR = "authentication_error"
    NOT_FOUND = "not_found"
    RATE_LIMIT = "rate_limit"
    QUOTA_EXCEEDED = "quota_exceeded"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROVIDER_ERROR = "provider_error"
    EMPTY_RESPONSE = "empty_response"
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
