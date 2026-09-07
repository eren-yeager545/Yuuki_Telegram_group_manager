import asyncio
import time
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import RetryAfter, Forbidden, BadRequest, TelegramError
from config import OWNER_IDS
from helpers import is_owner
from store import (
    get_all_user_ids, get_active_groups, set_group_inactive, delete_user_data
)
from logger_helper import send_logger_notification, format_utc_now, get_bot_name, DIVIDER

logger = logging.getLogger(__name__)

# Session state dictionary for active broadcast configuration per owner
BROADCAST_SESSIONS = {}


def build_target_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("👤 Users", callback_data="bcast_target:users"),
            InlineKeyboardButton("👥 Groups", callback_data="bcast_target:groups")
        ],
        [
            InlineKeyboardButton("🌐 Users + Groups", callback_data="bcast_target:both")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="bcast_target:cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_stop_keyboard():
    keyboard = [
        [InlineKeyboardButton("🛑 Stop Broadcast", callback_data="bcast_action:stop")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        if update.message:
            await update.message.reply_text("Gomen ne~ 🌸 Only the Bot Owner can use /broadcast desu! (⁠◕⁠‿⁠◕⁠✿⁠)")
        return

    session = BROADCAST_SESSIONS.get(user.id)
    if session and session.get("status") == "broadcasting":
        await update.message.reply_text("⚠️ A broadcast is already currently in progress desu! Please wait for it to complete or stop it.")
        return

    session_id = f"bcast_{user.id}_{int(time.time())}"
    BROADCAST_SESSIONS[user.id] = {
        "status": "selecting_target",
        "session_id": session_id,
        "target_type": None,
        "stop_requested": False
    }

    text = "📢 Choose Broadcast Type"
    await update.message.reply_text(text, reply_markup=build_target_keyboard())


async def broadcast_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user or not is_owner(user.id, OWNER_IDS):
        await query.answer("❌ Only the Bot Owner can perform broadcast actions.", show_alert=True)
        return

    data = query.data or ""

    if data.startswith("bcast_target:"):
        await query.answer()
        target_choice = data.split(":", 1)[1]
        session = BROADCAST_SESSIONS.get(user.id)

        if not session or session.get("status") != "selecting_target":
            await query.edit_message_text("❌ Session expired or invalid broadcast state.")
            return

        if target_choice == "cancel":
            BROADCAST_SESSIONS.pop(user.id, None)
            await query.edit_message_text("❌ Broadcast cancelled.")
            return

        session["target_type"] = target_choice
        session["status"] = "waiting_content"

        type_labels = {
            "users": "👤 Users",
            "groups": "👥 Groups",
            "both": "🌐 Users + Groups"
        }
        chosen_label = type_labels.get(target_choice, target_choice)
        await query.edit_message_text(f"Selected: {chosen_label}\n\n«📝 Send the message you want to broadcast.»")

    elif data == "bcast_action:stop":
        session = BROADCAST_SESSIONS.get(user.id)
        if session and session.get("status") == "broadcasting":
            session["stop_requested"] = True
            await query.answer("🛑 Stopping broadcast...", show_alert=True)
        else:
            await query.answer("No active broadcast to stop.", show_alert=True)


async def handle_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not is_owner(user.id, OWNER_IDS):
        return False

    session = BROADCAST_SESSIONS.get(user.id)
    if not session or session.get("status") != "waiting_content":
        return False

    if update.effective_chat and update.effective_chat.type != 'private':
        return False

    target_type = session.get("target_type")
    session["status"] = "broadcasting"
    session["from_chat_id"] = msg.chat_id
    session["from_message_id"] = msg.message_id

    asyncio.create_task(execute_broadcast(context, user.id, msg.chat_id, msg.message_id, target_type))
    return True


def format_duration(seconds: float) -> str:
    secs = int(seconds)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


async def log_broadcast_event(context: ContextTypes.DEFAULT_TYPE, owner_id: int, target_type: str, start_time: float, end_time: float, stats: dict, cancelled: bool):
    bot_name = get_bot_name(context)
    now_str = format_utc_now()
    duration_str = format_duration(end_time - start_time)

    status_str = "CANCELLED 🛑" if cancelled else "COMPLETED ✅"

    body = (
        f"👤 Initiator: Owner ({owner_id})\n"
        f"🎯 Target Type: {target_type.upper()}\n"
        f"📌 Status: {status_str}\n\n"
        f"👤 USERS\n"
        f"Total: {stats['users_total']:,}\n"
        f"✅ Sent: {stats['users_sent']:,}\n"
        f"❌ Failed: {stats['users_failed']:,}\n"
        f"🚫 Blocked/Deactivated: {stats['users_blocked']:,}\n\n"
        f"👥 GROUPS\n"
        f"Total: {stats['groups_total']:,}\n"
        f"✅ Sent: {stats['groups_sent']:,}\n"
        f"❌ Failed: {stats['groups_failed']:,}\n"
        f"🚪 Removed/Restricted: {stats['groups_removed']:,}\n\n"
        f"⏱ Duration: {duration_str}"
    )

    msg = (
        f"{DIVIDER}\n"
        f"📢 BROADCAST LOG ({status_str})\n"
        f"{DIVIDER}\n"
        f"{body}\n"
        f"{DIVIDER}\n"
        f"🤖 Bot: {bot_name}\n"
        f"🕐 {now_str}\n"
        f"{DIVIDER}"
    )
    await send_logger_notification(context, msg)


async def send_single_recipient(bot, chat_id: int, from_chat_id: int, message_id: int):
    # Sends single message preserving media, formatting, entities, captions
    try:
        await bot.copy_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id)
        return "success", None
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            await bot.copy_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id)
            return "success", None
        except Exception as retry_err:
            return "failed", retry_err
    except Forbidden as e:
        err_msg = str(e).lower()
        if "bot was blocked by the user" in err_msg or "user is deactivated" in err_msg or "bot was kicked" in err_msg:
            return "blocked", e
        return "failed", e
    except BadRequest as e:
        err_msg = str(e).lower()
        if "chat not found" in err_msg or "have no rights" in err_msg or "not a member" in err_msg or "kicked" in err_msg:
            return "blocked", e
        return "failed", e
    except Exception as e:
        return "failed", e


async def execute_broadcast(context: ContextTypes.DEFAULT_TYPE, owner_id: int, from_chat_id: int, message_id: int, target_type: str):
    session = BROADCAST_SESSIONS.get(owner_id)
    if not session:
        return

    start_time = time.time()
    bot = context.bot

    # Recipient lists
    user_ids = get_all_user_ids() if target_type in ("users", "both") else []
    active_groups_raw = get_active_groups() if target_type in ("groups", "both") else []
    group_ids = [g[0] for g in active_groups_raw]

    # Deduplication
    user_ids = list(dict.fromkeys(user_ids))
    group_ids = list(dict.fromkeys(group_ids))

    stats = {
        "users_total": len(user_ids),
        "users_sent": 0,
        "users_failed": 0,
        "users_blocked": 0,
        "groups_total": len(group_ids),
        "groups_sent": 0,
        "groups_failed": 0,
        "groups_removed": 0
    }

    # Initial Progress Message
    progress_msg = await bot.send_message(
        chat_id=owner_id,
        text="📢 Broadcast in progress...\n\n"
             f"👤 Users: 0 / {stats['users_total']:,}\n"
             f"👥 Groups: 0 / {stats['groups_total']:,}\n\n"
             f"✅ Successful: 0\n"
             f"❌ Failed: 0",
        reply_markup=build_stop_keyboard()
    )

    last_update_time = time.time()

    async def update_progress_if_needed(force=False):
        nonlocal last_update_time
        now = time.time()
        if force or (now - last_update_time >= 3.0):
            last_update_time = now
            u_processed = stats['users_sent'] + stats['users_failed']
            g_processed = stats['groups_sent'] + stats['groups_failed']
            total_success = stats['users_sent'] + stats['groups_sent']
            total_failed = stats['users_failed'] + stats['groups_failed']

            prog_text = (
                "📢 Broadcast in progress...\n\n"
                f"👤 Users: {u_processed:,} / {stats['users_total']:,}\n"
                f"👥 Groups: {g_processed:,} / {stats['groups_total']:,}\n\n"
                f"✅ Successful: {total_success:,}\n"
                f"❌ Failed: {total_failed:,}"
            )
            try:
                await progress_msg.edit_text(prog_text, reply_markup=build_stop_keyboard())
            except Exception:
                pass

    # Delivery Loop for Users
    for uid in user_ids:
        if session.get("stop_requested"):
            break

        status, err = await send_single_recipient(bot, uid, from_chat_id, message_id)
        if status == "success":
            stats["users_sent"] += 1
        elif status == "blocked":
            stats["users_failed"] += 1
            stats["users_blocked"] += 1
        else:
            stats["users_failed"] += 1

        await update_progress_if_needed()
        await asyncio.sleep(0.05)  # Rate limiting pause (~20 msg/sec max safe pace)

    # Delivery Loop for Groups
    for gid in group_ids:
        if session.get("stop_requested"):
            break

        status, err = await send_single_recipient(bot, gid, from_chat_id, message_id)
        if status == "success":
            stats["groups_sent"] += 1
        elif status == "blocked":
            stats["groups_failed"] += 1
            stats["groups_removed"] += 1
            set_group_inactive(gid, current_bot_status="kicked")
        else:
            stats["groups_failed"] += 1

        await update_progress_if_needed()
        await asyncio.sleep(0.1)  # Rate limiting pause (~10 msg/sec max safe pace for groups)

    end_time = time.time()
    duration_str = format_duration(end_time - start_time)
    was_stopped = session.get("stop_requested", False)

    # Completion / Partial Stop Message
    header = "📢 Broadcast Stopped (Partial Results)" if was_stopped else "📢 Broadcast Completed"
    final_text = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"👤 USERS\n"
        f"Total: {stats['users_total']:,}\n"
        f"✅ Sent: {stats['users_sent']:,}\n"
        f"❌ Failed: {stats['users_failed']:,}\n\n"
        f"👥 GROUPS\n"
        f"Total: {stats['groups_total']:,}\n"
        f"✅ Sent: {stats['groups_sent']:,}\n"
        f"❌ Failed: {stats['groups_failed']:,}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"⏱ Time: {duration_str}"
    )

    try:
        await progress_msg.edit_text(final_text)
    except Exception:
        await bot.send_message(chat_id=owner_id, text=final_text)

    # Log to logger system
    await log_broadcast_event(context, owner_id, target_type, start_time, end_time, stats, was_stopped)

    # Clean up session
    BROADCAST_SESSIONS.pop(owner_id, None)
