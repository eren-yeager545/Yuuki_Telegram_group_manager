import logging
import uuid
from telegram import MessageEntity
from config import OWNER_IDS, SUDO_USERS

logger = logging.getLogger(__name__)


def is_owner(user_id, owner_ids=OWNER_IDS):
    return user_id in owner_ids


def is_owner_or_sudo(user_id, owner_ids=OWNER_IDS, sudo_users=SUDO_USERS):
    return user_id in owner_ids or user_id in sudo_users


async def is_admin(update, context):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ('administrator', 'creator') or is_owner_or_sudo(user.id)


async def safe_reply_error(message_obj, public_text='☁️ Oopsie! Something went a little wrong. Please try again in a moment~ 💗'):
    try:
        if hasattr(message_obj, 'reply_text'):
            await message_obj.reply_text(public_text)
        elif hasattr(message_obj, 'message') and hasattr(message_obj.message, 'reply_text'):
            await message_obj.message.reply_text(public_text)
    except Exception:
        pass


def get_utf16_length(text: str) -> int:
    """Returns the length of a string in UTF-16 code units."""
    if not text:
        return 0
    return len(text.encode('utf-16-le')) // 2


def create_custom_emoji_entities(text: str, custom_emojis: list) -> list:
    """
    Creates MessageEntity objects for custom emojis in text.
    custom_emojis is a list of dicts: [{'emoji': '🌸', 'custom_emoji_id': '123456'}, ...]
    Calculates exact UTF-16 offsets and lengths.
    """
    entities = []
    if not text or not custom_emojis:
        return entities

    for item in custom_emojis:
        if isinstance(item, dict):
            emoji_char = item.get('emoji')
            cid = item.get('custom_emoji_id')
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            emoji_char, cid = item[0], item[1]
        else:
            continue

        emoji_str = str(emoji_char or '').strip()
        cid_str = str(cid or '').strip()
        if not emoji_str or not cid_str:
            continue

        start = 0
        while True:
            idx = text.find(emoji_str, start)
            if idx == -1:
                break
            utf16_offset = get_utf16_length(text[:idx])
            utf16_len = get_utf16_length(emoji_str)
            entities.append(
                MessageEntity(
                    type=MessageEntity.CUSTOM_EMOJI,
                    offset=utf16_offset,
                    length=utf16_len,
                    custom_emoji_id=cid_str
                )
            )
            start = idx + len(emoji_str)

    return entities


async def send_reply_with_custom_emoji(msg_obj, text: str, custom_emojis: list = None, **kwargs):
    """
    Replies to a message with text and optional custom emoji entities.
    If Telegram rejects the message with custom emoji entities, it gracefully retries
    sending the plain text with Unicode fallback emojis (without entities).
    Does NOT disable custom emojis in DB upon message send failure.
    """
    entities = create_custom_emoji_entities(text, custom_emojis) if custom_emojis else None

    if entities:
        existing_entities = kwargs.pop('entities', None)
        combined_entities = (list(existing_entities) if existing_entities else []) + entities
        try:
            return await msg_obj.reply_text(text, entities=combined_entities, **kwargs)
        except Exception as exc:
            logger.warning(f"Failed to send message with custom emoji entities: {exc}. Falling back to plain text.")
            try:
                return await msg_obj.reply_text(text, **kwargs)
            except Exception as e:
                logger.error(f"Failed to send fallback plain text message: {e}")
                return None
    else:
        try:
            return await msg_obj.reply_text(text, **kwargs)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None
