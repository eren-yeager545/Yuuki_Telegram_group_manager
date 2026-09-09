import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from src.ai.health import AIHealthTracker, ProviderHealthState
from src.ai.sanitizer import sanitize_text
from src.ai.manager import AIProviderManager
from src.ai.providers.base import AIProviderError, ProviderErrorCode, AIResponse
from src.ai.providers.gemini import GeminiProvider
from src.ai.providers.openrouter import OpenRouterProvider
import common


@pytest.mark.asyncio
async def test_secret_redaction_layer(monkeypatch):
    test_bot_token = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ123456789"
    test_gemini_key = "AIzaSyABC12345678901234567890123456789"
    test_sk_key = "sk-proj-123456789012345678901234"
    test_mongo_uri = "mongodb+srv://admin:supersecretpassword@cluster0.mongodb.net/yuukidb"

    monkeypatch.setattr(config, "BOT_TOKEN", test_bot_token)
    monkeypatch.setattr(config, "GEMINI_API_KEYS", [test_gemini_key])
    monkeypatch.setattr(config, "MONGO_URI", test_mongo_uri)

    raw_text = (
        f"Exception in bot with token {test_bot_token} and key {test_gemini_key}.\n"
        f"Authorization: Bearer {test_sk_key}\n"
        f"Connected to {test_mongo_uri} with query https://api.com/v1?api_key=my_secret_key_123"
    )

    sanitized = sanitize_text(raw_text)

    assert test_bot_token not in sanitized
    assert test_gemini_key not in sanitized
    assert test_sk_key not in sanitized
    assert "supersecretpassword" not in sanitized
    assert "my_secret_key_123" not in sanitized
    assert "[REDACTED]" in sanitized


@pytest.mark.asyncio
async def test_gemini_quota_exceeded_notification():
    sent_alerts = []

    def notifier(msg):
        sent_alerts.append(msg)

    tracker = AIHealthTracker(notification_cooldown=300, owner_notifier_callback=notifier)
    tracker.register_provider("gemini", "gemini-2.5-flash")

    err = AIProviderError(
        "Quota exceeded for Gemini API key AIzaSyABC12345678901234567890123456789",
        status_code=429,
        error_code=ProviderErrorCode.QUOTA_EXCEEDED
    )

    tracker.record_failure(
        provider_name="gemini",
        model_name="gemini-2.5-flash",
        error=err,
        cooldown_seconds=300,
        active_failover_provider="openrouter"
    )

    await asyncio.sleep(0.05)

    assert len(sent_alerts) == 1
    alert = sent_alerts[0]

    assert "🚨 AI Provider Error" in alert
    assert "Provider: Gemini" in alert
    assert "Model: gemini-2.5-flash" in alert
    assert "Type: Quota Exceeded" in alert
    assert "Status: 429" in alert
    assert "Action: Provider placed on cooldown" in alert
    assert "Failover: Enabled" in alert
    assert "AIzaSy" not in alert


@pytest.mark.asyncio
async def test_openrouter_429_notification():
    sent_alerts = []

    def notifier(msg):
        sent_alerts.append(msg)

    tracker = AIHealthTracker(notification_cooldown=300, owner_notifier_callback=notifier)
    tracker.register_provider("openrouter", "openrouter/free")

    err = AIProviderError(
        "OpenRouter Rate Limited (429)",
        status_code=429,
        error_code=ProviderErrorCode.RATE_LIMIT,
        is_upstream_error=True,
        upstream_provider="Anthropic"
    )

    tracker.record_failure(
        provider_name="openrouter",
        model_name="openrouter/free",
        error=err,
        cooldown_seconds=300,
        active_failover_provider="groq"
    )

    await asyncio.sleep(0.05)

    assert len(sent_alerts) == 1
    alert = sent_alerts[0]

    assert "🚨 AI Provider Error" in alert
    assert "Provider: Openrouter" in alert
    assert "Type: Rate Limited" in alert
    assert "Status: 429" in alert
    assert "Upstream: Anthropic" in alert
    assert "Action: Provider placed on cooldown" in alert


@pytest.mark.asyncio
async def test_provider_recovery_notification():
    sent_alerts = []

    def notifier(msg):
        sent_alerts.append(msg)

    tracker = AIHealthTracker(notification_cooldown=300, owner_notifier_callback=notifier)
    tracker.register_provider("openrouter", "openrouter/free")

    # First record failure
    err = AIProviderError("Temporarily unavailable", status_code=503, error_code=ProviderErrorCode.SERVER_ERROR)
    tracker.record_failure("openrouter", "openrouter/free", err, cooldown_seconds=300)
    await asyncio.sleep(0.05)

    assert len(sent_alerts) == 1
    assert "🚨 AI Provider Error" in sent_alerts[0]

    # Record success -> Trigger recovery
    tracker.record_success("openrouter", "openrouter/free")
    await asyncio.sleep(0.05)

    assert len(sent_alerts) == 2
    recovery_alert = sent_alerts[1]

    assert "✅ AI Provider Recovered" in recovery_alert
    assert "Provider: Openrouter" in recovery_alert
    assert "Model: openrouter/free" in recovery_alert
    assert "Status: Operational" in recovery_alert
    assert "Failover: Normal" in recovery_alert


@pytest.mark.asyncio
async def test_notification_deduplication_and_cooldown():
    sent_alerts = []

    def notifier(msg):
        sent_alerts.append(msg)

    tracker = AIHealthTracker(notification_cooldown=0.2, owner_notifier_callback=notifier)
    tracker.register_provider("gemini", "gemini-2.5-flash")

    err = AIProviderError("Quota exceeded", status_code=429, error_code=ProviderErrorCode.QUOTA_EXCEEDED)

    # 1. First failure -> triggers immediate alert
    tracker.record_failure("gemini", "gemini-2.5-flash", err, cooldown_seconds=300, active_failover_provider="openrouter")
    await asyncio.sleep(0.02)
    assert len(sent_alerts) == 1

    # 2. Immediate repeated failures (before notification cooldown) -> aggregated, no extra alert yet
    for _ in range(10):
        tracker.record_failure("gemini", "gemini-2.5-flash", err, cooldown_seconds=300, active_failover_provider="openrouter")

    await asyncio.sleep(0.02)
    assert len(sent_alerts) == 1  # Still 1 alert

    # 3. Wait for notification cooldown (0.2s) and record another failure -> triggers summary alert
    await asyncio.sleep(0.22)
    tracker.record_failure("gemini", "gemini-2.5-flash", err, cooldown_seconds=300, active_failover_provider="openrouter")
    await asyncio.sleep(0.05)

    assert len(sent_alerts) == 2
    summary_alert = sent_alerts[1]

    assert "⚠️ AI Provider Still Failing" in summary_alert
    assert "Provider: Gemini" in summary_alert
    assert "Failures since last notification: 11" in summary_alert or "Failures since last notification:" in summary_alert


@pytest.mark.asyncio
async def test_mybot_working_api_count_and_failed_provider_status():
    tracker = AIHealthTracker()
    tracker.register_provider("gemini", "gemini-2.5-flash")
    tracker.register_provider("openrouter", "openrouter/free")
    tracker.register_provider("groq", "llama-3.3-70b")

    # Gemini & Groq operational, OpenRouter on cooldown due to Rate limit
    tracker.record_success("gemini", "gemini-2.5-flash")
    tracker.record_success("groq", "llama-3.3-70b")

    err = AIProviderError("Upstream rate limit", status_code=429, error_code=ProviderErrorCode.RATE_LIMIT)
    tracker.record_failure("openrouter", "openrouter/free", err, cooldown_seconds=300)

    summary = tracker.get_health_summary()

    assert "🤖 <b>AI SYSTEM</b>" in summary
    assert "🟢 <b>Working:</b> 2" in summary
    assert "🟡 <b>Cooldown:</b> 1" in summary
    assert "<b>AI APIs:</b> 2/3 working" in summary
    assert "Active provider: Groq" in summary or "Active provider:" in summary
    assert "🔄 <b>Failover:</b> ENABLED" in summary
    assert "Openrouter" in summary
    assert "Status: Rate limited" in summary


@pytest.mark.asyncio
async def test_all_providers_failed_offline_status():
    tracker = AIHealthTracker()
    tracker.register_provider("gemini", "gemini-2.5-flash")
    tracker.register_provider("openrouter", "openrouter/free")

    err1 = AIProviderError("Quota exceeded", status_code=429, error_code=ProviderErrorCode.QUOTA_EXCEEDED)
    err2 = AIProviderError("Rate limit", status_code=429, error_code=ProviderErrorCode.RATE_LIMIT)

    tracker.record_failure("gemini", "gemini-2.5-flash", err1, cooldown_seconds=300)
    tracker.record_failure("openrouter", "openrouter/free", err2, cooldown_seconds=300)

    summary = tracker.get_health_summary()

    assert "🔴 <b>AI SYSTEM OFFLINE</b>" in summary
    assert "Working APIs: 0/2" in summary
    assert "All configured AI routes are currently unavailable." in summary


@pytest.mark.asyncio
async def test_mybot_command_owner_access(monkeypatch):
    manager = AIProviderManager(
        provider_order=["gemini", "openrouter"],
        gemini_keys=["AIzaSyTestKey12345678901234567890123"],
        groq_keys=[],
        openrouter_keys=["sk-or-v1-testkey12345678901234567890"],
        gemini_model="gemini-2.5-flash",
        openrouter_model="openrouter/free"
    )

    monkeypatch.setattr(common, "OWNER_IDS", [99999])
    monkeypatch.setattr(common, "SUDO_USERS", [])

    with patch("handlers.ai_chat.provider_manager", manager):
        # 1. Owner request
        update_owner = MagicMock()
        update_owner.effective_user.id = 99999
        update_owner.effective_message = AsyncMock()
        context_mock = MagicMock()
        context_mock.bot.first_name = "Yuki"

        await common.mybot_cmd(update_owner, context_mock)

        reply_text = update_owner.effective_message.reply_text.call_args[0][0]
        assert "🤖 <b>AI SYSTEM</b>" in reply_text
        assert "AIzaSy" not in reply_text
        assert "sk-or-v1" not in reply_text

        # 2. Non-owner request
        update_user = MagicMock()
        update_user.effective_user.id = 11111
        update_user.effective_message = AsyncMock()

        await common.mybot_cmd(update_user, context_mock)

        reply_user_text = update_user.effective_message.reply_text.call_args[0][0]
        assert "🤖 <b>AI SYSTEM</b>" not in reply_user_text

    await manager.close()
