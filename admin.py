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
)
from helpers import is_admin, is_owner_or_sudo


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
            await update.message.reply_text('Admin only command.')
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


async def apply_action(chat_id, target_id, action, context, duration_seconds=None):
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
    target = reply_target(update)
    if target:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'ban', target.id)
        await update.message.reply_text(f'Banned {target.full_name}.')


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
    target = reply_target(update)
    if target:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'kick', target.id)
        await update.message.reply_text(f'Kicked {target.full_name}.')


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
    if not update.message.reply_to_message:
        await update.message.reply_text('Reply to a message and use /purge to delete from that message to here.')
        return
    start_id = update.message.reply_to_message.message_id
    end_id = update.message.message_id
    removed = 0
    for mid in range(start_id, end_id + 1):
        try:
            await context.bot.delete_message(update.effective_chat.id, mid)
            removed += 1
        except Exception:
            pass
    log_admin_action(update.effective_chat.id, update.effective_user.id, 'purge', details=f'removed={removed}')


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
    target = reply_target(update)
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
        await update.message.reply_text(f'Muted {target.full_name}.')


async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = reply_target(update)
    if target:
        perms = ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_change_info=False, can_invite_users=True, can_pin_messages=False, can_manage_topics=False)
        await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=perms)
        clear_temp_action(update.effective_chat.id, target.id, 'tmute')
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'unmute', target.id)
        await update.message.reply_text(f'Unmuted {target.full_name}.')


async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = reply_target(update)
    if target:
        reason = extract_reason(update)
        count = add_warn(update.effective_chat.id, target.id, reason)
        limit = int(get_setting(update.effective_chat.id, 'warn_limit', '3'))
        action = get_setting(update.effective_chat.id, 'warn_mode', 'ban')
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'warn', target.id, f'count={count};reason={reason}')
        await update.message.reply_text(f'{target.full_name} now has {count} warn(s). Limit: {limit}. Action: {action}.')
        if count >= limit:
            try:
                await apply_action(update.effective_chat.id, target.id, action, context, parse_duration_to_seconds(get_setting(update.effective_chat.id, 'warn_time', '1h')))
                await update.message.reply_text(f'{target.full_name} reached warn limit. Action applied: {action}.')
                log_admin_action(update.effective_chat.id, update.effective_user.id, f'auto_{action}_warn_limit', target.id, f'count={count}')
            except Exception:
                pass


async def warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = reply_target(update)
    if target:
        reasons = get_warn_reasons(update.effective_chat.id, target.id)
        text = f'{target.full_name} has {get_warns(update.effective_chat.id, target.id)} warn(s).'
        if reasons:
            text += '\nReasons:\n' + reasons[:2500]
        await update.message.reply_text(text)


async def warned_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    rows = list_warned_users(update.effective_chat.id)
    if not rows:
        await update.message.reply_text('No warned users in this chat.')
        return
    lines = [f'{name or uid} - {count} warn(s)' for uid, count, name in rows]
    await update.message.reply_text('Warned users:\n' + '\n'.join(lines[:80]))


async def clearwarns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    target = reply_target(update)
    if target:
        reset_warns(update.effective_chat.id, target.id)
        log_admin_action(update.effective_chat.id, update.effective_user.id, 'clearwarns', target.id)
        await update.message.reply_text(f'Cleared warns for {target.full_name}.')


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
    if not context.args or context.args[0] not in ('warn', 'mute', 'kick', 'ban', 'tmute', 'tban'):
        await update.message.reply_text('Usage: /warnaction warn|mute|kick|ban|tmute|tban [1h]')
        return
    set_setting(update.effective_chat.id, 'warn_mode', context.args[0])
    if len(context.args) > 1:
        set_setting(update.effective_chat.id, 'warn_time', context.args[1])
    await update.message.reply_text(f'Warn action set to {context.args[0]}.')


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
    text = update.message.text.partition(' ')[2]
    if '|' not in text:
        await update.message.reply_text('Usage: /filter keyword | reply')
        return
    keyword, reply = [x.strip() for x in text.split('|', 1)]
    save_filter(update.effective_chat.id, keyword, reply)
    await update.message.reply_text(f'Filter saved for {keyword}.')


async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from store import get_filters
    items = [k for k, _ in get_filters(update.effective_chat.id)]
    await update.message.reply_text('Filters: ' + (', '.join(items) if items else 'None'))


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    if not context.args:
        await update.message.reply_text('Usage: /stop keyword')
        return
    delete_filter(update.effective_chat.id, context.args[0])
    await update.message.reply_text('Filter deleted.')


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


async def modlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    rows = get_recent_audit_logs(update.effective_chat.id, 20)
    if not rows:
        await update.message.reply_text('No moderation logs yet.')
        return
    lines = [f'{created_at} | actor {actor_id} | {action} | target {target_id or "-"} | {details or ""}' for actor_id, action, target_id, details, created_at in rows]
    await update.message.reply_text('\n'.join(lines[:40]))




async def groupquota_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update, context):
        return
    lines = get_group_quota_lines()
    await update.message.reply_text('GROUP QUOTA\n' + '\n'.join(lines))

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not reply_target_message(update):
        return
    target_msg = reply_target_message(update)
    if not target_msg or not target_msg.from_user:
        await update.message.reply_text('Reply to a valid user message to report it.')
        return
    target_id = target_msg.from_user.id
    reason = extract_reason(update)[:MAX_LENGTHS['report_reason']]
    if report_exists_recent(update.effective_chat.id, update.effective_user.id, target_msg.message_id, 60):
        await update.message.reply_text('You already reported this message recently.')
        return
    if not allow_report_event(update.effective_chat.id, update.effective_user.id, target_id, 60, 2):
        await update.message.reply_text('Rate limit reached: you can report this user only 2 times per minute.')
        return
    add_report(update.effective_chat.id, update.effective_user.id, target_id, target_msg.message_id, reason)
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    mentions = ' '.join([f'<a href="tg://user?id={a.user.id}">{html.escape(a.user.first_name)}</a>' for a in admins if not a.user.is_bot][:5])
    await update.message.reply_html(f'Reported. {mentions}\nReason: {html.escape(reason or "No reason")}', disable_web_page_preview=True)
    log_admin_action(update.effective_chat.id, update.effective_user.id, 'report', target_msg.from_user.id if target_msg.from_user else None, reason)


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
    target = reply_target(update)
    if not target:
        await update.message.reply_text('Reply to a user to fedban them.')
        return
    reason = extract_reason(update)
    fed_ban_user(fed_id, target.id, reason, update.effective_user.id)
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
    except Exception:
        pass
    await update.message.reply_text(f'Fedbanned {target.full_name}.')


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
    target = reply_target(update) or update.effective_user
    await update.message.reply_text(f'User ID: {target.id}\nChat ID: {update.effective_chat.id}')


async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    lines = [a.user.full_name for a in admins]
    await update.message.reply_text('Admins:\n' + '\n'.join(lines))


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = reply_target(update) or update.effective_user
    await update.message.reply_text(f'Name: {target.full_name}\nUsername: @{target.username or "none"}\nID: {target.id}')


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
    await update.message.reply_text('Zombie cleanup placeholder: Telegram bot API cannot reliably enumerate departed members without extra tracking, but member tracking is active for future cleanup support.')
