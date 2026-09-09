import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
import config
from .providers.base import AIProviderError, ProviderErrorCode
from .sanitizer import sanitize_text

logger = logging.getLogger(__name__)


class ProviderHealthState(str, Enum):
    OPERATIONAL = "OPERATIONAL"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    AUTH_ERROR = "AUTH_ERROR"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class ProviderHealthMetrics:
    def __init__(self, name: str, model: str = "unknown"):
        self.name = name
        self.model = model
        self.state = ProviderHealthState.OPERATIONAL
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self.last_recovery_time: Optional[float] = None
        self.cooldown_until: float = 0.0
        self.failures_since_notification = 0
        self.last_notification_time: float = 0.0
        self.last_notified_state: Optional[ProviderHealthState] = None
        self.last_error_type: Optional[str] = None
        self.last_error_status: Optional[int] = None
        self.last_error_detail: Optional[str] = None
        self.last_upstream_provider: Optional[str] = None

    def is_healthy(self) -> bool:
        if self.state == ProviderHealthState.DISABLED:
            return False
        return time.time() >= self.cooldown_until


class AIHealthTracker:
    """
    Centralized health monitoring, statistics aggregation, secret redaction,
    notification deduplication, and status reporting for AI providers.
    """

    def __init__(
        self,
        notification_cooldown: Optional[float] = None,
        owner_notifier_callback: Optional[Callable[[str], Any]] = None
    ):
        self.providers: Dict[str, ProviderHealthMetrics] = {}
        self.notification_cooldown = (
            notification_cooldown
            if notification_cooldown is not None
            else getattr(config, "AI_NOTIFICATION_COOLDOWN", 300.0)
        )
        self.owner_notifier_callback = owner_notifier_callback
        self.active_provider_route: Optional[str] = None

    def register_provider(self, name: str, model: str = "unknown") -> None:
        if name not in self.providers:
            self.providers[name] = ProviderHealthMetrics(name=name, model=model)
        else:
            self.providers[name].model = model

    def set_owner_notifier(self, callback: Callable[[str], Any]) -> None:
        self.owner_notifier_callback = callback

    def _format_time_str(self, ts: Optional[float] = None) -> str:
        dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    async def _send_owner_alert(self, text: str) -> None:
        sanitized_msg = sanitize_text(text)
        if self.owner_notifier_callback:
            try:
                res = self.owner_notifier_callback(sanitized_msg)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning(f"Failed to send owner AI notification: {e}")

    def record_success(self, provider_name: str, model_name: Optional[str] = None) -> None:
        if provider_name not in self.providers:
            self.register_provider(provider_name, model_name or "unknown")

        p = self.providers[provider_name]
        if model_name:
            p.model = model_name

        p.total_requests += 1
        p.successful_requests += 1
        p.consecutive_failures = 0
        p.cooldown_until = 0.0
        self.active_provider_route = provider_name

        # Check for recovery state
        if p.state != ProviderHealthState.OPERATIONAL or (p.last_notified_state and p.last_notified_state != ProviderHealthState.OPERATIONAL):
            p.state = ProviderHealthState.OPERATIONAL
            p.last_recovery_time = time.time()
            p.last_notified_state = ProviderHealthState.OPERATIONAL
            p.failures_since_notification = 0

            # Trigger recovery alert
            recovery_msg = (
                "✅ AI Provider Recovered\n\n"
                f"Provider: {p.name.capitalize()}\n"
                f"Model: {p.model}\n"
                "Status: Operational\n"
                "Failover: Normal"
            )
            asyncio.create_task(self._send_owner_alert(recovery_msg))

    def record_failure(
        self,
        provider_name: str,
        model_name: Optional[str],
        error: Exception,
        cooldown_seconds: float = 300.0,
        active_failover_provider: Optional[str] = None
    ) -> None:
        if provider_name not in self.providers:
            self.register_provider(provider_name, model_name or "unknown")

        p = self.providers[provider_name]
        if model_name:
            p.model = model_name

        now = time.time()
        p.total_requests += 1
        p.failed_requests += 1
        p.consecutive_failures += 1
        p.last_failure_time = now
        p.cooldown_until = now + cooldown_seconds

        # Classify error
        err_code: Optional[ProviderErrorCode] = None
        status_code: Optional[int] = None
        is_upstream = False
        upstream_provider = None

        if isinstance(error, AIProviderError):
            err_code = error.error_code
            status_code = error.status_code
            is_upstream = error.is_upstream_error
            upstream_provider = error.upstream_provider
            raw_msg = str(error.message)
        else:
            raw_msg = str(error)

        sanitized_detail = sanitize_text(raw_msg)
        p.last_error_detail = sanitized_detail
        p.last_error_status = status_code
        p.last_upstream_provider = upstream_provider

        # Determine health state & human type
        if err_code == ProviderErrorCode.QUOTA_EXCEEDED:
            p.state = ProviderHealthState.QUOTA_EXHAUSTED
            err_type_str = "Quota Exceeded"
        elif err_code == ProviderErrorCode.RATE_LIMIT:
            p.state = ProviderHealthState.RATE_LIMITED
            err_type_str = "Rate Limited"
        elif err_code in (ProviderErrorCode.AUTH_ERROR, ProviderErrorCode.AUTHENTICATION_ERROR):
            p.state = ProviderHealthState.AUTH_ERROR
            err_type_str = "Authentication Error"
        elif err_code == ProviderErrorCode.MODEL_UNAVAILABLE:
            p.state = ProviderHealthState.TEMPORARILY_UNAVAILABLE
            err_type_str = "Model Unavailable"
        elif err_code == ProviderErrorCode.TIMEOUT:
            p.state = ProviderHealthState.TEMPORARILY_UNAVAILABLE
            err_type_str = "Timeout"
        elif err_code == ProviderErrorCode.NETWORK_ERROR:
            p.state = ProviderHealthState.TEMPORARILY_UNAVAILABLE
            err_type_str = "Network Error"
        elif err_code == ProviderErrorCode.EMPTY_RESPONSE:
            p.state = ProviderHealthState.TEMPORARILY_UNAVAILABLE
            err_type_str = "Malformed/Empty AI Response"
        elif err_code in (ProviderErrorCode.SERVER_ERROR, ProviderErrorCode.PROVIDER_ERROR):
            p.state = ProviderHealthState.TEMPORARILY_UNAVAILABLE
            err_type_str = "Server Error"
        else:
            p.state = ProviderHealthState.TEMPORARILY_UNAVAILABLE
            err_type_str = "Unexpected Provider Error"

        p.last_error_type = err_type_str

        p.failures_since_notification += 1

        # Check deduplication & alert criteria
        should_notify = False
        is_summary = False

        if p.last_notified_state != p.state:
            # State changed -> Immediate alert
            should_notify = True
        elif now - p.last_notification_time >= self.notification_cooldown:
            # Cooldown period passed -> Send update summary if failures continue
            should_notify = True
            is_summary = True

        if should_notify:
            p.last_notification_time = now
            p.last_notified_state = p.state

            time_str = self._format_time_str(now)
            action_str = "Provider placed on cooldown" if active_failover_provider else "Switching provider/model"
            failover_str = "Enabled"

            if is_summary:
                failover_active = active_failover_provider.capitalize() if active_failover_provider else "Active"
                alert_msg = (
                    "⚠️ AI Provider Still Failing\n\n"
                    f"Provider: {p.name.capitalize()}\n"
                    f"Type: {err_type_str}\n"
                    f"Failures since last notification: {p.failures_since_notification}\n"
                    "Status: On cooldown\n"
                    f"Failover: {failover_active} active"
                )
            else:
                upstream_line = f"Upstream: {upstream_provider or 'temporarily unavailable'}\n" if is_upstream or upstream_provider else ""
                status_display = str(status_code) if status_code else "500"
                alert_msg = (
                    "🚨 AI Provider Error\n\n"
                    f"Provider: {p.name.capitalize()}\n"
                    f"Model: {p.model}\n"
                    f"Type: {err_type_str}\n"
                    f"Status: {status_display}\n"
                    f"{upstream_line}"
                    f"Action: {action_str}\n"
                    f"Failover: {failover_str}\n\n"
                    f"Time: {time_str}"
                )

            p.failures_since_notification = 0
            asyncio.create_task(self._send_owner_alert(alert_msg))

    def get_health_summary(self) -> str:
        """
        Generates a clean HTML summary for /mybot command owner view.
        No credentials or internal key indices are shown.
        """
        total = len(self.providers)
        if total == 0:
            return "🤖 <b>AI SYSTEM</b>\n\nNo AI providers configured."

        working_providers = []
        cooldown_providers = []
        failed_providers = []

        for p in self.providers.values():
            if p.is_healthy():
                working_providers.append(p)
            elif p.state in (ProviderHealthState.RATE_LIMITED, ProviderHealthState.QUOTA_EXHAUSTED, ProviderHealthState.TEMPORARILY_UNAVAILABLE):
                cooldown_providers.append(p)
            else:
                failed_providers.append(p)

        working_count = len(working_providers)
        cooldown_count = len(cooldown_providers)
        failed_count = len(failed_providers)

        if working_count == 0:
            msg = (
                "🔴 <b>AI SYSTEM OFFLINE</b>\n\n"
                f"Working APIs: 0/{total}\n\n"
                "All configured AI routes are currently unavailable.\n"
                "Owner should already have received an automatic alert."
            )
            return sanitize_text(msg)

        lines = [
            "🤖 <b>AI SYSTEM</b>\n",
            f"🟢 <b>Working:</b> {working_count}",
            f"🟡 <b>Cooldown:</b> {cooldown_count}",
            f"🔴 <b>Failed:</b> {failed_count}\n",
            "<b>Providers:</b>\n"
        ]

        for p in self.providers.values():
            p_name = p.name.capitalize()
            if p.is_healthy():
                lines.append(f"🟢 {p_name} — Operational")
            elif p.state == ProviderHealthState.QUOTA_EXHAUSTED:
                lines.append(f"🟡 {p_name}\nStatus: Quota exhausted\nRetry: Cooldown active")
            elif p.state == ProviderHealthState.RATE_LIMITED:
                lines.append(f"🟡 {p_name}\nStatus: Rate limited\nRetry: Cooldown active")
            elif p.state == ProviderHealthState.AUTH_ERROR:
                lines.append(f"🔴 {p_name}\nStatus: Authentication error\nRetry: Disabled")
            else:
                reason = p.last_error_type or "Temporarily unavailable"
                lines.append(f"🟡 {p_name}\nStatus: Temporarily unavailable\nReason: {reason}")

        active_route = self.active_provider_route.capitalize() if self.active_provider_route else "None"
        backup_routes = max(0, working_count - 1)

        lines.extend([
            "",
            f"<b>AI APIs:</b> {working_count}/{total} working",
            f"<b>Active provider:</b> {active_route}",
            "🔄 <b>Failover:</b> ENABLED",
            f"<b>Backup routes available:</b> {backup_routes}"
        ])

        summary_text = "\n".join(lines)
        return sanitize_text(summary_text)
