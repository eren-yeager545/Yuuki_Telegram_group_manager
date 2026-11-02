import asyncio
import re
from collections import defaultdict, deque
from typing import List, Dict, Any, Optional, Tuple, Union


class ContextManager:
    """
    Manages short-term conversation context for group and private chats with async locking.
    Supports context separation by chat, user, and persona, while remaining backwards-compatible
    with legacy calls that use only chat_id.
    """

    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages
        # Key: context_key -> deque of message dicts: [{"role": "user"/"assistant", "content": "...", "name": "..."}]
        self._history = defaultdict(lambda: deque(maxlen=self.max_messages))
        self._chat_locks: Dict[Any, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _make_key(self, chat_id: Any, user_id: Optional[int] = None, persona: Optional[str] = None) -> Any:
        if user_id is not None or persona is not None:
            p_val = (persona or "yuki").strip().lower()
            u_val = user_id if user_id is not None else 0
            return (chat_id, u_val, p_val)
        return chat_id

    async def get_chat_lock(self, chat_id: Any, user_id: Optional[int] = None, persona: Optional[str] = None) -> asyncio.Lock:
        """Retrieve or create a dedicated asyncio.Lock for a specific chat or (chat, user, persona) context."""
        key = self._make_key(chat_id, user_id, persona)
        if key not in self._chat_locks:
            async with self._global_lock:
                if key not in self._chat_locks:
                    self._chat_locks[key] = asyncio.Lock()
        return self._chat_locks[key]

    def add_message(
        self,
        chat_id: Any,
        role: str,
        content: str,
        user_name: Optional[str] = None,
        user_id: Optional[int] = None,
        persona: Optional[str] = None
    ) -> None:
        """Add a message to the conversation history."""
        if not content or not content.strip():
            return
        msg = {
            "role": role,
            "content": content.strip()
        }
        if user_name:
            msg["name"] = user_name

        key = self._make_key(chat_id, user_id, persona)
        self._history[key].append(msg)
        # If separated key was used, also mirror to chat_id for backwards compatibility
        if key != chat_id:
            self._history[chat_id].append(msg)

    def get_context(
        self,
        chat_id: Any,
        user_id: Optional[int] = None,
        persona: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Retrieve sliding window context messages for the specified chat/user/persona."""
        key = self._make_key(chat_id, user_id, persona)
        return list(self._history[key])

    def clear_context(
        self,
        chat_id: Any,
        user_id: Optional[int] = None,
        persona: Optional[str] = None
    ) -> None:
        """Clear history for a specific chat/user/persona or chat."""
        key = self._make_key(chat_id, user_id, persona)
        if key in self._history:
            self._history[key].clear()
        if key != chat_id and chat_id in self._history:
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
