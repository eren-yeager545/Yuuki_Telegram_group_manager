import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_IDS, SUDO_USERS, SUPPORT_GROUP_URL, UPDATE_CHANNEL_URL
from store import (
    get_global_link, set_global_link, get_active_group_count, get_user_count,
    is_mongo, conn, get_setting
)
from helpers import is_owner_or_sudo

BOT_START_TIME = time.time()


def format_uptime(seconds: float) -> str:
    secs = int(seconds)
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    minutes, secs = divmod(secs, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def build_help_text(registry: dict) -> str:
    order = [
        'Users Commands',
        'Moderation Commands',
        'Group Management Commands',
        'Group Notes Commands',
        'Help Sections',
        'Game Commands',
        'Owner Commands',
        'Owner/Sudo Commands',
    ]
    lines = ['🌸 Yuuki Full Help Menu', '']
    for category in order:
        cmds = registry.get(category, [])
        if not cmds:
            continue
        lines.append(category)
        for item in cmds:
            lines.append(f"{item['command']} - {item['usage']}")
            lines.append(f"Example: {item['example']}")
            lines.append('')
    lines.append('Only commands marked owner, sudo, or admin-only may be used by those roles.')
    return '\n'.join(lines)


def build_help_category_keyboard(registry: dict):
    rows = []
    for category in sorted(registry.keys()):
        key = f'helpcat:{category}'[:64]
        rows.append([InlineKeyboardButton(category, callback_data=key)])
    return InlineKeyboardMarkup(rows) if rows else None


def build_start_keyboard(registry: dict):
    support_url = get_global_link('support_group_url', SUPPORT_GROUP_URL)
    channel_url = get_global_link('update_channel_url', UPDATE_CHANNEL_URL)
    rows = [[InlineKeyboardButton('Help', callback_data='home:help'), InlineKeyboardButton('Privacy Policy', callback_data='home:privacy')]]
    rows.append([InlineKeyboardButton('Support Group', url=support_url), InlineKeyboardButton('Update Channel', url=channel_url)])
    return InlineKeyboardMarkup(rows)


def build_category_help_text(registry: dict, category: str) -> str:
    items = registry.get(category, [])
    lines = [f'📚 {category}', '']
    for item in items:
        lines.append(f"{item['command']}\nUse: {item['usage']}\nExample: {item['example']}\n")
    return '\n'.join(lines) if items else 'No commands in this category.'


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registry = context.application.bot_data.get('help_registry', {})
    text = (
        "Konnichiwa! 🌸 I'm Yuki, a 17-year-old college student from Tokyo! ✨ I'm super excited to help keep your group active, clean, and happy! 🎀\n\n"
        "Use the buttons below to browse my command menu, read our privacy policy, or join our official support family! 💕\n\n"
        "Data notice: I only store essential operational moderation and setting details (like warns, notes, rules, welcomes, and logs) to keep your group safe desu~ (⁠人⁠*⁠´⁠∀⁠｀⁠)"
    )
    await update.message.reply_text(text, reply_markup=build_start_keyboard(registry))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registry = context.application.bot_data.get('help_registry', {})
    await update.message.reply_text(build_help_text(registry), reply_markup=build_help_category_keyboard(registry))


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.time()
    msg = await update.message.reply_text("🏓 Measuring ping desu~ ✨")
    elapsed = (time.time() - start) * 1000
    uptime_str = format_uptime(time.time() - BOT_START_TIME)

    text = (
        f"Pong! 🏓✨\n\n"
        f"⚡ Latency: {int(elapsed)} ms\n"
        f"⏱ Uptime: {uptime_str}\n\n"
        f"Yuki is super energetic and ready to help! (⁠≧⁠▽⁠≦⁠)🌸"
    )
    await msg.edit_text(text)


async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    custom_rules = get_setting(chat.id, 'rules_text', '') if chat else ''
    if custom_rules:
        await update.message.reply_text(custom_rules)
    else:
        await update.message.reply_text("🌸 Group Rules 🌸\n\nNo rules configured for this group yet! Be super sweet to everyone and enjoy your time here desu~ 💕 (⁠人⁠*⁠´⁠∀⁠｀⁠)")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.time()
    db_connected = False
    try:
        if is_mongo():
            from store import get_mongo_db
            db = get_mongo_db()
            db.command('ping')
            db_connected = True
        else:
            with conn() as c:
                c.execute("SELECT 1")
            db_connected = True
    except Exception:
        db_connected = False

    latency = int((time.time() - start) * 1000)
    groups_count = get_active_group_count()
    users_count = get_user_count()
    uptime_str = format_uptime(time.time() - BOT_START_TIME)
    db_status = "Connected ✨" if db_connected else "Disconnected 🥺"

    text = (
        "📊 Yuki's Status Report ~ 🌸\n\n"
        f"👥 Groups Served: {groups_count}\n"
        f"👤 Cute Members: {users_count}\n\n"
        f"⏱ Bot Uptime: {uptime_str}\n"
        f"🗄 Database: {db_status}\n"
        f"🏓 Latency: {latency} ms\n\n"
        "Everything is running smoothly desu! ✨ (⁠≧⁠∇⁠≦⁠)/"
    )
    await update.message.reply_text(text)


async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_url = get_global_link('support_group_url', SUPPORT_GROUP_URL)
    text = (
        "📦 Data Storage\n"
        "We store minimal operational data required for moderation and group management, including Telegram user IDs, chat IDs, custom notes, filters, group rules, moderation logs, warning counters, and federation memberships.\n\n"
        "🍪 Cookies\n"
        "The bot and its inline web features do not use cookies.\n\n"
        "📥 Collection\n"
        "Information is collected automatically when users interact with the bot in groups or private messages (such as issuing commands or triggering moderation events).\n\n"
        "⚙️ Usage\n"
        "Collected information is strictly used to process group management settings, execute moderation actions (warns, mutes, bans), enforce blacklists, display logs, and deliver automated responses.\n\n"
        "🤝 Sharing\n"
        "We do not sell, rent, or share user or chat data with any third parties.\n\n"
        "🔐 Security\n"
        "All data is stored securely using parameterized database queries and protected access configurations.\n\n"
        "🔗 Third Parties\n"
        "The bot operates directly on Telegram APIs and does not export data to external marketing or analytics services.\n\n"
        "🗑 Data Deletion\n"
        "Group administrators can clear notes/filters at any time. Users or administrators can request full removal of stored data for a user ID using the /datadel command.\n\n"
        "🔄 Updates\n"
        "This privacy policy may be updated periodically to reflect new features or regulatory requirements.\n\n"
        "📩 Contact\n"
        f"For questions or data deletion inquiries, contact support at: {support_url}"
    )
    await update.message.reply_text(text)


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner_or_sudo(user.id, OWNER_IDS, SUDO_USERS):
        await update.message.reply_text('Only owner or sudo can use broadcast.')
        return
    message = update.message.text.partition(' ')[2].strip()
    if not message:
        await update.message.reply_text('Usage: /broadcast your message')
        return
    runner = context.application.bot_data.get('broadcast_runner')
    if not runner:
        await update.message.reply_text('Broadcast system is unavailable right now.')
        return
    await runner(message, context)
    await update.message.reply_text('Broadcast finished.')


def _valid_public_link(value: str) -> bool:
    return bool(value) and value.startswith(('http://', 'https://', 'https://t.me/', 'http://t.me/'))


async def addsupport_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner_or_sudo(user.id, OWNER_IDS, SUDO_USERS):
        await update.message.reply_text('Owner only command.')
        return
    link = update.message.text.partition(' ')[2].strip()
    if not _valid_public_link(link):
        await update.message.reply_text('Usage: /addsupport <https://t.me/your_support_group>')
        return
    set_global_link('support_group_url', link)
    await update.message.reply_text('Support group link updated.')


async def addchannel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner_or_sudo(user.id, OWNER_IDS, SUDO_USERS):
        await update.message.reply_text('Owner only command.')
        return
    link = update.message.text.partition(' ')[2].strip()
    if not _valid_public_link(link):
        await update.message.reply_text('Usage: /addchannel <https://t.me/your_update_channel>')
        return
    set_global_link('update_channel_url', link)
    await update.message.reply_text('Update channel link updated.')


async def addlogger_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner_or_sudo(user.id, OWNER_IDS, SUDO_USERS):
        await update.message.reply_text('Owner only command.')
        return
    arg = update.message.text.partition(' ')[2].strip()
    if not arg:
        await update.message.reply_text('Usage: /addlogger <channel_id | @channel_username | link>')
        return
    set_global_link('logger_channel_id', arg)
    set_global_link('logger_link', arg)
    await update.message.reply_text('Logger channel reference updated.')


if __name__ == '__main__':
    from bot import main
    main()
