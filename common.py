from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import OWNER_IDS, SUDO_USERS, PERSONA, SUPPORT_GROUP_URL, UPDATE_CHANNEL_URL
from store import get_global_link, set_global_link
from helpers import is_owner_or_sudo


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
        f"Hi, I am {PERSONA['name']} 🌸 A sweet college girl from {PERSONA['city']} who keeps groups tidy.\n\n"
        "Use the buttons below to read the privacy policy, browse command help by category, or join official support and updates.\n\n"
        "Data notice: the bot stores only operational moderation and settings data needed for features like warns, reports, notes, welcomes, logs, federation tools, and quiz/game state."
    )
    await update.message.reply_text(text, reply_markup=build_start_keyboard(registry))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registry = context.application.bot_data.get('help_registry', {})
    await update.message.reply_text(build_help_text(registry), reply_markup=build_help_category_keyboard(registry))


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Pong 🌙')


async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Be kind, avoid spam, and do not ask silly questions too often.')


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('I am watching the group quietly and doing my work just fine.')


async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔐 Privacy Policy for Yuuki\n\n"
        "What is stored: basic user and chat identifiers, moderation history such as warns and reports, configured notes/filters/welcome settings, button metadata, federation data, and limited audit logs needed to run the bot safely.\n\n"
        "Why it is stored: to provide moderation, anti-spam, automation, command help, federation controls, support troubleshooting, and abuse prevention.\n\n"
        "Who can act on deletion: the bot owners can process explicit deletion requests with /datadel for a specific user ID.\n\n"
        "Retention limits: some stored content is capped by per-group quotas and reports are trimmed by retention policy."
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
    link = update.message.text.partition(' ')[2].strip()
    if not _valid_public_link(link):
        await update.message.reply_text('Usage: /addlogger <https://t.me/your_logger_channel>')
        return
    set_global_link('logger_link', link)
    await update.message.reply_text('Logger link updated.')
