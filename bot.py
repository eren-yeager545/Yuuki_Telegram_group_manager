import asyncio
import logging
import random
import re
import time
import uuid
from collections import defaultdict, deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from config import BOT_TOKEN, LOG_CHANNEL_ID, QUIZ_INTERVAL_SECONDS, SEEN_UPDATE_COOLDOWN_SECONDS, PORT, WEBHOOK_URL, WEBHOOK_PATH, WEBHOOK_SECRET
from store import (
    init_db, get_active_groups, upsert_group, upsert_user, get_filters, get_setting,
    touch_member, get_note, get_random_quiz, get_buttons, list_blacklists,
    get_chat_federation, get_fed_ban, get_expired_temp_actions, clear_temp_action,
    log_admin_action, delete_user_data, get_global_link,
)
from common import start_cmd, help_cmd, ping_cmd, rules_cmd, stats_cmd, privacy_cmd, broadcast_cmd, build_help_category_keyboard, build_category_help_text, addsupport_cmd, addchannel_cmd, addlogger_cmd
from admin import *

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
message_cache = defaultdict(lambda: deque())
user_seen_cache = {}
group_seen_cache = {}
active_quizzes = {}


async def safe_reply_error(message_obj, public_text='Something went wrong. Reference ID: {cid}'):
    cid = uuid.uuid4().hex[:12]
    try:
        await message_obj.reply_text(public_text.format(cid=cid))
    except Exception:
        pass
    return cid


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error('Exception while handling an update:', exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await safe_reply_error(update.effective_message)



def register_help(app: Application, category: str, command: str, usage: str, example: str):
    registry = app.bot_data.setdefault('help_registry', {})
    registry.setdefault(category, []).append({'command': command, 'usage': usage, 'example': example})


def add_registered_command(app: Application, name: str, callback, category: str, usage: str, example: str):
    app.add_handler(CommandHandler(name, callback))
    register_help(app, category, f'/{name}', usage, example)



async def homepage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    registry = context.application.bot_data.get('help_registry', {})
    data = query.data or ''
    if data == 'home:privacy':
        text = (
            '🔐 Privacy Policy for Yuuki\n\n'
            'Stored data includes user/chat IDs, warns, reports, moderation logs, settings, notes, filters, welcomes, federation records, and limited anti-abuse metadata required for bot features.'
        )
        await query.message.reply_text(text)
        return
    if data == 'home:help':
        await query.message.reply_text('Choose a help category:', reply_markup=build_help_category_keyboard(registry))
        return
    if data.startswith('helpcat:'):
        category = data.split(':', 1)[1]
        await query.message.reply_text(build_category_help_text(registry, category))
        return


async def datadel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_owner_or_sudo(update.effective_user.id):
        await update.message.reply_text('Owner only command.')
        return
    if not context.args:
        await update.message.reply_text('Usage: /datadel <user_id>')
        return
    try:
        user_id = int(context.args[0])
    except Exception:
        await update.message.reply_text('Invalid user ID.')
        return
    delete_user_data(user_id)
    await update.message.reply_text(f'Deleted stored data for user ID {user_id}.')


async def log_event(context: ContextTypes.DEFAULT_TYPE, text: str):
    logger.info(text)
    logger_target = LOG_CHANNEL_ID
    logger_link = get_global_link('logger_link', '')
    if logger_target:
        try:
            await context.bot.send_message(logger_target, text)
        except Exception as e:
            logger.warning('Failed sending log [REDACTED_ERROR]: %s', type(e).__name__)
    elif logger_link:
        logger.info('Logger link configured but Bot API logging still requires LOG_CHANNEL_ID/chat id: %s', logger_link)


def note_keyboard(chat_id, scope):
    rows = get_buttons(chat_id, scope)
    return InlineKeyboardMarkup([[InlineKeyboardButton(text=label, url=url)] for label, url in rows]) if rows else None


def render_template(text, user, chat):
    return (text or '').replace('{first}', user.first_name or '').replace('{last}', user.last_name or '').replace('{fullname}', user.full_name or '').replace('{username}', '@' + user.username if user.username else user.full_name).replace('{chatname}', chat.title or '')


async def expire_quiz(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get('chat_id')
    quiz = active_quizzes.get(chat_id)
    if not quiz:
        return
    if not quiz.get('answered'):
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"Time is up 🌙 Nobody answered this one. The correct answer was: {quiz['answer_display']}")
        except Exception as e:
            logger.warning('Quiz reveal failed [REDACTED_TARGET]: %s', type(e).__name__)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=quiz['message_id'])
    except Exception:
        pass
    active_quizzes.pop(chat_id, None)


async def hourly_quiz(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, title in get_active_groups():
        if chat_id in active_quizzes:
            continue
        q = get_random_quiz()
        if not q:
            continue
        quiz_id, question, answer = q
        try:
            sent = await context.bot.send_message(chat_id=chat_id, text=(f"📝 Hourly Quiz #{quiz_id}\n\n{question}\n\nReply with the correct answer within 30 minutes."))
            active_quizzes[chat_id] = {'quiz_id': quiz_id, 'question': question, 'answer': ' '.join(answer.strip().lower().split()), 'answer_display': answer.strip(), 'message_id': sent.message_id, 'expires_at': time.time() + 1800, 'answered': False}
            context.job_queue.run_once(expire_quiz, when=1800, data={'chat_id': chat_id}, name=f'quiz_expire_{chat_id}_{int(time.time())}')
            await log_event(context, f'Quiz #{quiz_id} sent to a configured group [REDACTED]')
        except Exception as e:
            logger.warning('Quiz failed [REDACTED_TARGET]: %s', type(e).__name__)



async def run_broadcast(message: str, context: ContextTypes.DEFAULT_TYPE):
    groups = get_active_groups()
    sent = 0
    for chat_id, _ in groups:
        try:
            await context.bot.send_message(chat_id, message)
            sent += 1
        except Exception:
            pass
    await log_event(context, f'Broadcast delivered to {sent}/{len(groups)} groups.')


async def wrapped_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await log_event(context, 'Bot started by an authorized user [REDACTED]')
    await start_cmd(update, context)


async def rules_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    custom = get_setting(update.effective_chat.id, 'rules_text', '') if update.effective_chat else ''
    if custom:
        await update.message.reply_text(custom, reply_markup=note_keyboard(update.effective_chat.id, 'rules'))
    else:
        await rules_cmd(update, context)


async def service_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return
    if get_setting(chat.id, 'clean_service', 'off') == 'on':
        try:
            await msg.delete()
            return
        except Exception:
            pass
    if msg.new_chat_members and get_setting(chat.id, 'welcome', 'off') == 'on':
        for member in msg.new_chat_members:
            mute_for = get_setting(chat.id, 'welcome_mute', 'off')
            if mute_for != 'off':
                seconds = parse_duration_to_seconds(mute_for)
                if seconds:
                    try:
                        await context.bot.restrict_chat_member(chat.id, member.id, permissions=ChatPermissions(can_send_messages=False), until_date=int(time.time()) + seconds)
                    except Exception:
                        pass
            text = render_template(get_setting(chat.id, 'welcome_text', 'Welcome {first}!'), member, chat)
            await msg.reply_text(text, reply_markup=note_keyboard(chat.id, 'welcome'))
    if msg.left_chat_member and get_setting(chat.id, 'goodbye', 'off') == 'on':
        template = get_setting(chat.id, 'goodbye_text', 'Goodbye {fullname} 🌙')
        await msg.reply_text(render_template(template, msg.left_chat_member, chat))


async def maybe_delete(msg):
    try:
        await msg.delete()
        return True
    except Exception:
        return False


async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return
    now = int(time.time())
    if chat.type in ('group', 'supergroup'):
        last_g = group_seen_cache.get(chat.id, 0)
        upsert_group(chat.id, chat.title, chat.username, touch_seen=(now - last_g >= SEEN_UPDATE_COOLDOWN_SECONDS))
        if now - last_g >= SEEN_UPDATE_COOLDOWN_SECONDS:
            group_seen_cache[chat.id] = now
        last_u = user_seen_cache.get(user.id, 0)
        upsert_user(user.id, user.full_name, user.username, touch_seen=(now - last_u >= SEEN_UPDATE_COOLDOWN_SECONDS))
        if now - last_u >= SEEN_UPDATE_COOLDOWN_SECONDS:
            user_seen_cache[user.id] = now
        touch_member(chat.id, user.id, now)
        fed = get_chat_federation(chat.id)
        if fed and get_fed_ban(fed[0], user.id):
            try:
                await context.bot.ban_chat_member(chat.id, user.id)
            except Exception:
                pass
            return
        txt = msg.text or msg.caption or ''
        lower = txt.lower()
        if msg.text and msg.text.startswith('#') and len(msg.text.split()) == 1:
            row = get_note(chat.id, msg.text[1:].strip())
            if row:
                content, buttons_blob = row
                await msg.reply_text(content, reply_markup=build_keyboard(parse_buttons_blob(buttons_blob)))
                return
        lock_checks = {
            'all': True,
            'links': ('http://' in lower or 'https://' in lower or 't.me/' in lower),
            'media': bool(msg.photo or msg.video or msg.document or msg.audio or msg.sticker or msg.voice),
            'stickers': bool(msg.sticker),
            'gifs': bool(msg.animation),
            'polls': bool(msg.poll),
            'forwards': bool(msg.forward_origin),
            'bots': bool(user.is_bot),
            'voice': bool(msg.voice),
            'video_notes': bool(msg.video_note),
            'documents': bool(msg.document),
            'photos': bool(msg.photo),
            'videos': bool(msg.video),
            'audio': bool(msg.audio),
            'commands': bool(msg.text and msg.text.startswith('/')),
            'hashtags': '#' in lower,
            'mentions': '@' in lower,
            'inline': 'tg://user?id=' in lower,
            'contact': bool(msg.contact),
        }
        for key, matched in lock_checks.items():
            if matched and get_setting(chat.id, f'lock_{key}', 'off') == 'on':
                if await maybe_delete(msg):
                    log_admin_action(chat.id, user.id, f'lock_{key}_delete', user.id)
                    return
        for trigger, action in list_blacklists(chat.id):
            if trigger and trigger in lower:
                await maybe_delete(msg)
                try:
                    if action == 'warn':
                        add_warn(chat.id, user.id, f'Blacklist: {trigger}')
                    elif action == 'mute':
                        await context.bot.restrict_chat_member(chat.id, user.id, permissions=ChatPermissions(can_send_messages=False))
                    elif action == 'kick':
                        await context.bot.ban_chat_member(chat.id, user.id)
                        await context.bot.unban_chat_member(chat.id, user.id)
                    elif action == 'ban':
                        await context.bot.ban_chat_member(chat.id, user.id)
                except Exception:
                    pass
                log_admin_action(chat.id, user.id, 'blacklist_trigger', user.id, f'{trigger}->{action}')
                return
        key = (chat.id, user.id)
        dq = message_cache[key]
        dq.append(now)
        while dq and now - dq[0] > 8:
            dq.popleft()
        if len(dq) >= int(get_setting(chat.id, 'flood_limit', '5')):
            await maybe_delete(msg)
            action = get_setting(chat.id, 'flood_action', 'delete')
            duration = parse_duration_to_seconds(get_setting(chat.id, 'flood_time', '10m'))
            try:
                if action == 'warn':
                    add_warn(chat.id, user.id, 'Flood')
                elif action == 'mute':
                    await context.bot.restrict_chat_member(chat.id, user.id, permissions=ChatPermissions(can_send_messages=False))
                elif action == 'kick':
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await context.bot.unban_chat_member(chat.id, user.id)
                elif action == 'ban':
                    await context.bot.ban_chat_member(chat.id, user.id)
                elif action == 'tmute' and duration:
                    until = int(time.time()) + duration
                    await context.bot.restrict_chat_member(chat.id, user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
                    from store import add_temp_action
                    add_temp_action(chat.id, user.id, 'tmute', until)
                elif action == 'tban' and duration:
                    until = int(time.time()) + duration
                    await context.bot.ban_chat_member(chat.id, user.id, until_date=until)
                    from store import add_temp_action
                    add_temp_action(chat.id, user.id, 'tban', until)
            except Exception:
                pass
            log_admin_action(chat.id, user.id, 'flood_trigger', user.id, action)
            return
        quiz = active_quizzes.get(chat.id)
        if quiz and time.time() <= quiz.get('expires_at', 0) and not quiz.get('answered'):
            cleaned = ' '.join(txt.strip().lower().split())
            expected = quiz['answer']
            if cleaned and cleaned == expected:
                quiz['answered'] = True
                await msg.reply_text(random.choice(['Congratulations, that is right 🌸', 'Correct answer, well done ✨', 'Yay, that is correct 🌷']))
                try:
                    await context.bot.delete_message(chat_id=chat.id, message_id=quiz['message_id'])
                except Exception:
                    pass
                active_quizzes.pop(chat.id, None)
                return
        for keyword, reply in get_filters(chat.id):
            if keyword in lower:
                await msg.reply_text(reply)
                break


async def post_init(app: Application):
    app.bot_data['broadcast_runner'] = run_broadcast
    app.job_queue.run_repeating(hourly_quiz, interval=QUIZ_INTERVAL_SECONDS, first=30)



def ensure_event_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def main():
    ensure_event_loop()
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(error_handler)
    user_cmds = [
        ('start', wrapped_start, 'Users Commands', 'Start the bot', '/start'),
        ('help', help_cmd, 'Users Commands', 'Show all commands by category', '/help'),
        ('ping', ping_cmd, 'Users Commands', 'Check whether the bot is online', '/ping'),
        ('rules', rules_router, 'Users Commands', 'Show rules text', '/rules'),
        ('stats', stats_cmd, 'Users Commands', 'Show bot activity status', '/stats'),
        ('get', getnote_cmd, 'Users Commands', 'Get a saved note', '/get rules'),
        ('notes', notes_cmd, 'Users Commands', 'Show all saved note names', '/notes'),
        ('filters', filters_cmd, 'Users Commands', 'Show all active filter keywords', '/filters'),
        ('privacy', privacy_cmd, 'Users Commands', 'Show privacy text', '/privacy'),
        ('datadel', datadel_cmd, 'Owner Commands', 'Delete all stored data for a user ID', '/datadel 123456789'),
        ('addsupport', addsupport_cmd, 'Owner Commands', 'Set support group button link', '/addsupport https://t.me/your_support_group'),
        ('addchannel', addchannel_cmd, 'Owner Commands', 'Set update channel button link', '/addchannel https://t.me/your_update_channel'),
        ('addlogger', addlogger_cmd, 'Owner Commands', 'Save logger link reference for owner setup', '/addlogger https://t.me/your_logger_channel'),
        ('report', report_cmd, 'Users Commands', 'Report a replied message to admins', '/report spam'),
        ('id', id_cmd, 'Users Commands', 'Show your id or replied user id', '/id'),
        ('info', info_cmd, 'Users Commands', 'Show info about a user', '/info'),
        ('admins', admins_cmd, 'Users Commands', 'List admins', '/admins'),
        ('fedinfo', fedinfo_cmd, 'Users Commands', 'Show this chat federation', '/fedinfo'),
        ('fedstat', fedstat_cmd, 'Users Commands', 'Show fed info', '/fedstat'),
        ('blacklists', blacklists_cmd, 'Users Commands', 'List blacklists', '/blacklists'),
        ('warned', warned_cmd, 'Moderation Commands', 'List warned users (admins only)', '/warned'),
        ('locks', locks_cmd, 'Users Commands', 'Show lock status', '/locks'),
        ('myfeds', myfeds_cmd, 'Users Commands', 'List your federations', '/myfeds'),
    ]
    admin_cmds = [
        ('ban', ban_cmd, 'Group Management Commands', 'Reply-ban a user', '/ban'),
        ('unban', unban_cmd, 'Group Management Commands', 'Unban by user id', '/unban 1234'),
        ('kick', kick_cmd, 'Group Management Commands', 'Reply-kick a user', '/kick'),
        ('del', del_cmd, 'Group Management Commands', 'Delete replied message', '/del'),
        ('purge', purge_cmd, 'Group Management Commands', 'Purge from replied message to current', '/purge'),
        ('pin', pin_cmd, 'Group Management Commands', 'Pin replied message', '/pin'),
        ('unpin', unpin_cmd, 'Group Management Commands', 'Clear all pins', '/unpin'),
        ('mute', mute_cmd, 'Group Management Commands', 'Mute replied user, optional duration', '/mute 1h'),
        ('unmute', unmute_cmd, 'Group Management Commands', 'Unmute replied user', '/unmute'),
        ('warn', warn_cmd, 'Group Management Commands', 'Warn replied user', '/warn spam'),
        ('warns', warns_cmd, 'Moderation Commands', 'Show warns for replied user (admins only)', '/warns'),
        ('clearwarns', clearwarns_cmd, 'Group Management Commands', 'Clear warns for replied user', '/clearwarns'),
        ('warnlimit', warnlimit_cmd, 'Group Management Commands', 'Set warn limit', '/warnlimit 3'),
        ('warnaction', warnaction_cmd, 'Group Management Commands', 'Set warn action', '/warnaction tmute 1h'),
        ('save', note_cmd, 'Group Notes Commands', 'Save note, supports buttons', '/save rules | Be nice || Rules - https://example.com'),
        ('clear', clearnote_cmd, 'Group Notes Commands', 'Delete note', '/clear rules'),
        ('filter', filter_cmd, 'Group Notes Commands', 'Add filter', '/filter hi | hello'),
        ('stop', stop_cmd, 'Group Notes Commands', 'Delete filter', '/stop hi'),
        ('blacklist', blacklist_cmd, 'Moderation Commands', 'Add blacklist trigger', '/blacklist badword | warn'),
        ('rmblacklist', rmblacklist_cmd, 'Moderation Commands', 'Remove blacklist trigger', '/rmblacklist badword'),
        ('lock', lock_cmd, 'Moderation Commands', 'Lock content type', '/lock links'),
        ('unlock', unlock_cmd, 'Moderation Commands', 'Unlock content type', '/unlock links'),
        ('welcome', welcome_cmd, 'Moderation Commands', 'Manage welcome settings', '/welcome text Welcome {first}'),
        ('goodbye', goodbye_cmd, 'Moderation Commands', 'Manage goodbye settings', '/goodbye text Bye {fullname}'),
        ('flood', flood_cmd, 'Moderation Commands', 'Set antiflood', '/flood 6 tmute 10m'),
        ('setrules', setrules_cmd, 'Moderation Commands', 'Set rules text', '/setrules no spam'),
        ('rulesbtn', rulesbtn_cmd, 'Moderation Commands', 'Set rules buttons', '/rulesbtn Rules - https://example.com'),
        ('modlog', modlog_cmd, 'Moderation Commands', 'Show recent moderation logs (admins only)', '/modlog'),
        ('groupquota', groupquota_cmd, 'Help Sections', 'Show per-group quotas and retention caps', '/groupquota'),
        ('reports', reports_cmd, 'Moderation Commands', 'Enable or disable reports', '/reports on'),
        ('newfed', newfed_cmd, 'Federation Commands', 'Create federation', '/newfed myfed My Federation'),
        ('joinfed', joinfed_cmd, 'Federation Commands', 'Join chat to fed', '/joinfed myfed'),
        ('leavefed', leavefed_cmd, 'Federation Commands', 'Leave federation', '/leavefed'),
        ('fedadmin', fedadmin_cmd, 'Federation Commands', 'Add fed admin', '/fedadmin myfed 1234'),
        ('fedban', fedban_cmd, 'Federation Commands', 'Reply-fedban a user', '/fedban spammer'),
        ('unfedban', unfedban_cmd, 'Federation Commands', 'Unfedban a user id', '/unfedban 1234'),
        ('settitle', settitle_cmd, 'Utility Commands', 'Set chat title', '/settitle My Group'),
        ('setdesc', setdesc_cmd, 'Utility Commands', 'Set chat description', '/setdesc Group rules'),
        ('cleanservice', cleanservice_cmd, 'Utility Commands', 'Toggle service message cleaning', '/cleanservice on'),
        ('zombies', zombies_cmd, 'Utility Commands', 'Show zombie cleanup status', '/zombies'),
        ('addquiz', addquiz_cmd, 'Quiz Commands', 'Add quiz', '/addquiz Q ans. A'),
        ('showquiz', showquiz_cmd, 'Quiz Commands', 'Show saved quizzes', '/showquiz'),
        ('delquiz', delquiz_cmd, 'Quiz Commands', 'Delete saved quiz', '/delquiz 1'),
        ('broadcast', broadcast_cmd, 'Owner Commands', 'Broadcast to all groups', '/broadcast hello all'),
    ]
    for spec in user_cmds + admin_cmds:
        add_registered_command(app, *spec)
    app.add_handler(MessageHandler(filters.StatusUpdate.ALL, service_router))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.Sticker.ALL | filters.ANIMATION | filters.VOICE | filters.AUDIO | filters.CONTACT, message_router))
    if WEBHOOK_URL:
        url_path = WEBHOOK_PATH.lstrip('/')
        full_url = f"{WEBHOOK_URL.rstrip('/')}/{url_path}"
        kwargs = {
            'listen': '0.0.0.0',
            'port': PORT,
            'url_path': url_path,
            'webhook_url': full_url,
            'allowed_updates': Update.ALL_TYPES,
        }
        if WEBHOOK_SECRET:
            kwargs['secret_token'] = WEBHOOK_SECRET
        logger.info(f'Starting webhook server on 0.0.0.0:{PORT} pointing to {full_url}')
        app.run_webhook(**kwargs)
    else:
        logger.info('Starting bot in polling mode')
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
