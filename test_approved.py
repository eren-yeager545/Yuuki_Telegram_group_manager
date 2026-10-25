import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Chat, User, ChatPermissions, MessageEntity
from telegram.ext import ContextTypes, ApplicationHandlerStop

import store
from store import (
    init_db,
    add_approved_user,
    remove_approved_user,
    is_approved_user,
    get_approved_users,
    set_setting,
    add_blacklist,
)
from admin import approve_cmd, unapprove_cmd, approved_cmd, info_cmd
from bot import message_router, night_mode_message_handler, process_night_mode


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_approved_user_store_isolation():
    chat_id_1 = -1001111111111
    chat_id_2 = -1002222222222
    user_id = 987654321

    # Initially not approved in either group
    assert is_approved_user(chat_id_1, user_id) is False
    assert is_approved_user(chat_id_2, user_id) is False
    assert get_approved_users(chat_id_1) == []

    # Add to group 1
    assert add_approved_user(chat_id_1, user_id) is True
    assert is_approved_user(chat_id_1, user_id) is True
    assert is_approved_user(chat_id_2, user_id) is False  # Per-group isolation
    assert get_approved_users(chat_id_1) == [user_id]
    assert get_approved_users(chat_id_2) == []

    # Remove from group 1
    assert remove_approved_user(chat_id_1, user_id) is True
    assert is_approved_user(chat_id_1, user_id) is False


@pytest.mark.asyncio
async def test_approve_unapprove_approved_commands_and_permissions():
    chat = Chat(id=-100123456789, type="supergroup")
    admin_user = User(id=1001, first_name="AdminUser", is_bot=False, username="admin_guy")
    norm_user = User(id=1002, first_name="NormalUser", is_bot=False)
    target_user = User(id=2001, first_name="TargetUser", is_bot=False)  # User without username

    # Non-admin attempting /approve
    upd_non_admin = MagicMock(spec=Update)
    msg_non_admin = AsyncMock(spec=Message)
    upd_non_admin.effective_chat = chat
    upd_non_admin.effective_user = norm_user
    upd_non_admin.effective_message = msg_non_admin
    upd_non_admin.message = msg_non_admin
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = False
        await approve_cmd(upd_non_admin, ctx)
        msg_non_admin.reply_text.assert_called_with("Gomen ne~ 🌸 Only group admins can approve users desu! (⁠⁠◕⁠‿⁠◕⁠✿⁠)")

    # Admin executing /approve on user without username
    upd_admin = MagicMock(spec=Update)
    msg_admin = AsyncMock(spec=Message)
    upd_admin.effective_chat = chat
    upd_admin.effective_user = admin_user
    upd_admin.effective_message = msg_admin
    upd_admin.message = msg_admin

    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin, \
         patch("admin.resolve_target_user", new_callable=AsyncMock) as mock_resolve:
        mock_is_admin.return_value = True
        mock_resolve.return_value = target_user

        await approve_cmd(upd_admin, ctx)
        assert is_approved_user(chat.id, target_user.id) is True
        msg_admin.reply_html.assert_called()
        assert "User Approved" in msg_admin.reply_html.call_args[0][0]

        # Duplicate approve attempt
        await approve_cmd(upd_admin, ctx)
        assert "already approved" in msg_admin.reply_html.call_args[0][0]

        # /approved list command
        msg_admin.reply_html.reset_mock()
        await approved_cmd(upd_admin, ctx)
        approved_text = msg_admin.reply_html.call_args[0][0]
        assert "Approved Users List" in approved_text
        assert str(target_user.id) in approved_text
        assert "TargetUser" in approved_text

        # /unapprove command
        msg_admin.reply_html.reset_mock()
        await unapprove_cmd(upd_admin, ctx)
        assert is_approved_user(chat.id, target_user.id) is False
        assert "Approval Revoked" in msg_admin.reply_html.call_args[0][0]


@pytest.mark.asyncio
async def test_info_cmd_display():
    chat = Chat(id=-100987654321, type="supergroup")
    caller_user = User(id=1001, first_name="Caller", is_bot=False)

    # Target 1: No username, approved, member
    target_1 = User(id=3001, first_name="NoName", is_bot=False)
    add_approved_user(chat.id, target_1.id)

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = caller_user
    upd.effective_message = msg
    upd.message = msg
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot = AsyncMock()
    ctx.bot = bot

    member_mock = MagicMock()
    member_mock.status = "member"
    bot.get_chat_member.return_value = member_mock

    with patch("admin.resolve_target_user", new_callable=AsyncMock) as mock_resolve, \
         patch("admin.is_bot_or_group_owner", new_callable=AsyncMock) as mock_is_owner:
        mock_resolve.return_value = target_1
        mock_is_owner.return_value = False

        await info_cmd(upd, ctx)
        info_text = msg.reply_html.call_args[0][0]
        assert "ID: 3001" in info_text
        assert "Username: Not set" in info_text
        assert "Administrator: No" in info_text
        assert "Group Owner: No" in info_text
        assert "Approved status: Yes" in info_text

    # Target 2: Has username, admin & group owner, not approved
    target_2 = User(id=3002, first_name="OwnerGuy", username="the_boss", is_bot=False)
    member_mock.status = "creator"

    with patch("admin.resolve_target_user", new_callable=AsyncMock) as mock_resolve, \
         patch("admin.is_bot_or_group_owner", new_callable=AsyncMock) as mock_is_owner:
        mock_resolve.return_value = target_2
        mock_is_owner.return_value = True

        msg.reply_html.reset_mock()
        await info_cmd(upd, ctx)
        info_text_2 = msg.reply_html.call_args[0][0]
        assert "ID: 3002" in info_text_2
        assert "Username: @the_boss" in info_text_2
        assert "Administrator: Yes" in info_text_2
        assert "Group Owner: Yes" in info_text_2
        assert "Approved status: No" in info_text_2


@pytest.mark.asyncio
async def test_approved_user_bypasses_locks_blacklists_antiflood():
    chat = Chat(id=-100555444333, type="supergroup")
    normal_user = User(id=4001, first_name="Normal", is_bot=False)
    approved_user = User(id=4002, first_name="Approved", is_bot=False)

    add_approved_user(chat.id, approved_user.id)

    # Enable sticker lock, link lock, blacklisted word, antiflood
    set_setting(chat.id, "lock_stickers", "on")
    set_setting(chat.id, "lock_links", "on")
    set_setting(chat.id, "lock_gifs", "on")
    set_setting(chat.id, "flood_limit", "2")
    add_blacklist(chat.id, "badphrase", "ban")

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Helper to build mock message
    def make_msg(mid, text=None, sticker=None):
        m = AsyncMock(spec=Message)
        m.message_id = mid
        m.text = text
        m.caption = None
        m.sticker = sticker
        m.photo = None
        m.video = None
        m.animation = None
        m.audio = None
        m.voice = None
        m.video_note = None
        m.document = None
        m.poll = None
        m.forward_origin = None
        m.contact = None
        m.entities = []
        m.caption_entities = []
        m.new_chat_members = None
        m.left_chat_member = None
        m.story = None
        m.paid_media = None
        m.sender_chat = None
        return m

    # 1. Test Sticker / GIF / Link Lock
    # Normal user sending sticker
    stk_mock = MagicMock()
    stk_mock.is_animated = False
    stk_mock.is_video = False
    msg_normal_sticker = make_msg(101, sticker=stk_mock)

    upd_normal = MagicMock(spec=Update)
    upd_normal.effective_chat = chat
    upd_normal.effective_user = normal_user
    upd_normal.effective_message = msg_normal_sticker

    await message_router(upd_normal, ctx)
    msg_normal_sticker.delete.assert_called_once()  # Deleted due to lock_stickers

    # Approved user sending sticker
    msg_approved_sticker = make_msg(102, sticker=stk_mock)

    upd_approved = MagicMock(spec=Update)
    upd_approved.effective_chat = chat
    upd_approved.effective_user = approved_user
    upd_approved.effective_message = msg_approved_sticker

    await message_router(upd_approved, ctx)
    msg_approved_sticker.delete.assert_not_called()  # Bypassed!

    # 2. Test Blacklisted word
    msg_approved_bad = make_msg(103, text="Here is a badphrase in text")

    upd_approved.effective_message = msg_approved_bad
    await message_router(upd_approved, ctx)
    msg_approved_bad.delete.assert_not_called()  # Bypassed!

    # 3. Test Antiflood
    msg_approved_flood = make_msg(104, text="Hello regular message")
    upd_approved.effective_message = msg_approved_flood

    # Send 5 messages (flood limit is 2)
    for i in range(5):
        msg_approved_flood.message_id = 105 + i
        await message_router(upd_approved, ctx)

    msg_approved_flood.delete.assert_not_called()  # Bypassed!


@pytest.mark.asyncio
async def test_approved_user_still_affected_by_night_mode():
    chat = Chat(id=-100888777666, type="supergroup")
    approved_user = User(id=5001, first_name="ApprovedUser", is_bot=False)

    add_approved_user(chat.id, approved_user.id)
    set_setting(chat.id, "night_mode", "on")

    # Approved user sends sticker during Night Mode
    stk_mock = MagicMock()
    stk_mock.is_animated = False
    stk_mock.is_video = False

    msg_sticker = AsyncMock(spec=Message)
    msg_sticker.message_id = 999
    msg_sticker.sticker = stk_mock
    msg_sticker.photo = None
    msg_sticker.video = None
    msg_sticker.animation = None
    msg_sticker.audio = None
    msg_sticker.voice = None
    msg_sticker.video_note = None
    msg_sticker.document = None
    msg_sticker.text = None
    msg_sticker.caption = None
    msg_sticker.entities = []
    msg_sticker.caption_entities = []
    msg_sticker.sender_chat = None
    msg_sticker.is_automatic_forward = False
    msg_sticker.forward_origin = None
    msg_sticker.story = None
    msg_sticker.paid_media = None

    upd = MagicMock(spec=Update)
    upd.edited_message = None
    upd.message = msg_sticker
    upd.effective_chat = chat
    upd.effective_user = approved_user
    upd.effective_message = msg_sticker

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    bot = AsyncMock()
    bot.get_chat_administrators.return_value = []
    ctx.bot = bot

    # Night mode MUST still process and delete approved user's message!
    deleted = await process_night_mode(upd, ctx)
    assert deleted is True
    msg_sticker.delete.assert_called_once()

    # Test night_mode_message_handler stops propagation via ApplicationHandlerStop
    with pytest.raises(ApplicationHandlerStop):
        await night_mode_message_handler(upd, ctx)


@pytest.mark.asyncio
async def test_approval_does_not_grant_admin_or_bypass_bans():
    chat = Chat(id=-100333222111, type="supergroup")
    approved_user = User(id=6001, first_name="ApprovedUser", is_bot=False)

    add_approved_user(chat.id, approved_user.id)

    upd = MagicMock(spec=Update)
    upd.effective_chat = chat
    upd.effective_user = approved_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # 1. Admin permission check fails for approved user (Approval does NOT grant admin rights)
    member_mock = MagicMock()
    member_mock.status = "member"
    bot = AsyncMock()
    bot.get_chat_member.return_value = member_mock
    ctx.bot = bot

    from helpers import is_admin
    with patch("helpers.is_owner_or_sudo", return_value=False):
        assert await is_admin(upd, ctx) is False

    # 2. Federation ban check is NOT bypassed by approval
    with patch("bot.get_chat_federation", return_value=("test_fed",)), \
         patch("bot.get_fed_ban", return_value=("Spammer",)):
        msg_fed = AsyncMock(spec=Message)
        msg_fed.message_id = 701
        msg_fed.text = "Hello"
        msg_fed.caption = None
        upd.effective_message = msg_fed

        await message_router(upd, ctx)
        bot.ban_chat_member.assert_called_once_with(chat.id, approved_user.id)
