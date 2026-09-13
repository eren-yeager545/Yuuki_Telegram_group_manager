import logging
import random
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
    Calculates exact UTF-16 offsets and lengths. Prevents duplicate entities at the same offset.
    Sorts entities by offset and validates offset/length range bounds.
    """
    entities = []
    if not text or not custom_emojis:
        return entities

    total_utf16_len = get_utf16_length(text)
    used_offsets = set()

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

            if utf16_offset not in used_offsets and (utf16_offset + utf16_len) <= total_utf16_len:
                entities.append(
                    MessageEntity(
                        type=MessageEntity.CUSTOM_EMOJI,
                        offset=utf16_offset,
                        length=utf16_len,
                        custom_emoji_id=cid_str
                    )
                )
                used_offsets.add(utf16_offset)

            start = idx + len(emoji_str)

    entities.sort(key=lambda e: e.offset)
    return entities


async def send_reply_with_custom_emoji(msg_obj, text: str, custom_emojis: list = None, auto_insert: bool = True, **kwargs):
    """
    Replies to a message with text and optional custom emoji entities.
    If custom_emojis is not provided, automatically looks up custom emojis from store.get_all_custom_emojis().
    Matches fallback Unicode emojis in text or reliably appends an available custom emoji when available.
    If Telegram rejects the message with custom emoji entities, it logs the custom emoji ID and error,
    then gracefully retries sending plain text with Unicode fallback emojis (without entities).
    Does NOT disable custom emojis in DB upon message send failure.
    """
    import store

    if custom_emojis is None:
        custom_emojis = []
        try:
            all_custom = store.get_all_custom_emojis()
            valid_custom = [
                item for item in (all_custom or [])
                if isinstance(item, dict) and str(item.get('emoji') or '').strip() and str(item.get('custom_emoji_id') or '').strip()
            ]

            if valid_custom:
                matching = [item for item in valid_custom if item.get('emoji') in text]
                if matching:
                    custom_emojis.extend(matching)

                if not custom_emojis and auto_insert:
                    chosen = random.choice(valid_custom)
                    emoji_char = str(chosen.get('emoji') or '').strip()
                    text = f"{text} {emoji_char}".strip()
                    custom_emojis.append(chosen)
        except Exception as e:
            logger.warning(f"Error resolving custom emojis from store: {e}")

    # Filter custom_emojis parameter to valid items only
    filtered_custom_emojis = []
    if custom_emojis:
        for item in custom_emojis:
            if isinstance(item, dict):
                e_char = str(item.get('emoji') or '').strip()
                cid_val = str(item.get('custom_emoji_id') or '').strip()
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                e_char = str(item[0] or '').strip()
                cid_val = str(item[1] or '').strip()
            else:
                e_char, cid_val = '', ''
            if e_char and cid_val:
                filtered_custom_emojis.append(item)

    entities = create_custom_emoji_entities(text, filtered_custom_emojis) if filtered_custom_emojis else []

    if entities:
        for ent in entities:
            logger.debug(f"Custom emoji selected: emoji=<placeholder> custom_emoji_id={getattr(ent, 'custom_emoji_id', '')}")
            logger.debug(f"Custom emoji entity: offset={ent.offset} length={ent.length}")
        logger.debug("Sending custom emoji message")

        existing_entities = kwargs.pop('entities', None)
        if existing_entities:
            existing_list = list(existing_entities)
            combined_entities = existing_list + [e for e in entities if not any(ex.offset == e.offset for ex in existing_list)]
            combined_entities.sort(key=lambda e: e.offset)
        else:
            combined_entities = entities

        try:
            return await msg_obj.reply_text(text, entities=combined_entities, **kwargs)
        except Exception as exc:
            cids = [getattr(e, 'custom_emoji_id', '') for e in entities if getattr(e, 'custom_emoji_id', None)]
            for cid in cids:
                logger.error(f"Failed to send custom emoji message: custom_emoji_id={cid} error={exc}")
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
