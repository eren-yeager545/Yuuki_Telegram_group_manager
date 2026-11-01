import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Chat, User, ChatPermissions, ChatJoinRequest, CallbackQuery
from telegram.error import TelegramError, TimedOut, NetworkError
from telegram.ext import ContextTypes

import store
from store import (
    init_db,
    set_setting,
    get_setting,
    add_blacklist,
    list_blacklists,
    get_warns,
    save_join_request_decision,
    get_join_request_decision,
    clear_join_request_decision
)
import bot
import admin
from admin import (
    autojoin_cmd,
    chat_join_request_handler,
    chat_join_request_callback_handler,
    is_user_currently_in_group
)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch, tmp_path):
    db_file = str(tmp_path / "test_four_issues.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()


# =========================================================================
# ISSUE 1: Random Message Deletion Audit & Exemption Tests
# =========================================================================

@pytest.mark.asyncio
async def test_admin_and_owner_exemption_from_locks_and_antiflood():
    """Group admins and owners must not have their messages deleted by locks or antiflood."""
    chat = Chat(id=-100111222333, type="supergroup")
    admin_user = User(id=8888, first_name="BossAdmin", is_bot=False)

    # Enable locks and tight antiflood
    set_setting(chat.id, "lock_links", "on")
    set_setting(chat.id, "lock_stickers", "on")
    set_setting(chat.id, "flood_limit", "1")

    msg = AsyncMock(spec=Message)
    msg.message_id = 1001
    msg.text = "Check this link: https://google.com"
    msg.caption = None
    msg.sticker = None
    msg.photo = None
    msg.video = None
    msg.animation = None
    msg.audio = None
    msg.voice = None
    msg.video_note = None
    msg.document = None
    msg.poll = None
    msg.forward_origin = None
    msg.contact = None
    msg.entities = []
    msg.caption_entities = []
    msg.sender_chat = None

    upd = MagicMock(spec=Update)
    upd.effective_chat = chat
    upd.effective_user = admin_user
    upd.effective_message = msg

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    # Mock is_target_admin in bot module returning True
    with patch("bot.is_target_admin", new_callable=AsyncMock) as mock_admin_check:
        mock_admin_check.return_value = True

        await bot.message_router(upd, ctx)
        # Message must NOT be deleted because user is an admin!
        msg.delete.assert_not_called()


@pytest.mark.asyncio
async def test_non_admin_message_deleted_when_lock_active():
    """Normal user messages are deleted when lock is enabled."""
    chat = Chat(id=-100111222333, type="supergroup")
    regular_user = User(id=4444, first_name="RegularMember", is_bot=False)

    set_setting(chat.id, "lock_links", "on")

    msg = AsyncMock(spec=Message)
    msg.message_id = 1002
    msg.text = "Check this link: https://google.com"
    msg.caption = None
    msg.sticker = None
    msg.photo = None
    msg.video = None
    msg.animation = None
    msg.audio = None
    msg.voice = None
    msg.video_note = None
    msg.document = None
    msg.poll = None
    msg.forward_origin = None
    msg.contact = None
    msg.entities = []
    msg.caption_entities = []
    msg.sender_chat = None

    upd = MagicMock(spec=Update)
    upd.effective_chat = chat
    upd.effective_user = regular_user
    upd.effective_message = msg

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    with patch("bot.is_target_admin", new_callable=AsyncMock) as mock_admin_check:
        mock_admin_check.return_value = False

        await bot.message_router(upd, ctx)
        msg.delete.assert_called_once()


# =========================================================================
# ISSUE 2: Auto Join-Request Approval Toggle Tests
# =========================================================================

@pytest.mark.asyncio
async def test_autojoin_toggle_admin_permission_and_persistence():
    chat = Chat(id=-100222333444, type="supergroup")
    admin_user = User(id=9999, first_name="GroupAdmin", is_bot=False)
    non_admin_user = User(id=1111, first_name="NonAdmin", is_bot=False)

    msg = AsyncMock(spec=Message)
    upd = MagicMock(spec=Update)
    upd.effective_chat = chat
    upd.effective_user = non_admin_user
    upd.message = msg
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # 1. Non-admin cannot toggle
    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = False
        await autojoin_cmd(upd, ctx)
        msg.reply_text.assert_called_with("Gomen ne~ 🌸 Only group admins can use this command desu! (⁠⁠◕⁠‿⁠◕⁠✿⁠)")
        assert get_setting(chat.id, "autojoin", "off") == "off"

    # 2. Admin queries status (default off)
    upd.effective_user = admin_user
    msg.reset_mock()
    ctx.args = []
    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True
        await autojoin_cmd(upd, ctx)
        assert "OFF 🔒" in msg.reply_text.call_args[0][0]

    # 3. Admin enables /autojoin on
    msg.reset_mock()
    ctx.args = ["on"]
    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True
        await autojoin_cmd(upd, ctx)
        assert get_setting(chat.id, "autojoin", "off") == "on"
        assert "is now <b>ON</b>" in msg.reply_text.call_args[0][0]

    # 4. Admin disables /autojoin off
    msg.reset_mock()
    ctx.args = ["off"]
    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True
        await autojoin_cmd(upd, ctx)
        assert get_setting(chat.id, "autojoin", "off") == "off"
        assert "is now <b>OFF</b>" in msg.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_autojoin_handler_auto_approves_when_enabled():
    chat = Chat(id=-100333444555, type="supergroup", title="Alpha Lounge")
    requester = User(id=77777, first_name="JoinRequester", username="j_requester", is_bot=False)

    set_setting(chat.id, "autojoin", "on")

    req = MagicMock(spec=ChatJoinRequest)
    req.chat = chat
    req.from_user = requester
    req.user = requester

    upd = MagicMock(spec=Update)
    upd.chat_join_request = req

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()

    with patch("logger_helper.send_logger_notification", new_callable=AsyncMock):
        await chat_join_request_handler(upd, ctx)

        # Bot should have called approve_chat_join_request automatically
        ctx.bot.approve_chat_join_request.assert_called_once_with(chat_id=chat.id, user_id=requester.id)
        # Decision must be saved in DB
        decision = get_join_request_decision(chat.id, requester.id)
        assert decision is not None
        assert decision[0] == "accepted"

        # Auto-approved message sent
        ctx.bot.send_message.assert_called_once()
        sent_text = ctx.bot.send_message.call_args[1]["text"]
        assert "Automatically approved via Auto-Join" in sent_text


# =========================================================================
# ISSUE 3: Blacklist Warning Notifications Tests
# =========================================================================

@pytest.mark.asyncio
async def test_blacklist_warning_notification_and_escalation():
    chat = Chat(id=-100444555666, type="supergroup")
    violator = User(id=66666, first_name="Violator", username="bad_user", is_bot=False)

    add_blacklist(chat.id, "forbidden_word", "warn")
    set_setting(chat.id, "warn_limit", "2")
    set_setting(chat.id, "warn_mode", "ban")

    msg = AsyncMock(spec=Message)
    msg.message_id = 2001
    msg.text = "This contains forbidden_word in text"
    msg.caption = None
    msg.sticker = None
    msg.photo = None
    msg.video = None
    msg.animation = None
    msg.audio = None
    msg.voice = None
    msg.video_note = None
    msg.document = None
    msg.poll = None
    msg.forward_origin = None
    msg.contact = None
    msg.entities = []
    msg.caption_entities = []
    msg.sender_chat = None

    upd = MagicMock(spec=Update)
    upd.effective_chat = chat
    upd.effective_user = violator
    upd.effective_message = msg

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    with patch("bot.is_target_admin", new_callable=AsyncMock) as mock_admin_check:
        mock_admin_check.return_value = False

        # 1st violation: count becomes 1/2, warning message sent, not banned
        await bot.message_router(upd, ctx)
        assert get_warns(chat.id, violator.id) == 1
        msg.delete.assert_called_once()
        bot_mock.send_message.assert_called_once()
        call_text = bot_mock.send_message.call_args[1]["text"]
        assert "Warning for" in call_text
        assert "forbidden_word" in call_text
        assert "1/2" in call_text
        bot_mock.ban_chat_member.assert_not_called()

        # 2nd violation: count reaches 2/2 -> escalation triggers ban
        msg.reset_mock()
        bot_mock.reset_mock()
        await bot.message_router(upd, ctx)
        assert get_warns(chat.id, violator.id) == 0  # Warns reset upon reaching limit
        msg.delete.assert_called_once()
        bot_mock.ban_chat_member.assert_called_once_with(chat.id, violator.id)


# =========================================================================
# ISSUE 4: Fix Stale Join-Request Approval Status Tests (5 Requirements)
# =========================================================================

@pytest.mark.asyncio
async def test_stale_cjr_scenario_1_previously_approved_still_in_group():
    """Scenario 1: Previously approved user still in the group -> alert already accepted."""
    chat_id = -100555666777
    user_id = 10001
    admin_user = User(id=999, first_name="Admin", is_bot=False)

    save_join_request_decision(chat_id, user_id, "accepted", admin_user.id)

    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{user_id}"
    query.from_user = admin_user

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    admin_member = MagicMock()
    admin_member.status = "administrator"
    target_member = MagicMock()
    target_member.status = "member"

    bot_mock.get_chat_member.side_effect = [admin_member, target_member]

    await chat_join_request_callback_handler(upd, ctx)

    bot_mock.approve_chat_join_request.assert_not_called()
    query.answer.assert_called_with("This join request has already been accepted!", show_alert=True)


@pytest.mark.asyncio
async def test_stale_cjr_scenario_2_previously_approved_leaves_and_requests_again():
    """Scenario 2: Previously approved user leaves and requests again -> allow re-approval."""
    chat_id = -100555666777
    user_id = 10002
    admin_user = User(id=999, first_name="Admin", is_bot=False)

    # Historical decision was accepted
    save_join_request_decision(chat_id, user_id, "accepted", admin_user.id)

    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{user_id}"
    query.from_user = admin_user
    query.message = AsyncMock(text="Join request notification")

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    admin_member = MagicMock()
    admin_member.status = "administrator"
    target_member = MagicMock()
    target_member.status = "left"  # User has left!

    bot_mock.get_chat_member.side_effect = [admin_member, target_member]

    await chat_join_request_callback_handler(upd, ctx)

    # Must approve new request!
    bot_mock.approve_chat_join_request.assert_called_once_with(chat_id=chat_id, user_id=user_id)
    query.answer.assert_called_with("Join request accepted! 💚")
    decision = get_join_request_decision(chat_id, user_id)
    assert decision is not None
    assert decision[0] == "accepted"


@pytest.mark.asyncio
async def test_stale_cjr_scenario_3_previously_approved_kicked_and_requests_again():
    """Scenario 3: Previously approved user is kicked and requests again -> allow re-approval."""
    chat_id = -100555666777
    user_id = 10003
    admin_user = User(id=999, first_name="Admin", is_bot=False)

    save_join_request_decision(chat_id, user_id, "accepted", admin_user.id)

    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{user_id}"
    query.from_user = admin_user
    query.message = AsyncMock(text="Join request notification")

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    admin_member = MagicMock()
    admin_member.status = "administrator"
    target_member = MagicMock()
    target_member.status = "kicked"  # User was kicked/banned!

    bot_mock.get_chat_member.side_effect = [admin_member, target_member]

    await chat_join_request_callback_handler(upd, ctx)

    bot_mock.approve_chat_join_request.assert_called_once_with(chat_id=chat_id, user_id=user_id)
    query.answer.assert_called_with("Join request accepted! 💚")


@pytest.mark.asyncio
async def test_stale_cjr_scenario_4_api_error_does_not_assume_left():
    """Scenario 4: Membership verification fails due to a Telegram API error -> do not assume user left."""
    chat_id = -100555666777
    user_id = 10004
    admin_user = User(id=999, first_name="Admin", is_bot=False)

    save_join_request_decision(chat_id, user_id, "accepted", admin_user.id)

    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{user_id}"
    query.from_user = admin_user

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    admin_member = MagicMock()
    admin_member.status = "administrator"

    # Admin check succeeds, target member check raises TimedOut
    bot_mock.get_chat_member.side_effect = [admin_member, TimedOut("Telegram timed out")]

    await chat_join_request_callback_handler(upd, ctx)

    bot_mock.approve_chat_join_request.assert_not_called()
    query.answer.assert_called_with("Could not verify user's current membership status due to a Telegram error. Please try again.", show_alert=True)
    # Decision must NOT have been cleared
    assert get_join_request_decision(chat_id, user_id) is not None


@pytest.mark.asyncio
async def test_stale_cjr_scenario_5_different_membership_statuses_in_different_groups():
    """Scenario 5: The same user has different membership statuses in different groups."""
    chat_a = -100111111111
    chat_b = -100222222222
    user_id = 10005
    admin_user = User(id=999, first_name="Admin", is_bot=False)

    # In chat A, user was accepted and is still a member
    save_join_request_decision(chat_a, user_id, "accepted", admin_user.id)
    # In chat B, user was accepted but left
    save_join_request_decision(chat_b, user_id, "accepted", admin_user.id)

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot_mock = AsyncMock()
    ctx.bot = bot_mock

    # Chat A: user is still member
    mem_a = MagicMock(status="member")
    # Chat B: user has left
    mem_b = MagicMock(status="left")

    bot_mock.get_chat_member.side_effect = lambda cid, uid: mem_a if cid == chat_a else mem_b

    is_in_a, _ = await is_user_currently_in_group(chat_a, user_id, ctx)
    is_in_b, _ = await is_user_currently_in_group(chat_b, user_id, ctx)

    assert is_in_a is True
    assert is_in_b is False

    # Clearing stale decision in chat B must not affect chat A
    clear_join_request_decision(chat_b, user_id)
    assert get_join_request_decision(chat_a, user_id) is not None
    assert get_join_request_decision(chat_b, user_id) is None
