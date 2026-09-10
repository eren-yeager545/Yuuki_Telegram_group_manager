import logging
import random
import re
from typing import Optional, Tuple
from telegram import Update, Message, User
from telegram.ext import ContextTypes
import config
from store import get_all_stickers
from src.ai import (
    YUKI_PERSONA,
    ContextManager,
    AIRateLimiter,
    AIProviderManager,
    AIMessageDeduplicator,
    format_short_response,
    FALLBACK_YUKI_RESPONSE
)

logger = logging.getLogger(__name__)

# Initialize singletons for AI subsystems
context_manager = ContextManager(max_messages=config.AI_MAX_CONTEXT_MESSAGES)
deduplicator = AIMessageDeduplicator(ttl_seconds=300.0)
rate_limiter = AIRateLimiter(
    user_cooldown=config.AI_USER_COOLDOWN,
    chat_cooldown=config.AI_CHAT_COOLDOWN,
    max_concurrent=config.AI_MAX_CONCURRENT_REQUESTS
)
provider_manager = AIProviderManager(
    provider_order=config.AI_PROVIDER_ORDER,
    gemini_keys=config.GEMINI_API_KEYS,
    groq_keys=config.GROQ_API_KEYS,
    openrouter_keys=config.OPENROUTER_API_KEYS,
    gemini_model=config.GEMINI_MODEL,
    openrouter_model=config.OPENROUTER_MODEL
)


async def send_owner_ai_alert(message_text: str, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
    """
    Sends sanitized AI health notification alerts to the configured bot owner(s).
    """
    owner_ids = getattr(config, 'OWNER_IDS', [])
    if not owner_ids and getattr(config, 'OWNER_ID', None):
        owner_ids = [config.OWNER_ID]

    if context and hasattr(context, "bot") and context.bot:
        for oid in owner_ids:
            if oid:
                try:
                    await context.bot.send_message(chat_id=oid, text=message_text, disable_web_page_preview=True)
                except Exception as e:
                    logger.warning("Failed sending owner AI notification to %s: %s", oid, type(e).__name__)


def should_trigger_yuki(update: Update, bot_username: Optional[str] = None, bot_id: Optional[int] = None) -> Tuple[bool, str]:
    """
    Determines if Yuki should respond to the incoming message.
    Returns (should_trigger, extracted_prompt)

    Triggers:
    1. Private chat message
    2. Directly mentioned via Telegram entity or @Username
    3. Reply to a message previously sent by Yuki
    4. Sticker sent directly to Yuki (in PM or reply to Yuki in group)
    """
    msg: Optional[Message] = update.effective_message
    if not msg:
        return False, ""

    txt = (msg.text or msg.caption or "").strip()
    chat = update.effective_chat
    if not chat:
        return False, ""

    clean_bot_username = (bot_username or "").lstrip("@").lower()

    # Handle Sticker Trigger
    sticker_obj = msg.__dict__.get("sticker") if hasattr(msg, "__dict__") else getattr(msg, "sticker", None)
    if sticker_obj is None and hasattr(msg, "_spec_class") and not getattr(msg, "text", None):
        sticker_obj = getattr(msg, "sticker", None)

    if sticker_obj and type(sticker_obj).__name__ != "MagicMock":
        emoji = getattr(sticker_obj, "emoji", None) or "sticker"
        if chat.type == "private":
            return True, f"[User sent sticker: {emoji}]"
        if msg.reply_to_message and msg.reply_to_message.from_user:
            replied_user: User = msg.reply_to_message.from_user
            if bot_id and replied_user.id == bot_id:
                return True, f"[User sent sticker: {emoji}]"
            if clean_bot_username and replied_user.username and replied_user.username.lower() == clean_bot_username:
                return True, f"[User sent sticker: {emoji}]"
        return False, ""

    if not txt:
        return False, ""

    # 1. Private Chat
    if chat.type == "private":
        return True, txt

    # 2. Telegram Mention / Entities Check
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention_text = txt[entity.offset:entity.offset + entity.length].lstrip("@").lower()
                if clean_bot_username and mention_text == clean_bot_username:
                    prompt = re.sub(rf'@{clean_bot_username}\b', '', txt, flags=re.IGNORECASE).strip()
                    return True, prompt if prompt else txt
            elif entity.type == "text_mention" and entity.user:
                if bot_id and entity.user.id == bot_id:
                    prompt = txt[entity.offset + entity.length:].strip() or txt
                    return True, prompt

    # Text fallbacks for username
    if clean_bot_username and f"@{clean_bot_username}" in txt.lower():
        prompt = re.sub(rf'@{clean_bot_username}\b', '', txt, flags=re.IGNORECASE).strip()
        return True, prompt if prompt else txt

    # 3. Reply to Yuki's message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        replied_user: User = msg.reply_to_message.from_user
        if bot_id and replied_user.id == bot_id:
            return True, txt
        if clean_bot_username and replied_user.username and replied_user.username.lower() == clean_bot_username:
            return True, txt

    return False, ""


CUTE_FALLBACK_RESPONSES = [
    "E-ehh... my brain got a little sleepy just now 🥺💤",
    "Uwaa... I lost my train of thought! Give me a tiny moment~ 🥹✨",
    "Mmm... Yuki's brain is taking a little nap right now 😴🌸",
    "Ehehe... I wanted to answer, but my thoughts got tangled! 🫣💭",
    "Aahh~ my little brain connection wandered away again 🥺💫",
    "Yuki is thinking really hard... but the words aren't coming out yet~ 😭🌸"
]


def get_cute_fallback_response() -> str:
    """Returns a randomly selected cute fallback message when AI fails."""
    return random.choice(CUTE_FALLBACK_RESPONSES)


# In-memory LRU tracking for recent stickers sent per chat: chat_id -> list of file_ids
_recent_chat_stickers = {}


def get_random_sticker(user_emoji: Optional[str] = None, chat_id: Optional[int] = None) -> Optional[str]:
    """
    Randomly selects a sticker from stored sticker packs.
    If matching user_emoji is available in stored stickers, prioritizes those matching stickers.
    Avoids recently sent stickers in the same chat if possible.
    """
    stickers = get_all_stickers()
    if not stickers:
        return None

    candidates = stickers
    if user_emoji:
        matching = [s for s in stickers if s.get('emoji') and user_emoji in s.get('emoji')]
        if matching:
            candidates = matching

    file_ids = [s.get('file_id') for s in candidates if s.get('file_id')]
    if not file_ids:
        return None

    if len(file_ids) == 1:
        chosen = file_ids[0]
    else:
        recent = _recent_chat_stickers.get(chat_id, []) if chat_id else []
        fresh = [fid for fid in file_ids if fid not in recent]
        if fresh:
            chosen = random.choice(fresh)
        else:
            chosen = random.choice(file_ids)

    if chat_id:
        if chat_id not in _recent_chat_stickers:
            _recent_chat_stickers[chat_id] = []
        _recent_chat_stickers[chat_id].append(chosen)
        if len(_recent_chat_stickers[chat_id]) > 5:
            _recent_chat_stickers[chat_id].pop(0)

    return chosen


def find_matching_sticker(user_emoji: Optional[str], chat_id: Optional[int] = None) -> Optional[str]:
    return get_random_sticker(user_emoji=user_emoji, chat_id=chat_id)


async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt_override: Optional[str] = None):
    """
    Main Telegram handler for Yuki AI Chatbot responses.
    """
    if not config.AI_ENABLED:
        return

    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not msg or not chat or not user:
        return

    # Check Message / Update Deduplication atomically
    update_id = getattr(update, "update_id", None)
    reserved = await deduplicator.try_reserve(chat.id, msg.message_id, update_id=update_id)
    if not reserved:
        logger.debug(
            f"AI Message Deduplicated: Skipping duplicate processing for chat_id={chat.id} message_id={msg.message_id} update_id={update_id}"
        )
        return

    # Wire owner alert callback with current context
    provider_manager.set_owner_notifier(lambda text: send_owner_ai_alert(text, context))

    bot_obj = getattr(context, "bot", None)
    bot_username = getattr(bot_obj, "username", None)
    if not isinstance(bot_username, str):
        bot_username = "yuki_bot"

    bot_id = getattr(bot_obj, "id", None)
    if not isinstance(bot_id, int):
        bot_id = 123456

    if prompt_override:
        prompt = prompt_override.strip()
    else:
        triggered, prompt = should_trigger_yuki(update, bot_username=bot_username, bot_id=bot_id)
        if not triggered:
            return

    # Check Rate Limiting atomically
    allowed, rejection_msg = await rate_limiter.try_acquire(user.id, chat.id)
    if not allowed:
        if rejection_msg:
            try:
                await msg.reply_text(rejection_msg)
            except Exception:
                pass
        return

    # Acquire concurrency semaphore
    async with rate_limiter.semaphore:
        # Acquire per-chat lock to preserve context history order per chat
        chat_lock = await context_manager.get_chat_lock(chat.id)
        async with chat_lock:
            # If user sent a sticker, try replying with a sticker from saved packs
            sticker_obj = getattr(msg, "sticker", None)
            if sticker_obj and type(sticker_obj).__name__ != "MagicMock":
                user_emoji = getattr(sticker_obj, "emoji", None)
                saved_sticker_file_id = get_random_sticker(user_emoji=user_emoji, chat_id=chat.id)
                if saved_sticker_file_id:
                    try:
                        await msg.reply_sticker(sticker=saved_sticker_file_id)
                        return
                    except Exception as e:
                        logger.warning(f"Failed to reply with sticker: {e}")

            # Send typing action for text AI response
            try:
                await context.bot.send_chat_action(chat_id=chat.id, action="typing")
            except Exception:
                pass

            user_display = user.first_name or "Friend"
            context_manager.add_message(chat.id, role="user", content=prompt, user_name=user_display)
            conversation_history = context_manager.get_context(chat.id)

            # Call AI Provider Manager
            ai_resp = await provider_manager.chat(
                messages=conversation_history,
                system_prompt=YUKI_PERSONA,
                max_tokens=config.AI_MAX_OUTPUT_TOKENS,
                timeout=config.AI_REQUEST_TIMEOUT,
                chat_id=chat.id,
                message_id=msg.message_id
            )

            if ai_resp.provider == "fallback":
                final_text = get_cute_fallback_response()
            else:
                # Format/shorten response if casual chatting
                is_detailed_req = any(kw in prompt.lower() for kw in ["explain in detail", "detailed explanation", "essay", "full guide", "step by step"])
                final_text = ai_resp.text if is_detailed_req else format_short_response(ai_resp.text, max_words=50)

                if not final_text:
                    final_text = FALLBACK_YUKI_RESPONSE

            context_manager.add_message(chat.id, role="assistant", content=final_text)

            try:
                await msg.reply_text(final_text)
            except Exception as e:
                logger.error(f"Error sending AI response: {type(e).__name__}")

            # Optional randomized sticker response on Yuki interaction
            sticker_chance = getattr(config, "YUKI_STICKER_CHANCE", 0.25)
            if random.random() < sticker_chance:
                stk_file_id = get_random_sticker(chat_id=chat.id)
                if stk_file_id:
                    try:
                        await msg.reply_sticker(sticker=stk_file_id)
                    except Exception as e:
                        logger.warning(f"Failed to send random sticker: {e}")
