import datetime
import logging
from typing import Optional
from telegram import User, Chat
from telegram.ext import ContextTypes
import config
from store import (
    upsert_user, get_user_count, upsert_group, set_group_inactive,
    get_active_group_count, get_group_by_id, get_global_link
)

logger = logging.getLogger(__name__)

DIVIDER = "━━━━━━━━━━━━━━━━━━"


def format_utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def get_bot_name(context: ContextTypes.DEFAULT_TYPE) -> str:
    if context and context.bot:
        if getattr(context.bot, 'first_name', None):
            return context.bot.first_name
        if getattr(context.bot, 'username', None):
            return f"@{context.bot.username}"
    return "Yuuki Bot"


def format_logger_message(event_title: str, body: str, bot_name: str, date_time: str) -> str:
    return (
        f"{DIVIDER}\n"
        f"📢 {event_title}\n"
        f"{DIVIDER}\n"
        f"{body}\n"
        f"{DIVIDER}\n"
        f"🤖 Bot: {bot_name}\n"
        f"🕐 {date_time}\n"
        f"{DIVIDER}"
    )


async def send_logger_notification(context: ContextTypes.DEFAULT_TYPE, message_text: str):
    logger_target = config.LOG_CHANNEL_ID or get_global_link('logger_channel_id', '') or get_global_link('logger_link', '')
    if not logger_target:
        logger.info("Logger notification skipped: no LOG_CHANNEL_ID or logger link configured.")
        return

    try:
        target_id = logger_target
        if isinstance(target_id, str):
            target_id = target_id.strip()
            if target_id.startswith('-') and target_id[1:].isdigit():
                target_id = int(target_id)
            elif target_id.isdigit():
                target_id = int(target_id)

        await context.bot.send_message(chat_id=target_id, text=message_text, disable_web_page_preview=True)
    except Exception as e:
        logger.warning("Failed sending logger notification: %s", type(e).__name__)


async def get_group_link(chat: Chat, context: ContextTypes.DEFAULT_TYPE) -> str:
    username = getattr(chat, 'username', None)
    if username:
        return f"https://t.me/{username}"
    invite_link = getattr(chat, 'invite_link', None)
    if invite_link:
        return invite_link
    try:
        exported = await context.bot.export_chat_invite_link(chat.id)
        if exported:
            return exported
    except Exception:
        pass
    try:
        link_obj = await context.bot.create_chat_invite_link(chat.id)
        if link_obj and getattr(link_obj, 'invite_link', None):
            return link_obj.invite_link
    except Exception:
        pass
    return "Not Available"


async def get_member_count(chat: Chat, context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        cnt = await context.bot.get_chat_member_count(chat.id)
        return str(cnt)
    except Exception:
        return "Unknown"


async def log_user_started_event(user: User, context: ContextTypes.DEFAULT_TYPE):
    if not user:
        return
    upsert_user(user.id, user.full_name, user.username)
    total_users = get_user_count()
    now_str = format_utc_now()
    bot_name = get_bot_name(context)

    first_name = user.first_name or user.full_name or "User"
    username_str = f"@{user.username}" if user.username else '"Not Set"'

    body = (
        f'👤 User: "{first_name}"\n'
        f'🆔 User ID: "{user.id}"\n'
        f'🔗 Username: {username_str}\n'
        f'🕐 Started At: "{now_str}"\n\n'
        f'📊 Total Users: "{total_users}"'
    )

    msg = format_logger_message("NEW USER STARTED", body, bot_name, now_str)
    await send_logger_notification(context, msg)


async def log_group_added_event(chat: Chat, from_user: Optional[User], context: ContextTypes.DEFAULT_TYPE):
    if not chat:
        return

    existing = get_group_by_id(chat.id)
    if existing and existing.get('is_active') == 1 and existing.get('current_bot_status') in ('member', 'administrator'):
        # Already active, touch_seen without re-logging
        upsert_group(
            chat_id=chat.id,
            title=chat.title,
            username=chat.username,
            current_bot_status=existing.get('current_bot_status', 'member'),
            is_active=1
        )
        return

    group_name = chat.title or str(chat.id)
    group_link = await get_group_link(chat, context)
    member_count = await get_member_count(chat, context)
    added_by_id = from_user.id if from_user else None

    upsert_group(
        chat_id=chat.id,
        title=group_name,
        username=chat.username,
        group_link=group_link if group_link != "Not Available" else None,
        member_count=int(member_count) if member_count.isdigit() else None,
        added_by_user_id=added_by_id,
        current_bot_status="member",
        is_active=1
    )

    total_groups = get_active_group_count()
    now_str = format_utc_now()
    bot_name = get_bot_name(context)

    if from_user:
        fn = from_user.first_name or from_user.full_name or "User"
        un = f'@{from_user.username}' if from_user.username else '"Not Set"'
        added_by_block = (
            f'👤 Added By: "{fn}"\n'
            f'🆔 User ID: "{from_user.id}"\n'
            f'🔗 Username: {un}'
        )
    else:
        added_by_block = "👤 Added By: Unknown"

    link_display = f'"{group_link}"' if group_link != "Not Available" else "Not Available"
    members_display = f'"{member_count}"' if member_count != "Unknown" else "Unknown"

    body = (
        f'👥 Group: "{group_name}"\n'
        f'🔗 Group Link: {link_display}\n'
        f'🆔 Chat ID: "{chat.id}"\n\n'
        f'{added_by_block}\n\n'
        f'👥 Total Members: {members_display}\n'
        f'🕐 Added At: "{now_str}"\n\n'
        f'📊 Total Groups: "{total_groups}"'
    )

    msg = format_logger_message("NEW GROUP ADDED", body, bot_name, now_str)
    await send_logger_notification(context, msg)


async def log_group_removed_event(chat: Chat, from_user: Optional[User], context: ContextTypes.DEFAULT_TYPE, removal_status='kicked'):
    if not chat:
        return

    existing_group = get_group_by_id(chat.id)
    if existing_group and existing_group.get('is_active') == 0:
        # Already inactive
        return

    group_name = chat.title or (existing_group.get('title') if existing_group else None) or str(chat.id)

    group_link = "Not Available"
    if chat.username:
        group_link = f"https://t.me/{chat.username}"
    elif existing_group and existing_group.get('group_link'):
        group_link = existing_group.get('group_link')

    member_count = "Unknown"
    try:
        cnt = await context.bot.get_chat_member_count(chat.id)
        member_count = str(cnt)
    except Exception:
        if existing_group and existing_group.get('member_count'):
            member_count = str(existing_group.get('member_count'))

    set_group_inactive(
        chat_id=chat.id,
        current_bot_status=removal_status,
        member_count=int(member_count) if member_count.isdigit() else None
    )

    total_groups = get_active_group_count()
    now_str = format_utc_now()
    bot_name = get_bot_name(context)

    if from_user:
        fn = from_user.first_name or from_user.full_name or "User"
        un = f'@{from_user.username}' if from_user.username else '"Not Set"'
        action_by_block = (
            f'👤 Action By: "{fn}"\n'
            f'🆔 User ID: "{from_user.id}"\n'
            f'🔗 Username: {un}'
        )
    else:
        action_by_block = "👤 Action By: Unknown"

    link_display = f'"{group_link}"' if group_link != "Not Available" else "Not Available"
    members_display = f'"{member_count}"' if member_count != "Unknown" else "Unknown"

    body = (
        f'👥 Group: "{group_name}"\n'
        f'🔗 Group Link: {link_display}\n'
        f'🆔 Chat ID: "{chat.id}"\n\n'
        f'{action_by_block}\n\n'
        f'👥 Members Before Removal: {members_display}\n'
        f'🕐 Time: "{now_str}"\n\n'
        f'📊 Remaining Groups: "{total_groups}"'
    )

    msg = format_logger_message("BOT BANNED / REMOVED", body, bot_name, now_str)
    await send_logger_notification(context, msg)
