import logging
import re
from typing import Optional, Tuple
from telegram import Update, Message, User
from telegram.ext import ContextTypes
import config
from src.ai import (
    YUKI_PERSONA,
    ContextManager,
    AIRateLimiter,
    AIProviderManager,
    format_short_response,
    FALLBACK_YUKI_RESPONSE
)

logger = logging.getLogger(__name__)

# Initialize singletons for AI subsystems
context_manager = ContextManager(max_messages=config.AI_MAX_CONTEXT_MESSAGES)
rate_limiter = AIRateLimiter(
    user_cooldown=config.AI_USER_COOLDOWN,
    chat_cooldown=config.AI_CHAT_COOLDOWN,
    max_concurrent=config.AI_MAX_CONCURRENT_REQUESTS
)
provider_manager = AIProviderManager(
    provider_order=config.AI_PROVIDER_ORDER,
    gemini_keys=config.GEMINI_API_KEYS,
    groq_keys=config.GROQ_API_KEYS,
    openrouter_keys=config.OPENROUTER_API_KEYS
)


def should_trigger_yuki(update: Update, bot_username: Optional[str] = None, bot_id: Optional[int] = None) -> Tuple[bool, str]:
    """
    Determines if Yuki should respond to the incoming message.
    Returns (should_trigger, extracted_prompt)

    Triggers:
    1. Private chat message
    2. Directly mentioned via Telegram entity or @Username
    3. Reply to a message previously sent by Yuki
    """
    msg: Optional[Message] = update.effective_message
    if not msg:
        return False, ""

    txt = (msg.text or msg.caption or "").strip()
    if not txt:
        return False, ""

    chat = update.effective_chat
    if not chat:
        return False, ""

    # 1. Private Chat
    if chat.type == "private":
        return True, txt

    clean_bot_username = (bot_username or "").lstrip("@").lower()

    # 2. Telegram Mention / Entities Check
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                mention_text = txt[entity.offset:entity.offset + entity.length].lstrip("@").lower()
                if clean_bot_username and mention_text == clean_bot_username:
                    # Strip out bot mention from prompt
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

    bot_username = getattr(context.bot, "username", None)
    bot_id = getattr(context.bot, "id", None)

    if prompt_override:
        prompt = prompt_override.strip()
    else:
        triggered, prompt = should_trigger_yuki(update, bot_username=bot_username, bot_id=bot_id)
        if not triggered:
            return

    # Check Rate Limiting
    allowed, rejection_msg = rate_limiter.check_rate_limit(user.id, chat.id)
    if not allowed:
        if rejection_msg:
            try:
                await msg.reply_text(rejection_msg)
            except Exception:
                pass
        return

    rate_limiter.update_rate_limit(user.id, chat.id)

    # Acquire concurrency semaphore
    async with rate_limiter.semaphore:
        # Send typing action
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
            timeout=config.AI_REQUEST_TIMEOUT
        )

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
