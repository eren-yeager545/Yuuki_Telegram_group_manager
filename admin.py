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
    get_user_by_username, get_user_by_id, list_zombies, clean_zombies, get_user_messages, clear_user_messages, record_user_message
)
from helpers import is_admin, is_owner_or_sudo, safe_reply_error


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
    if update.effective_chat and update.effective_chat.type in ('group', 'supergroup'):
        if not await is_admin(update, context):
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
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def format_user_tag(user_id: int, name: str, username: str = None) -> str:
    if username:
        clean_user = username.lstrip('@').strip()
        return f"@{clean_user}"
    return format_user_link(user_id, name)


async def resolve_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.reply_to_message and update.message.reply_to_message.from_user:
        return update.message.reply_to_message.from_user
    if context.args:
        first_arg = context.args[0].strip()
        if first_arg.startswith('@'):
            u_info = get_user_by_username(first_arg)
            if u_info:
                class ResolvedUser:
                    def __init__(self, uid, name, uname):
                        self.id = uid
                        self.first_name = name or 'User'
                        self.full_name = name or 'User'
                        self.username = uname
                        self.is_bot = False
                return ResolvedUser(*u_info)
        elif first_arg.isdigit():
            uid = int(first_arg)
            u_info = get_user_by_id(uid)
            if u_info:
                class ResolvedUser:
                    def __init__(self, uid, name, uname):
                        self.id = uid
                        self.first_name = name or 'User'
                        self.full_name = name or 'User'
                        self.username = uname
                        self.is_bot = False
                return ResolvedUser(*u_info)
            else:
                class SimpleUser:
                    def __init__(self, uid):
                        self.id = uid
                        self.first_name = f'User {uid}'
                        self.full_name = f'User {uid}'
                        self.username = None
                        self.is_bot = False
                return SimpleUser(uid)
    return update.effective_user


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
    if not await admin_only(update, context):
        return
    target = await resolve_target_user(update, context)
    if target:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'ban', target.id)
        await update.message.reply_html(f'Banned {format_user_tag(target.id, target.first_name, target.username)}.')


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        await update.message.reply_text('Usage: /unban user_id')
        return
    try:
        uid = int(context.args[0])
        await context.bot.unban_chat_member(update.effective_chat.id, uid, only_if_banned=True)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'unban', uid)
        await update.message.reply_text(f'Unbanned {uid}.')
    except Exception:
        await update.message.reply_text('Failed to unban that user id.')


async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = await resolve_target_user(update, context)
    if target:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'kick', target.id)
        await update.message.reply_html(f'Kicked {format_user_tag(target.id, target.first_name, target.username)}.')


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


async def purge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    chat_id = update.effective_chat.id
    target_user = await resolve_target_user(update, context)

    if target_user:
        user_tag = format_user_tag(target_user.id, getattr(target_user, 'first_name', 'User'), getattr(target_user, 'username', None))
        mids = clear_user_messages(chat_id, target_user.id)

        if update.message.reply_to_message:
            reply_mid = update.message.reply_to_message.message_id
            cmd_mid = update.message.message_id
            mids_set = set(mids)
            for mid in range(reply_mid, cmd_mid + 1):
                mids_set.add(mid)
            mids = sorted(list(mids_set), reverse=True)

        removed = 0
        for mid in mids:
            try:
                await context.bot.delete_message(chat_id, mid)
                removed += 1
            except Exception:
                pass

        try:
            await context.bot.delete_message(chat_id, update.message.message_id)
        except Exception:
            pass

        log_admin_action(chat_id, update.effective_user.id, 'purge', target_id=target_user.id, details=f'removed={removed}')
        await update.effective_chat.send_message(
            f"Swept away! 🧹✨ All messages from {user_tag} have been purged from the group! Everything is clean and sweet now~ 🌸 (⁠人⁠*⁠´⁠∀⁠｀⁠)"
        )
        return

    if update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        target_user = reply_msg.from_user
        user_tag = format_user_tag(target_user.id, target_user.first_name, target_user.username)
        start_id = reply_msg.message_id
        end_id = update.message.message_id
        removed = 0
        for mid in range(start_id, end_id + 1):
            try:
                await context.bot.delete_message(chat_id, mid)
                removed += 1
            except Exception:
                pass
        clear_user_messages(chat_id, target_user.id)
        log_admin_action(chat_id, update.effective_user.id, 'purge', target_id=target_user.id, details=f'removed={removed}')
        await update.effective_chat.send_message(
            f"Swept away! 🧹✨ All messages from {user_tag} have been purged from the group! Everything is clean and sweet now~ 🌸 (⁠人⁠*⁠´⁠∀⁠｀⁠)"
        )
        return

    await update.message.reply_text(
        "Reply to a message or specify a user (`/purge @username`) to delete all messages from that selected user desu~ 🌸 (⁠⁠◕⁠‿⁠◕⁠✿⁠)"
    )


async def dban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    chat_id = update.effective_chat.id
    target = await resolve_target_user(update, context)
    if not target:
        await update.message.reply_text("Reply to a user or specify a username/ID to ban and purge their messages~ 🌸 (⁠⁠◕⁠‿⁠◕⁠✿⁠)")
        return

    user_tag = format_user_tag(target.id, getattr(target, 'first_name', 'User'), getattr(target, 'username', None))

    try:
        await context.bot.ban_chat_member(chat_id, target.id, revoke_messages=True)
    except Exception as e:
        cid = await safe_reply_error(update.effective_message, "Gomen ne~ 🥺 I couldn't ban that user!\nReference ID: {cid}")
        return

    mids = clear_user_messages(chat_id, target.id)
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

    log_admin_action(chat_id, update.effective_user.id, 'dban', target.id)
    await update.effective_chat.send_message(
        f"Banned and swept clean! 🔨🧹 {user_tag} has been banned and all their messages were purged from the group! Stay safe everyone~ 🌸 (⁠≧⁠∇⁠≦⁠)/"
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
    text = update.message.text.partition(' ')[2]
    if '|' not in text:
        await update.message.reply_text('Usage: /save name | content || Label - https://url')
        return
    name, rest = [x.strip() for x in text.split('|', 1)]
    content, buttons_blob = rest, ''
    if '||' in rest:
        content, buttons_blob = [x.strip() for x in rest.split('||', 1)]
    save_note(update.effective_chat.id, name, content, buttons_blob)
    await update.message.reply_text(f'Saved note #{name}.')


async def getnote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return
    row = get_note(update.effective_chat.id, context.args[0])
    if not row:
        await update.message.reply_text('Note not found.')
        return
    content, buttons_blob = row
    await update.message.reply_text(content, reply_markup=build_keyboard(parse_buttons_blob(buttons_blob)))


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
    text = update.message.text.partition(' ')[2].strip()
    if not text or '|' not in text:
        await update.message.reply_text('Usage: /filter [text|sticker|voice|link] keyword | reply')
        return

    parts = [x.strip() for x in text.split('|', 1)]
    left_part, reply = parts[0], parts[1]

    left_words = left_part.split(maxsplit=1)
    filter_type = 'text'
    keyword = left_part

    if len(left_words) == 2 and left_words[0].lower() in ('text', 'sticker', 'voice', 'link'):
        filter_type = left_words[0].lower()
        keyword = left_words[1]

    save_filter(update.effective_chat.id, keyword, reply, filter_type)
    await update.message.reply_text(f'Filter ({filter_type}) saved for "{keyword}".')


async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from store import get_filters
    all_filters = get_filters(update.effective_chat.id)
    if not all_filters:
        await update.message.reply_text('No active filters in this chat.')
        return

    categorized = {'text': [], 'sticker': [], 'voice': [], 'link': []}
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
        'link': '🔗 Link'
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
    if not is_owner_or_sudo(update.effective_user.id if update.effective_user else 0):
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
    if not is_owner_or_sudo(user.id, OWNER_IDS, SUDO_USERS):
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
    if not is_owner_or_sudo(user.id, OWNER_IDS, SUDO_USERS):
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
    if not is_owner_or_sudo(user.id, OWNER_IDS, SUDO_USERS):
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
    await update.message.reply_text(f'User ID: {target.id}\nChat ID: {update.effective_chat.id}')


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
    user_link = f'<a href="tg://user?id={user_id}">Profile</a>'

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
