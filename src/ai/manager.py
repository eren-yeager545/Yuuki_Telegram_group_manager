import logging
import time
import uuid
from typing import List, Dict, Tuple, Optional, Callable, Any
from .providers.base import AIResponse, AIProviderError, ProviderErrorCode
from .providers.gemini import GeminiProvider
from .providers.groq import GroqProvider
from .providers.openrouter import OpenRouterProvider
from .health import AIHealthTracker
from .sanitizer import sanitize_text

logger = logging.getLogger(__name__)

FALLBACK_YUKI_RESPONSE = "U-um… my brain connection is taking a little break… 😭🌸\nTry again in a moment?"


class AIProviderManager:
    """
    Manages multiple AI providers and API keys with rotation, health cooldowns,
    intelligent error handling, fast failover, health tracking, owner alerts, and secure operational logging.
    """

    def __init__(
        self,
        provider_order: List[str],
        gemini_keys: List[str],
        groq_keys: List[str],
        openrouter_keys: List[str],
        key_cooldown_seconds: float = 300.0,
        gemini_model: Optional[str] = None,
        openrouter_model: Optional[str] = None,
        owner_notifier_callback: Optional[Callable[[str], Any]] = None
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

        # Centralized AI Health Tracker
        self.health_tracker = AIHealthTracker(owner_notifier_callback=owner_notifier_callback)
        for name, p in self.providers.items():
            m_name = getattr(p, "model", "unknown")
            self.health_tracker.register_provider(name, m_name)

    def set_owner_notifier(self, callback: Callable[[str], Any]) -> None:
        self.health_tracker.set_owner_notifier(callback)

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

    def _get_next_active_failover_provider(self, current_provider: str) -> Optional[str]:
        """Finds the next available healthy provider in provider_order for failover context."""
        found_current = False
        for p_name in self.provider_order:
            if p_name == current_provider:
                found_current = True
                continue
            if found_current:
                provider = self.providers.get(p_name)
                if provider and provider.api_keys:
                    for idx in range(len(provider.api_keys)):
                        if self._is_key_healthy(p_name, idx):
                            return p_name
        return None

    def _redact_all_keys(self, text: str) -> str:
        if not text:
            return text
        all_keys = []
        for provider in self.providers.values():
            all_keys.extend(provider.api_keys)
        return sanitize_text(text, extra_secrets=all_keys)

    def get_health_summary(self) -> str:
        return self.health_tracker.get_health_summary()

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
        timeout: float = 10.0,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None
    ) -> AIResponse:
        """
        Executes chat request with failover across active providers and key rotation.
        Includes unique correlation ID and secure diagnostic logging.
        """
        start_time = time.time()
        corr_id = uuid.uuid4().hex[:8]

        logger.info(
            f"AI Request Started | correlation_id={corr_id} chat_id={chat_id or 'none'} message_id={message_id or 'none'}"
        )

        has_configured_keys = any(bool(p.api_keys) for p in self.providers.values())
        if not has_configured_keys:
            logger.warning(f"AI Request Warning | correlation_id={corr_id}: No API keys configured for any provider")

        for provider_name in self.provider_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.api_keys:
                continue

            model_name = getattr(provider, "model", "unknown")

            for key_idx, api_key in enumerate(provider.api_keys):
                if not self._is_key_healthy(provider_name, key_idx):
                    continue

                try:
                    logger.info(
                        f"AI Provider Selected | provider={provider_name} key_index={key_idx} correlation_id={corr_id}"
                    )
                    response = await provider.generate_response_with_key(
                        api_key=api_key,
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        timeout=timeout
                    )
                    duration = round(time.time() - start_time, 3)
                    logger.info(
                        f"AI Response Completed | provider={provider_name} key_index={key_idx} correlation_id={corr_id} duration={duration}s"
                    )
                    self.health_tracker.record_success(provider_name, model_name)
                    return response

                except AIProviderError as pe:
                    err_msg = self._redact_all_keys(str(pe))
                    err_type = pe.error_code.value if pe.error_code else "unknown"
                    logger.warning(
                        f"AI Provider Failed | provider={provider_name} model={model_name} key_index={key_idx} correlation_id={corr_id} type={err_type} timeout={timeout}s: {err_msg}"
                    )
                    cooldown = pe.suggested_cooldown
                    if pe.retry_after and pe.retry_after > 0:
                        cooldown = pe.retry_after
                    self._mark_key_cooldown(provider_name, key_idx, cooldown)

                    failover_provider = self._get_next_active_failover_provider(provider_name)
                    self.health_tracker.record_failure(
                        provider_name=provider_name,
                        model_name=model_name,
                        error=pe,
                        cooldown_seconds=cooldown or self.default_cooldown_seconds,
                        active_failover_provider=failover_provider
                    )
                    if pe.error_code == ProviderErrorCode.TIMEOUT:
                        logger.info(
                            f"AI Provider Failover | correlation_id={corr_id}: Provider '{provider_name}' model='{model_name}' timed out after {timeout}s on key_index={key_idx}. Immediately failing over to target='{failover_provider or 'next key/provider'}'"
                        )
                    else:
                        logger.info(f"Provider Fallback | correlation_id={corr_id}: Rotating to next available key/provider")

                except Exception as e:
                    err_msg = self._redact_all_keys(str(e))
                    logger.warning(
                        f"AI Provider Failed | provider={provider_name} key_index={key_idx} correlation_id={corr_id} model={model_name} type=server_error: {type(e).__name__}: {err_msg}"
                    )
                    self._mark_key_cooldown(provider_name, key_idx, self.default_cooldown_seconds)

                    failover_provider = self._get_next_active_failover_provider(provider_name)
                    self.health_tracker.record_failure(
                        provider_name=provider_name,
                        model_name=model_name,
                        error=e,
                        cooldown_seconds=self.default_cooldown_seconds,
                        active_failover_provider=failover_provider
                    )
                    logger.info(f"Provider Fallback | correlation_id={corr_id}: Rotating to next available key/provider")

        duration = round(time.time() - start_time, 3)
        logger.error(
            f"AI Request Failed | correlation_id={corr_id} chat_id={chat_id or 'none'} message_id={message_id or 'none'} duration={duration}s: All configured AI providers/keys failed"
        )
        return AIResponse(
            text=FALLBACK_YUKI_RESPONSE,
            provider="fallback",
            model="none"
        )
