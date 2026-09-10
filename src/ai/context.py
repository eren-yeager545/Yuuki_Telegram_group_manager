import asyncio
import re
from collections import defaultdict, deque
from typing import List, Dict, Any, Optional


class ContextManager:
    """Manages short-term conversation context for group and private chats with per-chat async locking."""

    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages
        # Key: chat_id -> deque of message dicts: [{"role": "user"/"assistant", "content": "...", "name": "..."}]
        self._history = defaultdict(lambda: deque(maxlen=self.max_messages))
        self._chat_locks: Dict[int, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def get_chat_lock(self, chat_id: int) -> asyncio.Lock:
        """Retrieve or create a dedicated asyncio.Lock for a specific chat_id."""
        if chat_id not in self._chat_locks:
            async with self._global_lock:
                if chat_id not in self._chat_locks:
                    self._chat_locks[chat_id] = asyncio.Lock()
        return self._chat_locks[chat_id]

    def add_message(self, chat_id: int, role: str, content: str, user_name: Optional[str] = None) -> None:
        """Add a message to the chat history."""
        if not content or not content.strip():
            return
        msg = {
            "role": role,
            "content": content.strip()
        }
        if user_name:
            msg["name"] = user_name
        self._history[chat_id].append(msg)

    def get_context(self, chat_id: int) -> List[Dict[str, str]]:
        """Retrieve sliding window context messages for the specified chat."""
        return list(self._history[chat_id])

    def clear_context(self, chat_id: int) -> None:
        """Clear history for a specific chat."""
        if chat_id in self._history:
            self._history[chat_id].clear()


def format_short_response(text: str, max_words: int = 60) -> str:
    """
    Safely formats and shortens AI response if unnecessarily long,
    preserving complete sentence boundaries.
    """
    if not text:
        return ""

    cleaned = text.strip()
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned

    # Split into sentences using punctuation boundaries (. ! ? 〜 ~)
    sentences = re.split(r'(?<=[.!?〜~])\s+', cleaned)
    shortened_parts = []
    current_word_count = 0

    for sentence in sentences:
        s_words = sentence.split()
        if not s_words:
            continue
        if current_word_count + len(s_words) <= max_words or not shortened_parts:
            shortened_parts.append(sentence)
            current_word_count += len(s_words)
        else:
            break

    result = " ".join(shortened_parts).strip()
    return result if result else cleaned
