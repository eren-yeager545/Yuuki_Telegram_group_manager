import asyncio
from telegram.error import RetryAfter
import logging
import html
import re
import time
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_IDS, OWNER_ID, SUDO_USERS
from store import (
    add_blacklist, add_fed_admin, add_quiz, add_report, add_temp_action, add_warn,
    clear_temp_action, create_federation, delete_filter, delete_note, delete_quiz,
    fed_ban_user, get_buttons, get_chat_federation, get_fed_ban, get_federation,
    get_note, get_quiz_count, get_recent_audit_logs, get_setting, get_warn_reasons,
    get_warns, is_fed_admin, leave_chat_federation, list_blacklists, list_federations_by_owner,
    list_notes, list_quizzes, list_warned_users, log_admin_action, remove_blacklist,
    reset_warns, save_buttons, save_filter, save_note, set_chat_federation, set_setting,
    unfed_ban_user, allow_report_event, report_exists_recent, get_group_quota_lines, MAX_LENGTHS,
    get_user_by_username, get_user_by_id, list_zombies, clean_zombies, get_user_messages, clear_user_messages, record_user_message, get_all_users, get_all_active_groups_detailed, get_group_members
)
from helpers import is_admin, is_owner, is_owner_or_sudo, safe_reply_error
logger = logging.getLogger(__name__)


async def is_target_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_owner_or_sudo(user_id):
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ('administrator', 'creator')
    except Exception:
        return False


def parse_duration_to_seconds(token: str):
    if not token:
        return None
    m = re.fullmatch(r'(\d+)([smhd])', token.lower())
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2)
    return value * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]


def parse_buttons_blob(blob: str):
    rows = []
    for chunk in [x.strip() for x in blob.split('||') if x.strip()]:
        if ' - ' not in chunk:
            continue
        label, url = [x.strip() for x in chunk.split(' - ', 1)]
        if label and url.startswith(('http://', 'https://')):
            rows.append((label[:32], url[:500]))
    return rows


def build_keyboard(rows):
    if not rows:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(text=label, url=url)] for label, url in rows])


async def admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type not in ('group', 'supergroup'):
        if update.message:
            await update.message.reply_text('Gomen ne~ 🌸 This command can only be used inside group or supergroup chats desu! (⁠⁠◕⁠‿⁠◕⁠✿⁠)')
        return False
    if not await is_admin(update, context):
        if update.message:
            await update.message.reply_text('Gomen ne~ 🌸 Only group admins can use this command desu! (⁠⁠◕⁠‿⁠◕⁠✿⁠)')
        return False
    return True


def reply_target(update: Update):
    return update.message.reply_to_message.from_user if update.message and update.message.reply_to_message else None


def reply_target_message(update: Update):
    return update.message.reply_to_message if update.message else None


def extract_reason(update: Update):
    if not update.message:
        return ''
    text = update.message.text or ''
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ''


def format_user_link(user_id: int, name: str) -> str:
    escaped_name = html.escape(name or 'User')
    return f'<a href="tg://user?id={user_id}">{escaped_name}</a>'


def format_user_tag(user_id: int, name: str, username: str = None) -> str:
    if username:
        clean_user = username.lstrip('@').strip()
        return f"@{clean_user}"
    return format_user_link(user_id, name)


class ResolvedUser:
    def __init__(self, uid, name, uname, is_bot=False):
        self.id = uid
        self.first_name = name or 'User'
        self.full_name = name or 'User'
        self.username = uname
        self.is_bot = is_bot


async def resolve_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update or not update.message:
        return None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        return update.message.reply_to_message.from_user

    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == 'text_mention' and getattr(entity, 'user', None):
                return entity.user

    if update.message.entities and update.message.text:
        for entity in update.message.entities:
            if entity.type == 'mention':
                raw_uname = update.message.text[entity.offset:entity.offset + entity.length].strip()
                u_info = get_user_by_username(raw_uname)
                if u_info:
                    return ResolvedUser(u_info[0], u_info[1], u_info[2])

    if context and context.args:
        first_arg = context.args[0].strip()
        if first_arg.startswith('@'):
            u_info = get_user_by_username(first_arg)
            if u_info:
                return ResolvedUser(u_info[0], u_info[1], u_info[2])
        elif first_arg.isdigit() or (first_arg.startswith('-') and first_arg[1:].isdigit()):
            uid = int(first_arg)
            u_info = get_user_by_id(uid)
            if u_info:
                return ResolvedUser(u_info[0], u_info[1], u_info[2])
            else:
                return ResolvedUser(uid, f'User {uid}', None)

    return None



async def apply_action(chat_id, target_id, action, context, duration_seconds=None):
    if action in ('none', 'off'):
        return 'none'
    if action == 'warn':
        return 'warn'
    if action == 'mute':
        await context.bot.restrict_chat_member(chat_id, target_id, permissions=ChatPermissions(can_send_messages=False))
        return 'mute'
    if action == 'kick':
        await context.bot.ban_chat_member(chat_id, target_id)
        await context.bot.unban_chat_member(chat_id, target_id)
        return 'kick'
    if action == 'ban':
        await context.bot.ban_chat_member(chat_id, target_id)
        return 'ban'
    if action == 'tmute' and duration_seconds:
        until = int(time.time()) + duration_seconds
        await context.bot.restrict_chat_member(chat_id, target_id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
        add_temp_action(chat_id, target_id, 'tmute', until)
        return f'tmute {duration_seconds}s'
    if action == 'tban' and duration_seconds:
        until = int(time.time()) + duration_seconds
        await context.bot.ban_chat_member(chat_id, target_id, until_date=until)
        add_temp_action(chat_id, target_id, 'tban', until)
        return f'tban {duration_seconds}s'
    return action


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("🌸 /ban command can only be used in group chats desu~ 💕")
        return

    if not await is_admin(update, context):
        await update.message.reply_text("Gomen ne~ 🌸 /ban command is only for admins desu! Let an admin handle it for you~ (⁠⁠◕⁠‿⁠◕⁠✿⁠)")
        return

    target = await resolve_target_user(update, context)
    if not target:
        await update.message.reply_text("🌸 Please reply to a message, tag (@username), or specify a valid user ID/username to ban desu~ 💕")
        return

    commander_id = user.id
    target_id = target.id

    if target_id == commander_id:
        await update.message.reply_text("😭 Nice try! You can't ban yourself. Pick another member, commander! 🫡")
        return

    bot_id = context.bot.id if hasattr(context, 'bot') and hasattr(context.bot, 'id') else None
    if (bot_id and target_id == bot_id) or (getattr(target, 'is_bot', False) and bot_id and target_id == bot_id):
        await update.message.reply_text("🥺 Aww, please don't ban me! I'm here to help you manage the group desu~ 💕")
        return

    try:
        target_member = await context.bot.get_chat_member(chat.id, target_id)
        target_status = getattr(target_member, 'status', None)
    except Exception:
        target_status = None

    if target_status in ('administrator', 'creator') or await is_target_admin(chat.id, target_id, context):
        await update.message.reply_text("""🛡️ Hey! This user is a group admin.
Please reply to, tag, or provide the user ID/username of the member you actually want to ban. 👀""")
        return

    if target_status in ('kicked', 'banned'):
        await update.message.reply_text("🌸 This user is already banned from the group desu! ✨")
        return

    try:
        await context.bot.ban_chat_member(chat.id, target_id)
        log_admin_action(chat.id, commander_id, 'ban', target_id)

        uname = getattr(target, 'username', None)
        if uname:
            target_disp = f"@{uname.lstrip('@')}"
        else:
            first_name = getattr(target, 'first_name', 'User')
            target_disp = format_user_link(target_id, first_name)

        confirm_msg = f"🔨 User banned successfully!\n👤 Target: {target_disp}\n🆔 ID: \"{target_id}\""
        await update.message.reply_html(confirm_msg)
    except Exception:
        await safe_reply_error(update.effective_message, "🌷 Aww, Telegram won't let me perform that action or ban this member. Please check that I have administrator permissions to ban users!")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("🌸 /unban command can only be used in group chats desu~ 💕")
        return

    if not await is_admin(update, context):
        await update.message.reply_text("Gomen ne~ 🌸 /unban command is only for admins desu!")
        return

    target = await resolve_target_user(update, context)
    if not target:
        await update.message.reply_text("🌸 Please reply to a message, tag (@username), or specify a valid user ID/username to unban desu~ 💕")
        return

    commander_id = user.id
    target_id = target.id

    uname = getattr(target, 'username', None)
    if uname:
        target_disp = f"@{uname.lstrip('@')}"
    else:
        first_name = getattr(target, 'first_name', 'User')
        target_disp = format_user_link(target_id, first_name)

    try:
        target_member = await context.bot.get_chat_member(chat.id, target_id)
        target_status = getattr(target_member, 'status', None)
    except Exception:
        target_status = None

    is_banned = target_status in ('kicked', 'banned')

    if is_banned:
        try:
            await context.bot.unban_chat_member(chat.id, target_id, only_if_banned=True)
            log_admin_action(chat.id, commander_id, 'unban', target_id)
            reply_text = f"🕊️ Welcome back!\n{target_disp} has been set free from the ban. ✨"
            await update.message.reply_html(reply_text)
        except Exception:
            await safe_reply_error(update.effective_message, "🌷 Aww, Telegram won't let me perform that action or unban this member. Please check my admin permissions!")
    else:
        reply_text = f"🕊️ Looks like this one is already free as a bird! 😂✨\n{target_disp} isn't banned."
        await update.message.reply_html(reply_text)



async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = await resolve_target_user(update, context)
    if not target or target.id == update.effective_user.id:
        await update.message.reply_text("🌸 Please reply to or specify a valid user to kick desu~")
        return

    chat = update.effective_chat
    if chat and chat.type in ('group', 'supergroup'):
        if await is_target_admin(chat.id, target.id, context):
            await update.message.reply_text("🥺 Ehehe… I can't kick an admin!")
            return

    target_tag = format_user_tag(target.id, getattr(target, 'first_name', 'User'), getattr(target, 'username', None))
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes", callback_data=f"kick_confirm:{target.id}:{update.effective_user.id}"),
            InlineKeyboardButton("No", callback_data=f"kick_cancel:{target.id}:{update.effective_user.id}")
        ]
    ])
    await update.message.reply_html(
        f"Are you sure you want to kick {target_tag}? 🌸",
        reply_markup=keyboard
    )


async def kick_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(('kick_confirm:', 'kick_cancel:')):
        return

    parts = query.data.split(':')
    action, target_id_str, admin_id_str = parts[0], parts[1], parts[2]
    target_id = int(target_id_str)
    admin_id = int(admin_id_str)

    if query.from_user.id != admin_id:
        await query.answer("Gomen ne~ Only the admin who issued this kick command can confirm it desu! 🌸", show_alert=True)
        return

    await query.answer()
    chat_id = query.message.chat_id

    if action == 'kick_cancel':
        await query.edit_message_text("Kick action canceled desu~ 🌸")
        return

    try:
        await context.bot.ban_chat_member(chat_id, target_id)
        await context.bot.unban_chat_member(chat_id, target_id)
        log_admin_action(chat_id, admin_id, 'kick', target_id)
        u_info = get_user_by_id(target_id)
        if u_info:
            target_tag = format_user_tag(u_info[0], u_info[1] or 'User', u_info[2])
        else:
            target_tag = format_user_link(target_id, f"User {target_id}")
        await query.edit_message_text(f"Kicked {target_tag} desu~ 🌸", parse_mode='HTML')
    except Exception:
        await query.edit_message_text("🌷 Aww, Telegram won't let me kick that member right now.")


async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if update.message.reply_to_message:
        try:
            await context.bot.delete_message(update.effective_chat.id, update.message.reply_to_message.message_id)
            await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
            log_admin_action(update.effective_chat.id, update.effective_user.id, 'del', update.message.reply_to_message.from_user.id)
        except Exception:
            await update.message.reply_text('I could not delete that message.')


async def dban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    chat_id = update.effective_chat.id
    target = await resolve_target_user(update, context)
    if not target:
        await update.message.reply_text("Reply to a user or specify a username/ID to ban and purge their messages~ 🌸 (⁠⁠◕⁠‿⁠◕⁠✿⁠)")
        return

    commander_id = update.effective_user.id
    target_id = target.id

    if target_id == commander_id:
        await update.message.reply_text("😭 Nice try! You can't ban yourself. Pick another member, commander! 🫡")
        return

    if await is_target_admin(chat_id, target_id, context):
        await update.message.reply_text("""🛡️ Hey! This user is a group admin.
Please reply to, tag, or provide the user ID/username of the member you actually want to ban. 👀""")
        return

    uname = getattr(target, 'username', None)
    if uname:
        user_tag = f"@{uname.lstrip('@')}"
    else:
        user_tag = format_user_link(target_id, getattr(target, 'first_name', 'User'))

    try:
        await context.bot.ban_chat_member(chat_id, target_id, revoke_messages=True)
    except Exception as e:
        await safe_reply_error(update.effective_message, "🌷 Aww, Telegram won't let me perform that action on this member.")
        return

    mids = clear_user_messages(chat_id, target_id)
    for mid in mids:
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass

    if update.message.reply_to_message:
        try:
            await context.bot.delete_message(chat_id, update.message.reply_to_message.message_id)
        except Exception:
            pass
    try:
        await context.bot.delete_message(chat_id, update.message.message_id)
    except Exception:
        pass

    log_admin_action(chat_id, commander_id, 'dban', target_id)
    await update.effective_chat.send_message(
        f"Banned and swept clean! 🔨🧹 {user_tag} has been banned and all their messages were purged from the group! Stay safe everyone~ 🌸 (⁠≧⁠∇⁠≦⁠)/",
        parse_mode='HTML'
    )



async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if update.message.reply_to_message:
        await context.bot.pin_chat_message(update.effective_chat.id, update.message.reply_to_message.message_id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'pin')
        await update.message.reply_text('Pinned.')


async def unpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    try:
        await context.bot.unpin_all_chat_messages(update.effective_chat.id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'unpin_all')
        await update.message.reply_text('All pinned messages cleared.')
    except Exception:
        await update.message.reply_text('I could not unpin messages.')


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = await resolve_target_user(update, context)
    if target:
        seconds = parse_duration_to_seconds(context.args[0]) if context.args else None
        if seconds:
            until = int(time.time()) + seconds
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
            add_temp_action(update.effective_chat.id, target.id, 'tmute', until)
            action_name = f'tmute {context.args[0]}'
        else:
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=ChatPermissions(can_send_messages=False))
            action_name = 'mute'
        log_admin_action(update.effective_chat.id, update.effective_user.id, action_name, target.id)
        await update.message.reply_html(f'Muted {format_user_tag(target.id, target.first_name, target.username)}.')


async def tmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    duration_str = None
    target = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
        if context.args:
            duration_str = context.args[0]
    elif context.args and len(context.args) >= 2:
        target = await resolve_target_user(update, context)
        duration_str = context.args[1]

    duration_sec = parse_duration_to_seconds(duration_str)
    if not target or not duration_sec:
        help_msg = (
            "❌ Please provide a valid duration.\n\n"
            "Examples:\n"
            "10m = 10 minutes\n"
            "2h = 2 hours\n"
            "1d = 1 day\n\n"
            "Usage:\n"
            "/tmute @username 10m\n"
            "or reply to a message: /tmute 30m"
        )
        await update.message.reply_text(help_msg)
        return

    try:
        until = int(time.time()) + duration_sec
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
        add_temp_action(update.effective_chat.id, target.id, 'tmute', until)
        log_admin_action(update.effective_chat.id, update.effective_user.id, f'tmute {duration_str}', target.id)
        tag = format_user_tag(target.id, getattr(target, 'first_name', 'User'), getattr(target, 'username', None))
        await update.message.reply_html(f'🔇 Muted {tag} for {duration_str}.')
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to mute user: {str(e)}")


async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = await resolve_target_user(update, context)
    if target:
        perms = ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_change_info=False, can_invite_users=True, can_pin_messages=False, can_manage_topics=False)
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=perms)
        clear_temp_action(update.effective_chat.id, target.id, 'tmute')
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'unmute', target.id)
        await update.message.reply_html(f'Unmuted {format_user_tag(target.id, target.first_name, target.username)}.')


async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return

    target = None
    reason = ""

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
        reason = (update.message.text or '').partition(' ')[2].strip()
    elif context.args:
        first_arg = context.args[0]
        if first_arg.startswith('@') or first_arg.isdigit():
            target = await resolve_target_user(update, context)
            reason = ' '.join(context.args[1:]).strip() if len(context.args) > 1 else ""

    if not target:
        await update.message.reply_text("Reply to a user's message or specify @username / user ID to warn them.")
        return

    if getattr(target, 'is_bot', False):
        await update.message.reply_text("Bots cannot be warned!")
        return

    # Check if target is admin/owner
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, target.id)
        if member.status in ('administrator', 'creator'):
            await update.message.reply_text("Administrators cannot be warned!")
            return
    except Exception:
        pass

    if not reason:
        reason = "🌸 A little warning for you! Please follow the group rules. 💕"

    count = add_warn(update.effective_chat.id, target.id, reason)
    limit = int(get_setting(update.effective_chat.id, 'warn_limit', '3'))
    action = get_setting(update.effective_chat.id, 'warn_mode', 'ban').lower()

    tag = format_user_tag(target.id, getattr(target, 'first_name', 'User'), getattr(target, 'username', None))
    log_admin_action(update.effective_chat.id, update.effective_user.id, 'warn', target.id, f'count={count};reason={reason}')

    msg_text = f"⚠️ Warning for {tag}!\nReason: {html.escape(reason)}\nWarn count: {count}/{limit}"
    await update.message.reply_html(msg_text)

    if count >= limit:
        try:
            duration_sec = parse_duration_to_seconds(get_setting(update.effective_chat.id, 'warn_time', '1h'))
            act_applied = await apply_action(update.effective_chat.id, target.id, action, context, duration_sec)
            action_disp = action.capitalize() if action not in ('none', 'off') else 'None'
            await update.message.reply_html(
                f"⚠️ Warning limit reached.\n\n"
                f"👤 User: {tag}\n"
                f"📌 Action: {html.escape(action_disp)}"
            )
            reset_warns(update.effective_chat.id, target.id)
            log_admin_action(update.effective_chat.id, update.effective_user.id, f'auto_{action}_warn_limit', target.id, f'count={count}')
        except Exception as e:
            await update.message.reply_text(f"Failed to execute warn action: {str(e)}")


async def warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_warned_users(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("⚠️ No warned users in this group.")
        return

    lines = ["⚠️ Warned Users", ""]
    for idx, row in enumerate(rows, start=1):
        uid = row[0]
        cnt = row[1]
        name = row[2] or f"User {uid}"
        username = row[3]
        tag = format_user_tag(uid, name, username)
        warn_word = "warn" if cnt == 1 else "warns"
        lines.append(f"{idx}. {tag} — {cnt} {warn_word}")

    await update.message.reply_html("\n".join(lines))


async def clearwarns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = await resolve_target_user(update, context)
    if target:
        reset_warns(update.effective_chat.id, target.id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'clearwarns', target.id)
        tag = format_user_tag(target.id, getattr(target, 'first_name', 'User'), getattr(target, 'username', None))
        await update.message.reply_html(f'Cleared warns for {tag}.')


async def warnlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text('Usage: /warnlimit 3')
        return
    set_setting(update.effective_chat.id, 'warn_limit', context.args[0])
    await update.message.reply_text(f'Warn limit set to {context.args[0]}.')


async def warnaction_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args or context.args[0].lower() not in ('none', 'mute', 'kick', 'ban', 'tmute', 'tban'):
        await update.message.reply_text('Usage: /warnaction none|mute|kick|ban|tmute [1h]')
        return
    action = context.args[0].lower()
    set_setting(update.effective_chat.id, 'warn_mode', action)
    if len(context.args) > 1:
        set_setting(update.effective_chat.id, 'warn_time', context.args[1])
    await update.message.reply_text(f'Warn action set to {action}.')


async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    text = (update.message.text or update.message.caption or '').partition(' ')[2].strip() if update.message else ''

    try:
        # Reply mode: /save <trigger_word>
        if update.message and update.message.reply_to_message:
            trigger_word = text.strip()
            if not trigger_word:
                await update.message.reply_text('🌸 Please specify a note name desu! Usage: reply to a message and send /save note_name')
                return

            reply_msg = update.message.reply_to_message
            note_type = 'text'
            note_content = ''

            if reply_msg.document:
                note_type = 'document'
                cap = reply_msg.caption or ''
                note_content = f"{reply_msg.document.file_id}||{cap}"
            elif reply_msg.sticker:
                note_type = 'sticker'
                note_content = reply_msg.sticker.file_id
            elif reply_msg.voice:
                note_type = 'voice'
                note_content = reply_msg.voice.file_id
            else:
                note_content = reply_msg.text or reply_msg.caption or ''

            # Extract inline keyboard buttons if present
            buttons_list = []
            if reply_msg.reply_markup and getattr(reply_msg.reply_markup, 'inline_keyboard', None):
                for row in reply_msg.reply_markup.inline_keyboard:
                    for btn in row:
                        if getattr(btn, 'url', None) and getattr(btn, 'text', None):
                            buttons_list.append(f"{btn.text} - {btn.url}")
            buttons_blob = ' || '.join(buttons_list)

            save_note(update.effective_chat.id, trigger_word, note_content, buttons_blob, note_type)
            await update.message.reply_text("💾✨ Saved successfully!\nYour note is safely tucked away~ 🌸")
            return

        # Standard syntax: /save name | content || Label - https://url
        if '|' not in text:
            await update.message.reply_text('🌸 Usage: /save name | content || Label - https://url OR reply to a message with /save note_name')
            return

        name, rest = [x.strip() for x in text.split('|', 1)]
        note_content, buttons_blob = rest, ''
        if '||' in rest:
            note_content, buttons_blob = [x.strip() for x in rest.split('||', 1)]
        save_note(update.effective_chat.id, name, note_content, buttons_blob, 'text')
        await update.message.reply_text("💾✨ Saved successfully!\nYour note is safely tucked away~ 🌸")
    except Exception:
        await update.message.reply_text("🥺 Aww, I couldn't save that note right now. Please try again! 💕")


async def send_note_reply(msg, content, buttons_blob, note_type='text'):
    reply_markup = build_keyboard(parse_buttons_blob(buttons_blob)) if buttons_blob else None
    if note_type == 'document':
        file_id = content
        caption = None
        if '||' in content:
            file_id, caption = content.split('||', 1)
        await msg.reply_document(document=file_id, caption=caption if caption else None, reply_markup=reply_markup)
    elif note_type == 'sticker':
        await msg.reply_sticker(sticker=content, reply_markup=reply_markup)
    elif note_type == 'voice':
        await msg.reply_voice(voice=content, reply_markup=reply_markup)
    else:
        await msg.reply_text(text=content, reply_markup=reply_markup)


async def getnote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not update.effective_chat or not update.message:
        return
    row = get_note(update.effective_chat.id, context.args[0])
    if not row:
        await update.message.reply_text("🌸 Aww, I couldn't find that note desu~ 💕")
        return
    content = row[0]
    buttons_blob = row[1] if len(row) > 1 else ''
    note_type = row[2] if len(row) > 2 else 'text'
    try:
        await send_note_reply(update.message, content, buttons_blob, note_type)
    except Exception:
        await update.message.reply_text("🥺 Aww, I couldn't send that note right now. Please try again! 💕")


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = [x[0] for x in list_notes(update.effective_chat.id)]
    await update.message.reply_text('Notes: ' + (', '.join(items) if items else 'None'))


async def clearnote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        return
    delete_note(update.effective_chat.id, context.args[0])
    await update.message.reply_text('Note deleted.')


async def filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    text = (update.message.text or update.message.caption or '').partition(' ')[2].strip() if update.message else ''

    if update.message and update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        keyword = text.strip()
        if not keyword:
            await update.message.reply_text('Usage: reply to a message and send /filter trigger_word')
            return

        filter_type = 'text'
        reply_content = ''

        if reply_msg.document:
            filter_type = 'document'
            cap = reply_msg.caption or ''
            reply_content = f"{reply_msg.document.file_id}||{cap}"
        elif reply_msg.sticker:
            filter_type = 'sticker'
            reply_content = reply_msg.sticker.file_id
        elif reply_msg.voice:
            filter_type = 'voice'
            reply_content = reply_msg.voice.file_id
        elif reply_msg.text and ('http://' in reply_msg.text or 'https://' in reply_msg.text or 't.me/' in reply_msg.text):
            filter_type = 'link'
            reply_content = reply_msg.text
        else:
            reply_content = reply_msg.text or reply_msg.caption or ''
            if 'http://' in reply_content or 'https://' in reply_content or 't.me/' in reply_content:
                filter_type = 'link'

        save_filter(update.effective_chat.id, keyword, reply_content, filter_type)
        await update.message.reply_text(f'Filter ({filter_type}) saved for "{keyword}".')
        return

    if not text or '|' not in text:
        await update.message.reply_text('Usage: /filter [text|sticker|voice|link|document] keyword | reply OR reply to a message with /filter trigger_word')
        return

    parts = [x.strip() for x in text.split('|', 1)]
    left_part, reply = parts[0], parts[1]

    left_words = left_part.split(maxsplit=1)
    filter_type = 'text'
    keyword = left_part

    if len(left_words) == 2 and left_words[0].lower() in ('text', 'sticker', 'voice', 'link', 'document', 'pdf'):
        filter_type = left_words[0].lower()
        if filter_type == 'pdf':
            filter_type = 'document'
        keyword = left_words[1]

    save_filter(update.effective_chat.id, keyword, reply, filter_type)
    await update.message.reply_text(f'Filter ({filter_type}) saved for "{keyword}".')


async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from store import get_filters
    all_filters = get_filters(update.effective_chat.id)
    if not all_filters:
        await update.message.reply_text('No active filters in this chat.')
        return

    categorized = {'text': [], 'sticker': [], 'voice': [], 'link': [], 'document': []}
    for kw, reply, f_type in all_filters:
        if f_type in categorized:
            categorized[f_type].append(kw)
        else:
            categorized['text'].append(kw)

    lines = ["📋 Active Filters", ""]
    type_labels = {
        'text': '📝 Text',
        'sticker': '🖼️ Sticker',
        'voice': '🎤 Voice',
        'link': '🔗 Link',
        'document': '📄 Document/PDF'
    }

    for f_type, label in type_labels.items():
        if categorized[f_type]:
            items_str = ", ".join(categorized[f_type])
            lines.append(f"{label}: {items_str}")

    await update.message.reply_text("\n".join(lines))


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        await update.message.reply_text('Usage: /stop keyword')
        return
    keyword = ' '.join(context.args).strip()
    deleted = delete_filter(update.effective_chat.id, keyword)
    if deleted:
        await update.message.reply_text(f'Filter "{keyword}" deleted.')
    else:
        await update.message.reply_text('Filter not found.')


async def lock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    valid = {'all','links','media','stickers','gifs','polls','forwards','bots','voice','video_notes','documents','photos','videos','audio','commands','hashtags','mentions','inline','contact'}
    if not context.args or context.args[0] not in valid:
        await update.message.reply_text('Usage: /lock ' + ', '.join(sorted(valid)))
        return
    set_setting(update.effective_chat.id, 'lock_' + context.args[0], 'on')
    await update.message.reply_text(f'Locked {context.args[0]}.')


async def unlock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        await update.message.reply_text('Usage: /unlock item')
        return
    set_setting(update.effective_chat.id, 'lock_' + context.args[0], 'off')
    await update.message.reply_text(f'Unlocked {context.args[0]}.')


async def locks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    valid = ['all','links','media','stickers','gifs','polls','forwards','bots','voice','video_notes','documents','photos','videos','audio','commands','hashtags','mentions','inline','contact']
    lines = [f'{item}: {get_setting(update.effective_chat.id, "lock_" + item, "off")}' for item in valid]
    await update.message.reply_text('Locks:\n' + '\n'.join(lines))


async def welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        state = get_setting(update.effective_chat.id, 'welcome', 'off')
        text = get_setting(update.effective_chat.id, 'welcome_text', 'Welcome {first}!')
        await update.message.reply_text(f'Welcome is {state}. Text: {text}')
        return
    mode = context.args[0].lower()
    if mode in ('on', 'off'):
        set_setting(update.effective_chat.id, 'welcome', mode)
        await update.message.reply_text(f'Welcome turned {mode}.')
        return
    if mode == 'text':
        text = update.message.text.split(' ', 2)[2] if len(update.message.text.split(' ', 2)) > 2 else ''
        set_setting(update.effective_chat.id, 'welcome_text', text)
        await update.message.reply_text('Welcome text updated.')
        return
    if mode == 'buttons':
        blob = update.message.text.split(' ', 2)[2] if len(update.message.text.split(' ', 2)) > 2 else ''
        rows = parse_buttons_blob(blob)
        save_buttons(update.effective_chat.id, 'welcome', rows)
        await update.message.reply_text('Welcome buttons updated.')
        return
    if mode == 'mute':
        set_setting(update.effective_chat.id, 'welcome_mute', context.args[1] if len(context.args) > 1 else 'off')
        await update.message.reply_text('Welcome mute mode updated.')
        return
    await update.message.reply_text('Usage: /welcome on|off|text ...|buttons ...|mute off|5m')


async def goodbye_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        return
    mode = context.args[0].lower()
    if mode in ('on', 'off'):
        set_setting(update.effective_chat.id, 'goodbye', mode)
        await update.message.reply_text(f'Goodbye turned {mode}.')
        return
    if mode == 'text':
        text = update.message.text.split(' ', 2)[2] if len(update.message.text.split(' ', 2)) > 2 else ''
        set_setting(update.effective_chat.id, 'goodbye_text', text)
        await update.message.reply_text('Goodbye text updated.')


async def flood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    limit = context.args[0] if context.args and context.args[0].isdigit() else '5'
    set_setting(update.effective_chat.id, 'flood_limit', limit)
    if len(context.args) > 1:
        set_setting(update.effective_chat.id, 'flood_action', context.args[1])
    if len(context.args) > 2:
        set_setting(update.effective_chat.id, 'flood_time', context.args[2])
    await update.message.reply_text(f'Flood limit set to {limit}. Action: {get_setting(update.effective_chat.id, "flood_action", "delete")}.')


async def setrules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    text = update.message.text.partition(' ')[2].strip()
    if not text:
        await update.message.reply_text('Usage: /setrules your rules text')
        return
    set_setting(update.effective_chat.id, 'rules_text', text)
    await update.message.reply_text('Rules updated.')


async def rulesbtn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    blob = update.message.text.partition(' ')[2].strip()
    rows = parse_buttons_blob(blob)
    save_buttons(update.effective_chat.id, 'rules', rows)
    await update.message.reply_text('Rules buttons updated.')


def format_relative_time(created_at: int) -> str:
    diff = int(time.time()) - created_at
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


async def modlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("❌ This command can only be used inside a group.")
        return
    if not await is_admin(update, context):
        await update.message.reply_text("🚫 You don't have permission to view the moderation log.")
        return

    rows = get_recent_audit_logs(chat.id, 20)
    if not rows:
        await update.message.reply_text("📋 Moderation Log\n\nNo recent logs.")
        return

    lines = ["📋 Moderation Log", ""]
    action_emojis = {
        'ban': '🚫 BAN',
        'unban': '✅ UNBAN',
        'kick': '👞 KICK',
        'mute': '🔇 MUTE',
        'unmute': '🔊 UNMUTE',
        'warn': '⚠️ WARN',
        'clearwarns': '🧹 CLEAR WARNS',
        'purge': '🗑 PURGE',
        'report': '🚨 REPORT',
        'pin': '📌 PIN'
    }

    for actor_id, action, target_id, details, created_at in rows:
        action_upper = action.split()[0].lower() if action else ''
        header = action_emojis.get(action_upper, f"⚡ {action.upper()}")

        # User display
        if target_id:
            u_info = get_user_by_id(target_id)
            if u_info:
                user_str = format_user_tag(u_info[0], u_info[1] or 'User', u_info[2])
            else:
                user_str = format_user_link(target_id, f"User {target_id}")
        else:
            actor_info = get_user_by_id(actor_id)
            if actor_info:
                user_str = format_user_tag(actor_info[0], actor_info[1] or 'Admin', actor_info[2])
            else:
                user_str = format_user_link(actor_id, f"Admin {actor_id}")

        rel_time = format_relative_time(created_at)
        lines.append(f"{header}")
        lines.append(f"👤 {user_str}")
        if details:
            lines.append(f"📝 {html.escape(details)}")
        lines.append(f"🕐 {rel_time}")
        lines.append("")

    await update.message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def groupquota_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    lines = get_group_quota_lines()
    await update.message.reply_text('GROUP QUOTA\n' + '\n'.join(lines))


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not reply_target_message(update):
        await update.message.reply_text("Reply to a user message to report it to admins.")
        return
    target_msg = reply_target_message(update)
    if not target_msg or not target_msg.from_user:
        await update.message.reply_text('Reply to a valid user message to report it.')
        return
    target_user = target_msg.from_user
    reason = extract_reason(update)[:MAX_LENGTHS['report_reason']]
    if not reason:
        reason = "No reason provided"

    if report_exists_recent(update.effective_chat.id, update.effective_user.id, target_msg.message_id, 60):
        await update.message.reply_text('You already reported this message recently.')
        return
    if not allow_report_event(update.effective_chat.id, update.effective_user.id, target_user.id, 60, 2):
        await update.message.reply_text('Rate limit reached: you can report this user only 2 times per minute.')
        return

    add_report(update.effective_chat.id, update.effective_user.id, target_user.id, target_msg.message_id, reason)

    # Send notification text
    reported_user_str = format_user_tag(target_user.id, target_user.first_name, target_user.username)
    admin_user_str = format_user_tag(update.effective_user.id, update.effective_user.first_name, update.effective_user.username)

    report_notice = (
        "🚨 New Report\n\n"
        f"👤 Reported User:\n{reported_user_str}\n\n"
        f"👮 Reporter:\n{admin_user_str}\n\n"
        f"📝 Reason:\n{html.escape(reason)}"
    )

    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
        admin_mentions = ' '.join([format_user_link(a.user.id, a.user.first_name) for a in admins if not a.user.is_bot][:5])
        await update.message.reply_html(f"{report_notice}\n\n📢 Admins notified: {admin_mentions}", disable_web_page_preview=True)
    except Exception as e:
        await update.message.reply_html(report_notice, disable_web_page_preview=True)

    log_admin_action(update.effective_chat.id, update.effective_user.id, 'report', target_user.id, reason)


async def reports_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args or context.args[0] not in ('on', 'off'):
        await update.message.reply_text('Usage: /reports on|off')
        return
    set_setting(update.effective_chat.id, 'reports', context.args[0])
    await update.message.reply_text(f'Reports turned {context.args[0]}.')


async def blacklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        await update.message.reply_text('Usage: /blacklist trigger | delete|warn|mute|kick|ban')
        return
    payload = update.message.text.partition(' ')[2]
    if '|' in payload:
        trigger, action = [x.strip() for x in payload.split('|', 1)]
    else:
        trigger, action = payload.strip(), 'delete'
    add_blacklist(update.effective_chat.id, trigger, action)
    await update.message.reply_text(f'Blacklisted: {trigger} ({action}).')


async def rmblacklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        await update.message.reply_text('Usage: /rmblacklist trigger')
        return
    trigger = update.message.text.partition(' ')[2].strip()
    remove_blacklist(update.effective_chat.id, trigger)
    await update.message.reply_text('Blacklist removed.')


async def blacklists_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_blacklists(update.effective_chat.id)
    if not rows:
        await update.message.reply_text('No blacklists set.')
        return
    await update.message.reply_text('Blacklists:\n' + '\n'.join([f'{t} -> {a}' for t, a in rows[:100]]))


async def newfed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id if update.effective_user else 0, OWNER_IDS):
        await update.message.reply_text('Trusted users only.')
        return
    if not context.args:
        await update.message.reply_text('Usage: /newfed shortid Federation Name')
        return
    fed_id = context.args[0]
    fed_name = ' '.join(context.args[1:]).strip() or fed_id
    try:
        create_federation(fed_id, fed_name, update.effective_user.id)
        await update.message.reply_text(f'Federation created: {fed_name} ({fed_id})')
    except Exception:
        await update.message.reply_text('Could not create federation. The fed id may already exist.')


async def myfeds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_federations_by_owner(update.effective_user.id)
    if not rows:
        await update.message.reply_text('You do not own any federations yet.')
        return
    await update.message.reply_text('Your federations:\n' + '\n'.join([f'{fed_id} - {fed_name}' for fed_id, fed_name in rows]))


async def joinfed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        await update.message.reply_text('Usage: /joinfed fed_id')
        return
    fed = get_federation(context.args[0])
    if not fed:
        await update.message.reply_text('Federation not found.')
        return
    if fed[2] != update.effective_user.id and not is_fed_admin(context.args[0], update.effective_user.id):
        await update.message.reply_text('Only the federation owner or a fed admin can join chats to this fed.')
        return
    set_chat_federation(update.effective_chat.id, context.args[0], update.effective_user.id)
    await update.message.reply_text(f'This chat joined federation {context.args[0]}.')


async def leavefed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    leave_chat_federation(update.effective_chat.id)
    await update.message.reply_text('This chat left its federation.')


async def fedinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_chat_federation(update.effective_chat.id)
    if not row:
        await update.message.reply_text('This chat is not in any federation.')
        return
    fed = get_federation(row[0])
    await update.message.reply_text(f'Federation: {fed[1]} ({fed[0]}) owner: {fed[2]}')


async def fedadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Usage: /fedadmin fed_id user_id')
        return
    fed_id = context.args[0]
    fed = get_federation(fed_id)
    if not fed or fed[2] != update.effective_user.id:
        await update.message.reply_text('Only the federation owner can add fed admins.')
        return
    uid = int(context.args[1])
    add_fed_admin(fed_id, uid)
    await update.message.reply_text('Fed admin added.')


async def fedban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_chat_federation(update.effective_chat.id)
    if not row:
        await update.message.reply_text('This chat is not in any federation.')
        return
    fed_id = row[0]
    if not is_fed_admin(fed_id, update.effective_user.id):
        await update.message.reply_text('Only fed admins can fedban.')
        return
    target = await resolve_target_user(update, context)
    if not target:
        await update.message.reply_text('Reply to a user to fedban them.')
        return
    reason = extract_reason(update)
    fed_ban_user(fed_id, target.id, reason, update.effective_user.id)
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
    except Exception:
        pass
    await update.message.reply_text(f'Fedbanned {getattr(target, "full_name", target.id)}.')


async def unfedban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_chat_federation(update.effective_chat.id)
    if not row:
        await update.message.reply_text('This chat is not in any federation.')
        return
    fed_id = row[0]
    if not is_fed_admin(fed_id, update.effective_user.id):
        await update.message.reply_text('Only fed admins can unfedban.')
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text('Usage: /unfedban user_id')
        return
    uid = int(context.args[0])
    unfed_ban_user(fed_id, uid)
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, uid, only_if_banned=True)
    except Exception:
        pass
    await update.message.reply_text('Federation ban removed.')


async def fedstat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = get_chat_federation(update.effective_chat.id)
    if not row:
        await update.message.reply_text('This chat is not in any federation.')
        return
    fed = get_federation(row[0])
    await update.message.reply_text(f'Fed id: {fed[0]}\nFed name: {fed[1]}\nOwner: {fed[2]}')


async def addquiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text('Only my owner or sudo users can add quizzes.')
        return
    payload = (update.message.text or '').partition(' ')[2].strip()
    marker = ' ans. '
    lowered = payload.lower()
    if marker not in lowered:
        await update.message.reply_text('Usage: /addquiz question text ans. answer text')
        return
    idx = lowered.rfind(marker)
    question = payload[:idx].strip()
    answer = payload[idx + len(marker):].strip()
    if not question or not answer:
        await update.message.reply_text('Question or answer missing. Usage: /addquiz question text ans. answer text')
        return
    quiz_id = add_quiz(question, answer, user.id)
    await update.message.reply_text(f'Quiz saved as #{quiz_id}. Total quizzes: {get_quiz_count()}')


async def showquiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text('Only my owner or sudo users can see saved quizzes.')
        return
    rows = list_quizzes()
    if not rows:
        await update.message.reply_text('No quizzes saved yet.')
        return
    lines = []
    for quiz_id, question, answer, _created_at in rows:
        q = question.replace('\n', ' ').strip()
        if len(q) > 70:
            q = q[:67] + '...'
        lines.append(f'#{quiz_id} - {q} | ans: {answer}')
    await update.message.reply_text('Saved quizzes:\n' + '\n'.join(lines[:80]))


async def delquiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text('Only my owner or sudo users can delete quizzes.')
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text('Usage: /delquiz quiz_number')
        return
    quiz_id = int(context.args[0])
    if delete_quiz(quiz_id):
        await update.message.reply_text(f'Quiz #{quiz_id} deleted. Total quizzes: {get_quiz_count()}')
    else:
        await update.message.reply_text('Quiz not found.')


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await resolve_target_user(update, context)
    if not target:
        target = update.effective_user
    user_id = target.id if target else (update.effective_user.id if update.effective_user else 0)
    chat_id = update.effective_chat.id if update.effective_chat else 0
    await update.message.reply_text(f'User ID: {user_id}\nChat ID: {chat_id}')


async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    except Exception as e:
        await update.message.reply_text("Could not fetch administrator list.")
        return

    admin_tags = []
    for a in admins:
        u = a.user
        tag = format_user_tag(u.id, u.first_name, u.username)
        admin_tags.append(tag)

    lines = ["🌸 Here are the amazing admins keeping this group safe! 💕", ""]
    for tag in admin_tags:
        lines.append(f"• {tag}")

    # If used in context of a complaint/report (message reply)
    reason = extract_reason(update)
    if update.message and update.message.reply_to_message:
        lines.append("")
        lines.append(f"📝 Reason: {html.escape(reason or 'User requested admin attention')}")

    await update.message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await resolve_target_user(update, context)
    user_id = target.id
    first_name = getattr(target, 'first_name', 'User')
    username = getattr(target, 'username', None)
    username_str = f"@{username}" if username else "Not set"
    user_link = format_user_link(user_id, first_name)

    status_str = "Member"
    banned_str = "No"

    if update.effective_chat and update.effective_chat.type in ('group', 'supergroup'):
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
            st = member.status
            status_map = {
                'creator': 'Owner',
                'administrator': 'Administrator',
                'member': 'Member',
                'restricted': 'Restricted',
                'left': 'Left',
                'kicked': 'Kicked'
            }
            status_str = status_map.get(st, st.capitalize())
            if st == 'kicked':
                banned_str = "Yes"
        except Exception:
            pass

    info_text = (
        "👤 User Information\n\n"
        f"🆔 ID: {user_id}\n"
        f"👤 First Name: {html.escape(first_name)}\n"
        f"🔹 Username: {username_str}\n"
        f"🔗 User Link: {user_link}\n"
        f"🛡 Status: {status_str}\n"
        f"🚫 Banned: {banned_str}"
    )
    await update.message.reply_html(info_text, disable_web_page_preview=True)


async def settitle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    title = update.message.text.partition(' ')[2].strip()
    if not title:
        await update.message.reply_text('Usage: /settitle New Title')
        return
    try:
        await context.bot.set_chat_title(update.effective_chat.id, title)
        await update.message.reply_text('Title updated.')
    except Exception:
        await update.message.reply_text('Could not update title.')


async def setdesc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    desc = update.message.text.partition(' ')[2].strip()
    if not desc:
        await update.message.reply_text('Usage: /setdesc New Description')
        return
    try:
        await context.bot.set_chat_description(update.effective_chat.id, desc)
        await update.message.reply_text('Description updated.')
    except Exception:
        await update.message.reply_text('Could not update description.')


async def cleanservice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    set_setting(update.effective_chat.id, 'clean_service', context.args[0] if context.args and context.args[0] in ('on', 'off') else 'on')
    await update.message.reply_text(f'Clean service set to {get_setting(update.effective_chat.id, "clean_service", "off")}.')


async def zombies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return

    if context.args and context.args[0].lower() in ('clean', 'cleanup'):
        cleaned_count = clean_zombies(update.effective_chat.id)
        await update.message.reply_text(f"🧹 Zombie records cleaned! Removed {cleaned_count} departed user record(s).")
        return

    zombies = list_zombies(update.effective_chat.id)
    if not zombies:
        await update.message.reply_text("🧟 Zombie Members\n\nNo departed or inactive zombie records found in this group.")
        return

    lines = ["🧟 Zombie Members", ""]
    for idx, (uid, full_name, username, st) in enumerate(zombies, start=1):
        tag = format_user_tag(uid, full_name, username)
        lines.append(f"{idx}. {tag}")

    lines.append("")
    lines.append("Use `/zombies clean` to remove these records from group database tracking.")
    await update.message.reply_html("\n".join(lines), disable_web_page_preview=True)


async def promote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("🌸 This command can only be used in groups desu~ 💕")
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    # 1. Check user permissions to promote/manage admins
    try:
        user_member = await context.bot.get_chat_member(chat_id, user_id)
        if user_member.status == 'creator' or is_owner_or_sudo(user_id):
            can_manage = True
        elif user_member.status == 'administrator':
            can_manage = getattr(user_member, 'can_promote_members', False)
        else:
            can_manage = False

        if not can_manage:
            await update.message.reply_text("🥺 Ehehe, I don't have enough permission to do that yet! Please make sure I'm an admin with the required permissions. 🌸")
            return
    except Exception:
        await update.message.reply_text("🥺 Ehehe, I don't have enough permission to do that yet! Please make sure I'm an admin with the required permissions. 🌸")
        return

    # 2. Check bot permissions to promote members
    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status != 'administrator' or not getattr(bot_member, 'can_promote_members', False):
            await update.message.reply_text("«🥺 Oopsie! I can't do that right now because Telegram doesn't allow me to change this member's permissions. 💕\nPlease make sure I have the required admin permissions!»")
            return
    except Exception:
        await update.message.reply_text("«🥺 Oopsie! I can't do that right now because Telegram doesn't allow me to change this member's permissions. 💕\nPlease make sure I have the required admin permissions!»")
        return

    # 3. Resolve and validate target user
    target = await resolve_target_user(update, context)
    if not target or target.id == update.effective_user.id:
        await update.message.reply_text("🌸 Please reply to or specify a valid group member you want to promote!")
        return

    try:
        target_member = await context.bot.get_chat_member(chat_id, target.id)
        if target_member.status in ('left', 'kicked'):
            await update.message.reply_text("🌷 Aww, Telegram won't let me perform that action on this member.")
            return
        if target_member.status in ('administrator', 'creator'):
            await update.message.reply_text("🌸 That member is already an admin desu~ 💕")
            return
    except Exception:
        await update.message.reply_text("🌷 Aww, Telegram won't let me perform that action on this member.")
        return

    # 4. Promote target member
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            can_change_info=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_manage_video_chats=True
        )
        tag = format_user_tag(target.id, getattr(target, 'first_name', 'User'), getattr(target, 'username', None))
        log_admin_action(chat_id, user_id, 'promote', target.id)
        await update.message.reply_html(f"👑✨ Yay! {tag} has been promoted to group admin! 🌸")
    except Exception:
        await update.message.reply_text("«🥺 Oopsie! I can't do that right now because Telegram doesn't allow me to change this member's permissions. 💕\nPlease make sure I have the required admin permissions!»")


async def demote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or update.effective_chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("🌸 This command can only be used in groups desu~ 💕")
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    # 1. Check user permissions to manage/demote admins
    try:
        user_member = await context.bot.get_chat_member(chat_id, user_id)
        if user_member.status == 'creator' or is_owner_or_sudo(user_id):
            can_manage = True
        elif user_member.status == 'administrator':
            can_manage = getattr(user_member, 'can_promote_members', False)
        else:
            can_manage = False

        if not can_manage:
            await update.message.reply_text("🥺 Ehehe, I don't have enough permission to do that yet! Please make sure I'm an admin with the required permissions. 🌸")
            return
    except Exception:
        await update.message.reply_text("🥺 Ehehe, I don't have enough permission to do that yet! Please make sure I'm an admin with the required permissions. 🌸")
        return

    # 2. Check bot permissions
    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status != 'administrator' or not getattr(bot_member, 'can_promote_members', False):
            await update.message.reply_text("«🥺 Oopsie! I can't do that right now because Telegram doesn't allow me to change this member's permissions. 💕\nPlease make sure I have the required admin permissions!»")
            return
    except Exception:
        await update.message.reply_text("«🥺 Oopsie! I can't do that right now because Telegram doesn't allow me to change this member's permissions. 💕\nPlease make sure I have the required admin permissions!»")
        return

    # 3. Resolve target
    target = await resolve_target_user(update, context)
    if not target or target.id == update.effective_user.id:
        await update.message.reply_text("🌸 Please reply to or specify a valid admin you want to demote!")
        return

    try:
        target_member = await context.bot.get_chat_member(chat_id, target.id)
        if target_member.status == 'creator':
            await update.message.reply_text("«🌸 Ehehe, I can't do that to an admin! Telegram won't let me change their permissions this way. 🥺»")
            return
        if target_member.status != 'administrator':
            await update.message.reply_text("🌸 That member is not an admin desu~ 💕")
            return
    except Exception:
        await update.message.reply_text("«🌸 Ehehe, I can't do that to an admin! Telegram won't let me change their permissions this way. 🥺»")
        return

    # 4. Demote target admin
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            can_change_info=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_delete_messages=False,
            can_invite_users=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_promote_members=False,
            can_manage_video_chats=False,
            is_anonymous=False
        )
        tag = format_user_tag(target.id, getattr(target, 'first_name', 'User'), getattr(target, 'username', None))
        log_admin_action(chat_id, user_id, 'demote', target.id)
        await update.message.reply_html(f"🌸 {tag} has been demoted back to a normal member desu~ 💕")
    except Exception:
        await update.message.reply_text("«🌸 Ehehe, I can't do that to an admin! Telegram won't let me change their permissions this way. 🥺»")



async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    text = ' '.join(context.args).strip() if context.args else ''
    if not text and update.message and update.message.reply_to_message and update.message.reply_to_message.text:
        text = update.message.reply_to_message.text.strip()
    if not text:
        await update.message.reply_text('Usage: /setwelcome <text> - Set custom welcome message. Placeholders: {user}, {group} desu~ 🌸')
        return
    set_setting(update.effective_chat.id, 'welcome_text', text)
    set_setting(update.effective_chat.id, 'welcome', 'on')
    await update.message.reply_text('🌸 Custom welcome message saved successfully! 💕')


async def setgoodbye_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    text = ' '.join(context.args).strip() if context.args else ''
    if not text and update.message and update.message.reply_to_message and update.message.reply_to_message.text:
        text = update.message.reply_to_message.text.strip()
    if not text:
        await update.message.reply_text('Usage: /setgoodbye <text> - Set custom goodbye message. Placeholders: {user} desu~ 🌸')
        return
    set_setting(update.effective_chat.id, 'goodbye_text', text)
    set_setting(update.effective_chat.id, 'goodbye', 'on')
    await update.message.reply_text('🌸 Custom goodbye message saved successfully! 💕')



async def kickme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type not in ('group', 'supergroup'):
        await update.message.reply_text("🌸 /kickme can only be used in group chats!")
        return

    try:
        await update.message.reply_text(""" 🥺 Aww, you want to leave?
Okayyy… kicking you out gently! 👋💗
Bye-bye, take care! 🌸""")
        await context.bot.ban_chat_member(chat.id, user.id)
        await context.bot.unban_chat_member(chat.id, user.id)
        log_admin_action(chat.id, user.id, 'kickme', user.id)
    except Exception:
        await safe_reply_error(update.effective_message, "🌷 Oopsie! Telegram won't let me kick you right now.")




async def addpack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addpack command handler.
    Allows authorized owners/sudos to save an entire sticker pack by replying to a sticker.
    """
    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text("🌸 Only my owner or sudo users can save sticker packs desu~!")
        return

    msg = update.effective_message
    if not msg:
        return

    reply_msg = msg.reply_to_message
    if not reply_msg or not reply_msg.sticker:
        await update.message.reply_text("🌸 Please reply to a sticker with /addpack to save the sticker pack!")
        return

    sticker = reply_msg.sticker
    set_name = sticker.set_name
    if not set_name:
        await update.message.reply_text("🌸 This sticker does not belong to a valid sticker pack desu~")
        return

    try:
        sticker_set = await context.bot.get_sticker_set(set_name)
    except Exception as e:
        await update.message.reply_text("🌸 Could not retrieve sticker pack details from Telegram desu~")
        return

    stickers_data = []
    for s in sticker_set.stickers:
        stickers_data.append({
            'file_id': s.file_id,
            'file_unique_id': getattr(s, 'file_unique_id', ''),
            'emoji': getattr(s, 'emoji', ''),
            'type': getattr(s, 'type', 'regular')
        })

    from store import save_sticker_pack
    save_sticker_pack(set_name, sticker_set.title, stickers_data)
    log_admin_action(update.effective_chat.id, update.effective_user.id, 'addpack', details=set_name)

    await update.message.reply_text(f"Done~ 🌸 Added the whole sticker pack '{sticker_set.title}' ({len(stickers_data)} stickers)!")


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text('🌸 Only my owner or sudo users can view the user list desu~!')
        return

    users = get_all_users()
    if not users:
        await update.message.reply_text('🌸 No registered users found yet!')
        return

    lines = [f'👥 <b>Total Users:</b> {len(users)}\n']
    for idx_u, u in enumerate(users, 1):
        uid = u['user_id']
        name = html.escape(u.get('full_name') or 'User')
        un = f"@{u['username']}" if u.get('username') else 'None'
        profile_link = f'<a href="tg://user?id={uid}">tap to open profile</a>'
        lines.append(f'{idx_u}. <b>{name}</b> (ID: <code>{uid}</code>) | {un} | {profile_link}')

    out_text = '\n'.join(lines)
    if len(out_text) > 4000:
        for i in range(0, len(out_text), 4000):
            await update.message.reply_text(out_text[i:i+4000], parse_mode='HTML', disable_web_page_preview=True)
    else:
        await update.message.reply_text(out_text, parse_mode='HTML', disable_web_page_preview=True)


async def grouplist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text('🌸 Only my owner or sudo users can view the group list desu~!')
        return

    groups = get_all_active_groups_detailed()
    if not groups:
        await update.message.reply_text('🌸 No connected active groups found yet!')
        return

    lines = [f'👥 <b>Connected Groups List:</b> {len(groups)}\n']
    for idx_g, g in enumerate(groups, 1):
        gid = g['chat_id']
        title = html.escape(g.get('title') or str(gid))
        members = g.get('member_count') if g.get('member_count') is not None else 'Unknown'
        added_by_id = g.get('added_by_user_id')

        added_by_str = 'Unknown'
        if added_by_id:
            adder = get_user_by_id(added_by_id)
            if adder:
                adder_name = html.escape(adder[1] or 'User')
                adder_link = f'<a href="tg://user?id={added_by_id}">tap to open profile</a>'
                added_by_str = f'{adder_name} (<code>{added_by_id}</code> - {adder_link})'
            else:
                adder_link = f'<a href="tg://user?id={added_by_id}">tap to open profile</a>'
                added_by_str = f'ID: <code>{added_by_id}</code> ({adder_link})'

        lines.append(
            f'{idx_g}. <b>{title}</b> (ID: <code>{gid}</code>)\n'
            f'   • 👥 Total Members: {members}\n'
            f'   • 👤 Added By: {added_by_str}'
        )

    out_text = '\n\n'.join(lines)
    if len(out_text) > 4000:
        for i in range(0, len(out_text), 4000):
            await update.message.reply_text(out_text[i:i+4000], parse_mode='HTML', disable_web_page_preview=True)
    else:
        await update.message.reply_text(out_text, parse_mode='HTML', disable_web_page_preview=True)


async def tag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message

    if not chat or not user or not msg:
        return

    if chat.type not in ('group', 'supergroup'):
        await msg.reply_text("🌸 This command can only be used inside a group.")
        return

    if not await is_admin(update, context):
        await msg.reply_text("🌸 Gomen ne! Only group admins and the owner can use tag everyone desu~ ✨")
        return

    giver_name = html.escape(user.first_name or user.full_name or "Admin")

    text_after_cmd = ""
    raw_text = msg.text or msg.caption or ""

    if raw_text.startswith('/tag') or raw_text.startswith('/all'):
        parts = raw_text.split(maxsplit=1)
        if len(parts) > 1:
            text_after_cmd = parts[1].strip()
    elif raw_text.lower().startswith('@all'):
        parts = raw_text.split(maxsplit=1)
        if len(parts) > 1:
            text_after_cmd = parts[1].strip()

    if not text_after_cmd and context.args:
        text_after_cmd = " ".join(context.args).strip()

    if not text_after_cmd and msg.reply_to_message:
        replied = msg.reply_to_message
        text_after_cmd = (replied.text or replied.caption or "").strip()

    if not text_after_cmd:
        custom_message = "Hey everyone! 👋"
    else:
        custom_message = html.escape(text_after_cmd)

    try:
        members = get_group_members(chat.id)
    except Exception as e:
        logger.error(f"Error retrieving group members for tag: {e}")
        await safe_reply_error(msg)
        return

    bot_id = context.bot.id if context and context.bot else None

    eligible_members = []
    for uid, name, username, is_bot in members:
        if is_bot or (bot_id and uid == bot_id):
            continue
        clean_name = (name or "").strip()
        if not clean_name or clean_name.lower() in ("deleted account", "deleted"):
            continue
        eligible_members.append((uid, clean_name, username))

    if not eligible_members:
        await msg.reply_text("😅 I couldn't find any members to tag right now.")
        return

    CHUNK_SIZE = 10
    total_eligible = len(eligible_members)
    chunks = [eligible_members[i:i + CHUNK_SIZE] for i in range(0, total_eligible, CHUNK_SIZE)]

    header = f"""📢 <b>Attention everyone!</b>

👤 <b>Called by:</b> {giver_name}

💬 {custom_message}

"""

    partial_failure = False

    for idx, chunk in enumerate(chunks):
        tags_str = " ".join([format_user_link(uid, name) for uid, name, username in chunk])
        chunk_text = (header + tags_str) if idx == 0 else tags_str

        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=chunk_text,
                parse_mode="HTML",
                reply_to_message_id=msg.message_id if idx == 0 else None
            )
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=chunk_text,
                    parse_mode="HTML",
                    reply_to_message_id=msg.message_id if idx == 0 else None
                )
            except Exception as retry_err:
                logger.error(f"Failed sending tag batch after retry: {retry_err}")
                partial_failure = True
        except Exception as err:
            logger.error(f"Failed sending tag batch: {err}")
            partial_failure = True

        if idx < len(chunks) - 1:
            await asyncio.sleep(0.5)

    if not partial_failure:
        await msg.reply_text(
            f"""✅ <b>Tag completed successfully!</b>

👤 {giver_name}, everyone has been notified. ✨""",
            parse_mode="HTML"
        )
    else:
        await msg.reply_text(
            f"""⚠️ <b>Tag completed with some limitations.</b>

👤 {giver_name}, some members could not be mentioned.""",
            parse_mode="HTML"
        )


async def packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text("🌸 Only my owner or sudo users can view sticker packs desu~!")
        return

    msg = update.effective_message
    if not msg:
        return

    from store import get_all_sticker_packs
    packs = get_all_sticker_packs()

    if not packs:
        await msg.reply_text("Aww~ I don't have any sticker packs saved yet! 🥺✨")
        return

    page = 0
    await send_packs_page(update, context, packs, page=page)


async def send_packs_page(update: Update, context: ContextTypes.DEFAULT_TYPE, packs: list, page: int = 0, is_callback: bool = False):
    per_page = 5
    total_packs = len(packs)
    total_pages = (total_packs + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))

    start_idx = page * per_page
    page_packs = packs[start_idx:start_idx + per_page]

    text_lines = ["<b>🌸 Stored Sticker Packs 🌸</b>", ""]
    for p in page_packs:
        title = p.get('title') or 'Untitled Pack'
        set_name = p.get('set_name') or 'unknown'
        count = p.get('count', 0)
        text_lines.append(f"📦 <b>{title}</b>")
        text_lines.append(f"🆔 <code>{set_name}</code>")
        text_lines.append(f"🔢 {count} stickers")
        text_lines.append("")

    text_lines.append(f"<i>Page {page + 1} of {total_pages} (Total: {total_packs})</i>")
    text = "\n".join(text_lines)

    buttons = []
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"packs_page:{page - 1}"))
        if page < total_pages - 1:
            row.append(InlineKeyboardButton("Next ➡️", callback_data=f"packs_page:{page + 1}"))
        buttons.append(row)

    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    if is_callback and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            pass
    else:
        msg = update.effective_message
        if msg:
            await msg.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)


async def packs_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("packs_page:"):
        return

    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await query.answer("🌸 Only my owner or sudo users can view sticker packs desu~!", show_alert=True)
        return

    await query.answer()
    try:
        page = int(query.data.split(":")[1])
    except Exception:
        page = 0

    from store import get_all_sticker_packs
    packs = get_all_sticker_packs()
    if not packs:
        await query.edit_message_text("Aww~ I don't have any sticker packs saved yet! 🥺✨")
        return

    await send_packs_page(update, context, packs, page=page, is_callback=True)


async def delpack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text("🌸 Only my owner or sudo users can delete sticker packs desu~!")
        return

    msg = update.effective_message
    if not msg:
        return

    args = context.args
    if not args:
        await msg.reply_text("🌸 Usage: <code>/delpack &lt;sticker_pack_id&gt;</code>", parse_mode="HTML")
        return

    target_id = args[0].strip()
    if not target_id:
        await msg.reply_text("🌸 Usage: <code>/delpack &lt;sticker_pack_id&gt;</code>", parse_mode="HTML")
        return

    from store import get_sticker_pack, delete_sticker_pack
    pack_data = get_sticker_pack(target_id)
    if not pack_data:
        await msg.reply_text("Uwaa~ I couldn't find that sticker pack anywhere! 🥺🔍")
        return

    pack_name = pack_data.get('title') or target_id
    success = delete_sticker_pack(target_id)

    if success:
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'delpack', details=target_id)
        msg_text = f"✨ Pack deleted successfully!\n🧸 Pack: {pack_name}\n🆔 ID: <code>{target_id}</code>"
        await msg.reply_text(msg_text, parse_mode="HTML")
    else:
        await msg.reply_text("Uwaa~ I couldn't find that sticker pack anywhere! 🥺🔍")



async def pack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pack <id> command handler.
    Allows the bot owner to preview a specific saved sticker pack by its set_name/id.
    """
    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await update.message.reply_text("🌸 Only my owner or sudo users can view sticker pack details desu~!")
        return

    msg = update.effective_message
    if not msg:
        return

    args = context.args
    if not args:
        await msg.reply_text("🌸 Usage: <code>/pack &lt;sticker_pack_id&gt;</code>", parse_mode="HTML")
        return

    target_id = args[0].strip()
    if not target_id:
        await msg.reply_text("🌸 Usage: <code>/pack &lt;sticker_pack_id&gt;</code>", parse_mode="HTML")
        return

    from store import get_sticker_pack
    pack_data = get_sticker_pack(target_id)
    if not pack_data:
        await msg.reply_text("Uwaa~ I couldn't find that sticker pack anywhere! 🥺🔍")
        return

    title = pack_data.get('title') or 'Untitled Pack'
    set_name = pack_data.get('set_name') or target_id
    stickers = pack_data.get('stickers') or []
    count = pack_data.get('count', len(stickers))

    text_lines = [
        f"📦 <b>{html.escape(title)}</b>",
        f"🆔 <code>{html.escape(set_name)}</code>",
        f"🔢 {count} stickers in pack\n"
    ]

    await msg.reply_text("\n".join(text_lines), parse_mode="HTML")

    if stickers:
        first_sticker = stickers[0]
        file_id = first_sticker.get('file_id')
        if file_id:
            try:
                await msg.reply_sticker(sticker=file_id)
            except Exception as e:
                logger.error(f"Error sending sticker preview for pack {set_name}: {e}")
