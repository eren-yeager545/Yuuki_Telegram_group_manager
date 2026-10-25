import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Chat, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from store import init_db, add_approved_user, upsert_user
from admin import info_cmd, resolve_target_user


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


@pytest.mark.asyncio
async def test_info_cmd_sender_fallback():
    """Test /info with no args and no reply shows info about the sender."""
    chat = Chat(id=-100123456789, type="supergroup")
    sender = User(id=111, first_name="SenderUser", username="sender_uname", is_bot=False)

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = sender
    upd.effective_message = msg
    upd.message = msg
    upd.message.entities = []
    upd.message.reply_to_message = None

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.args = []
    ctx.bot = AsyncMock()

    member_mock = MagicMock()
    member_mock.status = "member"
    ctx.bot.get_chat_member.return_value = member_mock

    await info_cmd(upd, ctx)

    msg.reply_html.assert_called_once()
    reply_text = msg.reply_html.call_args[0][0]
    assert "111" in reply_text
    assert "SenderUser" in reply_text
    assert "@sender_uname" in reply_text
    assert "<b>Status:</b> Member" in reply_text


@pytest.mark.asyncio
async def test_info_cmd_reply_based():
    """Test replying to a user's message with /info."""
    chat = Chat(id=-100123456789, type="supergroup")
    sender = User(id=111, first_name="SenderUser", is_bot=False)
    target = User(id=222, first_name="RepliedUser", username="replied_uname", is_bot=False)

    reply_msg = MagicMock(spec=Message)
    reply_msg.from_user = target

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = sender
    upd.effective_message = msg
    upd.message = msg
    upd.message.entities = []
    upd.message.reply_to_message = reply_msg

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.args = []
    ctx.bot = AsyncMock()

    member_mock = MagicMock()
    member_mock.status = "member"
    ctx.bot.get_chat_member.return_value = member_mock

    await info_cmd(upd, ctx)

    msg.reply_html.assert_called_once()
    reply_text = msg.reply_html.call_args[0][0]
    assert "222" in reply_text
    assert "RepliedUser" in reply_text
    assert "@replied_uname" in reply_text


@pytest.mark.asyncio
async def test_info_cmd_numeric_id_lookup():
    """Test /info <user_id> for user present in DB and absent from DB."""
    chat = Chat(id=-100123456789, type="supergroup")
    sender = User(id=111, first_name="SenderUser", is_bot=False)

    # 1. User present in DB
    upsert_user(333, "Known DB User", "known_uname")

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = sender
    upd.effective_message = msg
    upd.message = msg
    upd.message.entities = []
    upd.message.reply_to_message = None

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.args = ["333"]
    ctx.bot = AsyncMock()

    member_mock = MagicMock()
    member_mock.status = "member"
    ctx.bot.get_chat_member.return_value = member_mock

    await info_cmd(upd, ctx)

    reply_text = msg.reply_html.call_args[0][0]
    assert "333" in reply_text
    assert "Known DB User" in reply_text
    assert "@known_uname" in reply_text

    # 2. User absent from DB
    msg.reply_html.reset_mock()
    ctx.args = ["999999"]

    await info_cmd(upd, ctx)

    reply_text = msg.reply_html.call_args[0][0]
    assert "999999" in reply_text
    assert "User 999999" in reply_text
    assert "<b>Username:</b> Not set" in reply_text


@pytest.mark.asyncio
async def test_info_cmd_approved_and_unapproved():
    """Test approved vs unapproved user status reporting."""
    chat = Chat(id=-100123456789, type="supergroup")
    sender = User(id=111, first_name="SenderUser", is_bot=False)
    approved_target = User(id=444, first_name="ApprovedUser", is_bot=False)

    add_approved_user(chat.id, approved_target.id)

    reply_msg = MagicMock(spec=Message)
    reply_msg.from_user = approved_target

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = sender
    upd.effective_message = msg
    upd.message = msg
    upd.message.entities = []
    upd.message.reply_to_message = reply_msg

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.args = []
    ctx.bot = AsyncMock()

    member_mock = MagicMock()
    member_mock.status = "member"
    ctx.bot.get_chat_member.return_value = member_mock

    # Test approved target
    await info_cmd(upd, ctx)
    reply_text = msg.reply_html.call_args[0][0]
    assert "<b>Approved status:</b> Yes" in reply_text

    # Test unapproved target
    reply_msg.from_user = sender
    msg.reply_html.reset_mock()
    await info_cmd(upd, ctx)
    reply_text_unapproved = msg.reply_html.call_args[0][0]
    assert "<b>Approved status:</b> No" in reply_text_unapproved


@pytest.mark.asyncio
async def test_info_cmd_admin_and_owner_status():
    """Test administrator and group owner status detection."""
    chat = Chat(id=-100123456789, type="supergroup")

    owner_user = User(id=555, first_name="GroupOwner", is_bot=False)
    admin_user = User(id=666, first_name="GroupAdmin", is_bot=False)

    reply_msg = MagicMock(spec=Message)
    reply_msg.from_user = owner_user

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = owner_user
    upd.effective_message = msg
    upd.message = msg
    upd.message.entities = []
    upd.message.reply_to_message = reply_msg

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.args = []
    ctx.bot = AsyncMock()

    # 1. Creator / Owner
    member_mock = MagicMock()
    member_mock.status = "creator"
    ctx.bot.get_chat_member.return_value = member_mock

    await info_cmd(upd, ctx)
    reply_text = msg.reply_html.call_args[0][0]
    assert "<b>Administrator:</b> Yes" in reply_text
    assert "<b>Group Owner:</b> Yes" in reply_text
    assert "<b>Status:</b> Owner" in reply_text

    # 2. Administrator (not creator)
    msg.reply_html.reset_mock()
    reply_msg.from_user = admin_user
    member_mock.status = "administrator"

    with patch("admin.is_bot_or_group_owner", new_callable=AsyncMock) as mock_owner:
        mock_owner.return_value = False
        await info_cmd(upd, ctx)
        reply_text_admin = msg.reply_html.call_args[0][0]
        assert "<b>Administrator:</b> Yes" in reply_text_admin
        assert "<b>Group Owner:</b> No" in reply_text_admin
        assert "<b>Status:</b> Administrator" in reply_text_admin


@pytest.mark.asyncio
async def test_info_cmd_user_without_username():
    """Test user without username formatted correctly with HTML escaping."""
    chat = Chat(id=-100123456789, type="supergroup")
    no_uname_user = User(id=777, first_name="<TagUser>&Special", username=None, is_bot=False)

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = no_uname_user
    upd.effective_message = msg
    upd.message = msg
    upd.message.entities = []
    upd.message.reply_to_message = None

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.args = []
    ctx.bot = AsyncMock()

    member_mock = MagicMock()
    member_mock.status = "member"
    ctx.bot.get_chat_member.return_value = member_mock

    await info_cmd(upd, ctx)

    reply_text = msg.reply_html.call_args[0][0]
    assert "<b>Username:</b> Not set" in reply_text
    assert "&lt;TagUser&gt;&amp;Special" in reply_text


@pytest.mark.asyncio
async def test_info_cmd_inaccessible_user_and_api_errors():
    """Test inaccessible user or Telegram API errors when fetching member info."""
    chat = Chat(id=-100123456789, type="supergroup")
    sender = User(id=111, first_name="SenderUser", is_bot=False)

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_chat = chat
    upd.effective_user = sender
    upd.effective_message = msg
    upd.message = msg
    upd.message.entities = []
    upd.message.reply_to_message = None

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.args = ["888888"]
    ctx.bot = AsyncMock()

    # get_chat_member raises BadRequest (e.g., User not found)
    ctx.bot.get_chat_member.side_effect = BadRequest("User not found")

    await info_cmd(upd, ctx)

    # Must NOT report unknown user as a confirmed group member ("Member")
    reply_text = msg.reply_html.call_args[0][0]
    assert "<b>Status:</b> Not in group" in reply_text
    assert "<b>Administrator:</b> No" in reply_text
    assert "<b>Group Owner:</b> No" in reply_text

    # get_chat_member raises general TelegramError
    msg.reply_html.reset_mock()
    ctx.bot.get_chat_member.side_effect = TelegramError("API Error")

    await info_cmd(upd, ctx)
    reply_text_err = msg.reply_html.call_args[0][0]
    assert "<b>Status:</b> Not in group" in reply_text_err
