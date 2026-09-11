import datetime
import html
import logging
import random
from telegram import Update, MessageEntity
from telegram.ext import ContextTypes
import store

logger = logging.getLogger(__name__)

DEFAULT_AFK_REASONS = [
    "probably fighting the sleep boss 😴",
    "went to touch grass 🌱",
    "temporarily disconnected from reality ✨",
    "busy pretending to be productive 📚",
    "recharging human battery 🔋",
    "somewhere between online and missing 👻",
    "AFK, AFK! 🌸",
    "wandering around in dreamland ☁️",
    "snacking on sweet treats 🍰",
]


def format_afk_duration(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} day" if days == 1 else f"{days} days")
    if hours > 0:
        parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
    if minutes > 0:
        parts.append(f"{minutes} minute" if minutes == 1 else f"{minutes} minutes")
    if not parts or (days == 0 and hours == 0 and minutes == 0):
        parts.append(f"{secs} second" if secs == 1 else f"{secs} seconds")

    return " ".join(parts)


async def afk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user or not chat:
        return

    # Determine reason
    reason = " ".join(context.args).strip() if context.args else ""
    if not reason:
        reason = random.choice(DEFAULT_AFK_REASONS)

    now_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    u_fn = user.full_name or user.first_name or "User"
    if not isinstance(u_fn, str):
        u_fn = str(u_fn)
    u_un = user.username if isinstance(user.username, str) else None

    try:
        store.upsert_user(user.id, u_fn, u_un, touch_seen=True, is_bot=bool(getattr(user, "is_bot", False)))
        store.set_afk(user.id, reason, now_utc)
    except Exception as e:
        logger.error(f"Failed to set AFK status in DB: {e}")

    user_name = html.escape(user.first_name if isinstance(user.first_name, str) else "User")
    resp_text = f"🌸 <b>{user_name}</b> is now AFK!\n💭 Reason: {html.escape(reason)}"
    resp_text = resp_text.replace("\\n", "\n")
    await msg.reply_text(
        resp_text,
        parse_mode="HTML"
    )


def extract_mentioned_afk_users(update: Update, chat_id: int = None):
    msg = update.effective_message
    if not msg:
        return []

    afk_users = []
    seen_uids = set()

    if msg and msg.from_user and hasattr(msg.from_user, "id"):
        uid = msg.from_user.id
        if isinstance(uid, int):
            seen_uids.add(uid)
    if update and getattr(update, "effective_user", None) and hasattr(update.effective_user, "id"):
        uid = update.effective_user.id
        if isinstance(uid, int):
            seen_uids.add(uid)

    # 1. Reply checking
    if msg.reply_to_message and msg.reply_to_message.from_user:
        replied_user = msg.reply_to_message.from_user
        if not getattr(replied_user, "is_bot", False) and replied_user.id not in seen_uids:
            try:
                afk_info = store.get_afk(replied_user.id)
                if afk_info:
                    seen_uids.add(replied_user.id)
                    r_fn = replied_user.first_name if isinstance(replied_user.first_name, str) else "User"
                    r_un = replied_user.username if isinstance(replied_user.username, str) else None
                    afk_users.append((replied_user.id, r_fn, r_un, afk_info[0], afk_info[1]))
            except Exception as e:
                logger.error(f"Error checking AFK for replied user: {e}")

    # 2. Entity mentions checking
    entities = (msg.entities or []) + (msg.caption_entities or [])
    txt = msg.text or msg.caption or ""

    for entity in entities:
        target_uid = None
        target_first_name = None
        target_username = None

        if entity.type == MessageEntity.TEXT_MENTION and entity.user:
            if not getattr(entity.user, "is_bot", False):
                target_uid = entity.user.id
                target_first_name = entity.user.first_name if isinstance(entity.user.first_name, str) else "User"
                target_username = entity.user.username if isinstance(entity.user.username, str) else None
                try:
                    store.upsert_user(target_uid, target_first_name, target_username, is_bot=bool(getattr(entity.user, "is_bot", False)))
                except Exception as e:
                    logger.error(f"Error saving user from text mention: {e}")
        elif entity.type == MessageEntity.MENTION:
            username = txt[entity.offset:entity.offset + entity.length].lstrip("@").strip()
            if username:
                user_row = store.get_user_by_username(username)
                if user_row:
                    target_uid, target_first_name, target_username = user_row[0], user_row[1], user_row[2]

        if target_uid and target_uid not in seen_uids:
            try:
                afk_info = store.get_afk(target_uid)
                if afk_info:
                    seen_uids.add(target_uid)
                    if not target_first_name:
                        user_by_id = store.get_user_by_id(target_uid)
                        if user_by_id:
                            target_first_name = user_by_id[1]
                            target_username = target_username or user_by_id[2]
                    afk_users.append((target_uid, target_first_name or "User", target_username, afk_info[0], afk_info[1]))
            except Exception as e:
                logger.error(f"Error checking AFK for entity mention: {e}")

    return afk_users
