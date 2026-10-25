import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Chat, User, ChatJoinRequest, CallbackQuery, Message
from telegram.ext import ContextTypes
import store
from store import init_db, get_join_request_decision, save_join_request_decision
from admin import chat_join_request_handler, chat_join_request_callback_handler


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


@pytest.mark.asyncio
async def test_chat_join_request_handler_notification():
    chat = Chat(id=-100123456781, title="Test Lounge", type="supergroup")
    user_with_username = User(id=123456781, first_name="Example", last_name="User", username="example_user", is_bot=False)

    req = MagicMock(spec=ChatJoinRequest)
    req.chat = chat
    req.user = user_with_username

    upd = MagicMock(spec=Update)
    upd.chat_join_request = req

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()

    with patch("logger_helper.send_logger_notification", new_callable=AsyncMock) as mock_send_logger:
        mock_send_logger.return_value = True
        await chat_join_request_handler(upd, ctx)

        mock_send_logger.assert_called_once()
        args, kwargs = mock_send_logger.call_args
        msg_text = args[1] if len(args) > 1 else args[0]
        assert "Yuki noticed a new visitor!" in msg_text
        assert "Example User" in msg_text
        assert "@example_user" in msg_text
        assert "123456781" in msg_text
        assert kwargs.get("group_chat_id") == chat.id
        reply_markup = kwargs.get("reply_markup")
        assert reply_markup is not None
        btns = reply_markup.inline_keyboard[0]
        assert btns[0].text == "💚 Accept"
        assert btns[0].callback_data == f"cjr:a:{chat.id}:{user_with_username.id}"
        assert btns[1].text == "❤️ Decline"
        assert btns[1].callback_data == f"cjr:d:{chat.id}:{user_with_username.id}"

    # Test user WITHOUT username
    user_no_username = User(id=987654321, first_name="NoUser", is_bot=False)
    req.user = user_no_username

    with patch("logger_helper.send_logger_notification", new_callable=AsyncMock) as mock_send_logger:
        mock_send_logger.return_value = True
        await chat_join_request_handler(upd, ctx)

        args, _ = mock_send_logger.call_args
        msg_text = args[1] if len(args) > 1 else args[0]
        assert "Username: Not set" in msg_text
        assert "987654321" in msg_text


@pytest.mark.asyncio
async def test_chat_join_request_callback_accept_success():
    chat_id = -100123456782
    requester_id = 123456782
    admin_user = User(id=999, first_name="Admin", username="admin_user", is_bot=False)

    msg = AsyncMock()
    msg.text = "🌸 Yuki noticed a new visitor!"

    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{requester_id}"
    query.from_user = admin_user
    query.message = msg

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()

    with patch("admin.is_target_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True

        await chat_join_request_callback_handler(upd, ctx)

        ctx.bot.approve_chat_join_request.assert_called_once_with(chat_id=chat_id, user_id=requester_id)
        query.answer.assert_called_with("Join request accepted! 💚")
        query.edit_message_text.assert_called_once()
        assert "Request Accepted" in query.edit_message_text.call_args[0][0]

        decision = get_join_request_decision(chat_id, requester_id)
        assert decision is not None
        assert decision[0] == "accepted"
        assert decision[1] == admin_user.id


@pytest.mark.asyncio
async def test_chat_join_request_callback_decline_success():
    chat_id = -100123456783
    requester_id = 123456783
    admin_user = User(id=999, first_name="Admin", username="admin_user", is_bot=False)

    msg = AsyncMock()
    msg.text = "🌸 Yuki noticed a new visitor!"

    query = AsyncMock()
    query.data = f"cjr:d:{chat_id}:{requester_id}"
    query.from_user = admin_user
    query.message = msg

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()

    with patch("admin.is_target_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True

        await chat_join_request_callback_handler(upd, ctx)

        ctx.bot.decline_chat_join_request.assert_called_once_with(chat_id=chat_id, user_id=requester_id)
        query.answer.assert_called_with("Join request declined. ❤️")
        query.edit_message_text.assert_called_once()
        assert "Request Declined" in query.edit_message_text.call_args[0][0]

        decision = get_join_request_decision(chat_id, requester_id)
        assert decision is not None
        assert decision[0] == "declined"
        assert decision[1] == admin_user.id


@pytest.mark.asyncio
async def test_chat_join_request_callback_unauthorized_user():
    chat_id = -100123456784
    requester_id = 123456784
    regular_user = User(id=888, first_name="RegularUser", is_bot=False)

    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{requester_id}"
    query.from_user = regular_user

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = regular_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()

    with patch("admin.is_target_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = False

        await chat_join_request_callback_handler(upd, ctx)

        ctx.bot.approve_chat_join_request.assert_not_called()
        ctx.bot.decline_chat_join_request.assert_not_called()
        query.answer.assert_called_once_with("Gomen ne~ 🌸 Only group admins can manage join requests for this chat desu! 💕", show_alert=True)


@pytest.mark.asyncio
async def test_chat_join_request_callback_duplicate_decision():
    chat_id = -100123456785
    requester_id = 123456785
    admin_user = User(id=999, first_name="Admin", is_bot=False)

    # Record decision beforehand in DB
    save_join_request_decision(chat_id, requester_id, 'accepted', admin_user.id)

    query = AsyncMock()
    query.data = f"cjr:d:{chat_id}:{requester_id}"
    query.from_user = admin_user

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()

    with patch("admin.is_target_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True

        await chat_join_request_callback_handler(upd, ctx)

        ctx.bot.approve_chat_join_request.assert_not_called()
        ctx.bot.decline_chat_join_request.assert_not_called()
        query.answer.assert_called_once_with("This join request has already been accepted!", show_alert=True)


@pytest.mark.asyncio
async def test_chat_join_request_callback_api_error_handling():
    chat_id = -100123456786
    requester_id = 123456786
    admin_user = User(id=999, first_name="Admin", is_bot=False)

    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{requester_id}"
    query.from_user = admin_user

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()
    ctx.bot.approve_chat_join_request.side_effect = Exception("HIDE_ME: User not found or request expired")

    with patch("admin.is_target_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True

        await chat_join_request_callback_handler(upd, ctx)

        query.answer.assert_called_once_with("Failed to approve request (it may have expired or already been decided).", show_alert=True)
