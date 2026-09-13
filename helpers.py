import logging
import random
import re
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


def _is_position_formatted_or_protected(text: str, char_idx: int) -> bool:
    """
    Returns True if char_idx in text is within code blocks, inline code, URLs,
    commands, or HTML tags where custom emoji entities shouldn't be placed.
    """
    if char_idx < 0 or char_idx >= len(text):
        return False

    # Check backtick code blocks / inline code
    # Count backticks before char_idx
    backticks_before = text[:char_idx].count('`')
    if backticks_before % 2 != 0:
        return True

    # Check HTML tags <code>...</code> or <pre>...</pre>
    open_code = text.rfind('<code>', 0, char_idx)
    close_code = text.rfind('</code>', 0, char_idx)
    if open_code > close_code:
        return True

    open_pre = text.rfind('<pre>', 0, char_idx)
    close_pre = text.rfind('</pre>', 0, char_idx)
    if open_pre > close_pre:
        return True

    # Check URL boundaries
    # Find all http:// or https:// or t.me/ links
    url_pattern = re.compile(r'https?://\S+|t\.me/\S+')
    for match in url_pattern.finditer(text):
        if match.start() <= char_idx < match.end():
            return True

    return False


def create_custom_emoji_entities(text: str, custom_emojis: list) -> list:
    """
    Creates MessageEntity objects for custom emojis in text.
    custom_emojis is a list of dicts: [{'emoji': '🌸', 'custom_emoji_id': '123456'}, ...]
    Calculates exact UTF-16 offsets and lengths. Prevents duplicate or overlapping entities.
    Sorts entities by offset.
    """
    entities = []
    if not text or not custom_emojis:
        return entities

    total_utf16_len = get_utf16_length(text)
    used_spans = []  # list of (utf16_start, utf16_end)

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

            # Skip if inside code/url
            if not _is_position_formatted_or_protected(text, idx):
                utf16_offset = get_utf16_length(text[:idx])
                utf16_len = get_utf16_length(emoji_str)
                end_offset = utf16_offset + utf16_len

                if end_offset <= total_utf16_len:
                    overlap = any(
                        not (end_offset <= s_start or utf16_offset >= s_end)
                        for s_start, s_end in used_spans
                    )
                    if not overlap:
                        entities.append(
                            MessageEntity(
                                type=MessageEntity.CUSTOM_EMOJI,
                                offset=utf16_offset,
                                length=utf16_len,
                                custom_emoji_id=cid_str
                            )
                        )
                        used_spans.append((utf16_offset, end_offset))

            start = idx + len(emoji_str)

    entities.sort(key=lambda e: e.offset)
    return entities


async def send_reply_with_custom_emoji(msg_obj, text: str, custom_emojis: list = None, auto_insert: bool = True, **kwargs):
    """
    Replies to a message with text and custom emoji entities.
    If custom_emojis is not provided, automatically looks up custom emojis from store.get_all_custom_emojis().
    Selects 1-3 distinct custom emojis naturally depending on message length.
    Calculates UTF-16 offsets and attaches MessageEntity.CUSTOM_EMOJI.
    If Telegram rejects the message with custom emoji entities, logs a fallback warning
    and gracefully retries sending plain text with Unicode fallback emojis.
    Does NOT disable custom emojis in DB upon message send failure.
    """
    import store

    filtered_custom_emojis = []

    if custom_emojis is None:
        custom_emojis = []
        try:
            all_custom = store.get_all_custom_emojis()
            valid_custom = [
                item for item in (all_custom or [])
                if isinstance(item, dict) and str(item.get('emoji') or '').strip() and str(item.get('custom_emoji_id') or '').strip()
            ]

            if valid_custom:
                # Find emojis that already exist in text outside code/urls
                matching = []
                for item in valid_custom:
                    e_char = str(item.get('emoji') or '').strip()
                    if e_char in text:
                        idx = text.find(e_char)
                        if not _is_position_formatted_or_protected(text, idx):
                            matching.append(item)

                if matching:
                    custom_emojis.extend(matching)

                if auto_insert:
                    # Decide target number of custom emojis based on text length (1 to 3)
                    word_count = len(text.split())
                    if word_count < 20:
                        target_count = 1
                    elif word_count < 60:
                        target_count = random.choice([1, 2])
                    else:
                        target_count = random.choice([2, 3])

                    needed = max(0, target_count - len(custom_emojis))
                    if needed > 0:
                        # Pick random distinct custom emojis not yet selected
                        used_cids = {str(item.get('custom_emoji_id')) for item in custom_emojis if item.get('custom_emoji_id')}
                        candidates = [item for item in valid_custom if str(item.get('custom_emoji_id')) not in used_cids]
                        if not candidates:
                            candidates = valid_custom

                        sample_size = min(needed, len(candidates))
                        if sample_size > 0:
                            chosen_items = random.sample(candidates, sample_size)
                            for chosen in chosen_items:
                                emoji_char = str(chosen.get('emoji') or '').strip()
                                # Append emoji to text if not already present
                                if emoji_char not in text:
                                    text = f"{text} {emoji_char}".strip()
                                custom_emojis.append(chosen)

        except Exception as e:
            logger.warning(f"Error resolving custom emojis from store: {e}")

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
                filtered_custom_emojis.append({'emoji': e_char, 'custom_emoji_id': cid_val})

    entities = create_custom_emoji_entities(text, filtered_custom_emojis) if filtered_custom_emojis else []

    if entities:
        logger.info("Custom emoji message sent")
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
            logger.warning("Custom emoji message fallback used")
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
