import asyncio
import time
from typing import Dict, Tuple, Optional


class AIMessageDeduplicator:
    """
    Message-level deduplicator for Telegram AI processing.
    Ensures that a single Telegram update or message (chat_id, message_id) / update_id
    is processed at most once by AI handlers, with thread/async safety and TTL cleanup.
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl_seconds = ttl_seconds
        self._seen_messages: Dict[Tuple[int, int], float] = {}
        self._seen_updates: Dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def try_reserve(
        self,
        chat_id: int,
        message_id: int,
        update_id: Optional[int] = None
    ) -> bool:
        """
        Atomically checks and reserves a message/update for AI processing.
        Returns True if the request is new and successfully reserved.
        Returns False if the message/update was already processed or is currently in flight.
        """
        now = time.time()
        msg_key = (chat_id, message_id)

        async with self._lock:
            # Cleanup expired entries periodically
            cutoff = now - self.ttl_seconds

            expired_msg_keys = [k for k, ts in self._seen_messages.items() if ts < cutoff]
            for k in expired_msg_keys:
                del self._seen_messages[k]

            expired_upd_keys = [k for k, ts in self._seen_updates.items() if ts < cutoff]
            for k in expired_upd_keys:
                del self._seen_updates[k]

            if msg_key in self._seen_messages:
                return False

            if update_id is not None and update_id in self._seen_updates:
                return False

            self._seen_messages[msg_key] = now
            if update_id is not None:
                self._seen_updates[update_id] = now

            return True

    async def is_duplicate(
        self,
        chat_id: int,
        message_id: int,
        update_id: Optional[int] = None
    ) -> bool:
        """Read-only duplicate check (without reserving)."""
        now = time.time()
        msg_key = (chat_id, message_id)
        cutoff = now - self.ttl_seconds

        async with self._lock:
            if msg_key in self._seen_messages and self._seen_messages[msg_key] >= cutoff:
                return True
            if update_id is not None and update_id in self._seen_updates and self._seen_updates[update_id] >= cutoff:
                return True
            return False

    async def clear(self) -> None:
        """Clears all stored deduplication keys."""
        async with self._lock:
            self._seen_messages.clear()
            self._seen_updates.clear()
