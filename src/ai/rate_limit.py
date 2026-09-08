import asyncio
import time
from typing import Dict, Tuple, Optional


class AIRateLimiter:
    """Handles per-user and per-chat cooldowns and max concurrency limits."""

    def __init__(self, user_cooldown: float = 5.0, chat_cooldown: float = 2.0, max_concurrent: int = 10):
        self.user_cooldown = user_cooldown
        self.chat_cooldown = chat_cooldown
        self.semaphore = asyncio.Semaphore(max_concurrent)

        self._last_user_time: Dict[int, float] = {}
        self._last_chat_time: Dict[int, float] = {}

    def check_rate_limit(self, user_id: int, chat_id: int) -> Tuple[bool, Optional[str]]:
        """
        Check if request is allowed under rate limiting rules.
        Returns (is_allowed, optional_rejection_message)
        """
        now = time.time()

        # Check user cooldown
        last_u = self._last_user_time.get(user_id, 0.0)
        if now - last_u < self.user_cooldown:
            return False, "Ehehe… one question at a time, please! 😳🌸"

        # Check chat cooldown
        last_c = self._last_chat_time.get(chat_id, 0.0)
        if now - last_c < self.chat_cooldown:
            return False, "U-um… please wait just a second! 🌸"

        return True, None

    def update_rate_limit(self, user_id: int, chat_id: int) -> None:
        """Update last active timestamp for user and chat."""
        now = time.time()
        self._last_user_time[user_id] = now
        self._last_chat_time[chat_id] = now
