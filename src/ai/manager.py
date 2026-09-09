import logging
import time
from typing import List, Dict, Tuple, Optional
from .providers.base import AIResponse, AIProviderError, ProviderErrorCode
from .providers.gemini import GeminiProvider
from .providers.groq import GroqProvider
from .providers.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)

FALLBACK_YUKI_RESPONSE = "U-um… my brain connection is taking a little break… 😭🌸\nTry again in a moment?"


class AIProviderManager:
    """
    Manages multiple AI providers and API keys with rotation, health cooldowns,
    intelligent error handling, fast failover, and secure operational logging.
    """

    def __init__(
        self,
        provider_order: List[str],
        gemini_keys: List[str],
        groq_keys: List[str],
        openrouter_keys: List[str],
        key_cooldown_seconds: float = 300.0,
        gemini_model: Optional[str] = None,
        openrouter_model: Optional[str] = None
    ):
        self.provider_order = provider_order
        self.default_cooldown_seconds = key_cooldown_seconds
        self.providers = {}

        if gemini_keys:
            self.providers["gemini"] = GeminiProvider(gemini_keys, model=gemini_model)
        if groq_keys:
            self.providers["groq"] = GroqProvider(groq_keys)
        if openrouter_keys:
            self.providers["openrouter"] = OpenRouterProvider(openrouter_keys, model=openrouter_model)

        # Key cooldown tracking: (provider_name, key_index) -> cooldown_until_timestamp
        self._key_cooldowns: Dict[Tuple[str, int], float] = {}

    def _is_key_healthy(self, provider_name: str, key_idx: int) -> bool:
        cooldown_until = self._key_cooldowns.get((provider_name, key_idx), 0.0)
        return time.time() >= cooldown_until

    def _mark_key_cooldown(self, provider_name: str, key_idx: int, cooldown_seconds: Optional[float] = None) -> None:
        duration = cooldown_seconds if cooldown_seconds is not None else self.default_cooldown_seconds
        if duration <= 0.0:
            return
        self._key_cooldowns[(provider_name, key_idx)] = time.time() + duration
        logger.info(
            f"AI Key Cooldown: Provider '{provider_name}' key index {key_idx} placed on temporary cooldown for {round(duration, 1)}s"
        )

    def _redact_all_keys(self, text: str) -> str:
        if not text:
            return text
        redacted = text
        for provider in self.providers.values():
            for k in provider.api_keys:
                if k and k in redacted:
                    redacted = redacted.replace(k, "[REDACTED]")
        return redacted

    async def close(self) -> None:
        """Close HTTP clients across all managed providers."""
        for provider in self.providers.values():
            if hasattr(provider, "close") and callable(provider.close):
                try:
                    await provider.close()
                except Exception as ce:
                    logger.warning(f"Error closing provider '{provider.name}': {ce}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 150,
        timeout: float = 20.0
    ) -> AIResponse:
        """
        Executes chat request with failover across active providers and key rotation.
        """
        start_time = time.time()
        logger.info("AI Request Started: Attempting generation across configured providers")

        has_configured_keys = any(bool(p.api_keys) for p in self.providers.values())
        if not has_configured_keys:
            logger.warning("AI Request Warning: No API keys are configured for any provider")

        for provider_name in self.provider_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.api_keys:
                continue

            model_name = getattr(provider, "model", "unknown")

            for key_idx, api_key in enumerate(provider.api_keys):
                if not self._is_key_healthy(provider_name, key_idx):
                    continue

                try:
                    logger.info(f"AI Provider Selected: Trying provider '{provider_name}' (key index {key_idx})")
                    response = await provider.generate_response_with_key(
                        api_key=api_key,
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        timeout=timeout
                    )
                    duration = round(time.time() - start_time, 3)
                    logger.info(f"AI Response Completed: Provider '{provider_name}' succeeded in {duration}s")
                    return response

                except AIProviderError as pe:
                    err_msg = self._redact_all_keys(str(pe))
                    err_type = pe.error_code.value if pe.error_code else "unknown"
                    logger.warning(
                        f"Provider Failure: provider={provider_name} key_index={key_idx} model={model_name} type={err_type}: {err_msg}"
                    )
                    cooldown = pe.suggested_cooldown
                    if pe.retry_after and pe.retry_after > 0:
                        cooldown = pe.retry_after
                    self._mark_key_cooldown(provider_name, key_idx, cooldown)
                    logger.info("Provider Fallback: Rotating to next available key/provider")

                except Exception as e:
                    err_msg = self._redact_all_keys(str(e))
                    logger.warning(
                        f"Provider Failure: provider={provider_name} key_index={key_idx} model={model_name} type=server_error: {type(e).__name__}: {err_msg}"
                    )
                    self._mark_key_cooldown(provider_name, key_idx, self.default_cooldown_seconds)
                    logger.info("Provider Fallback: Rotating to next available key/provider")

        duration = round(time.time() - start_time, 3)
        logger.error(f"AI Request Failed: All configured AI providers/keys failed after {duration}s")
        return AIResponse(
            text=FALLBACK_YUKI_RESPONSE,
            provider="fallback",
            model="none"
        )
