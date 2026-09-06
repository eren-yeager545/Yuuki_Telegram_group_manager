import asyncio
import pytest
from bot import ensure_event_loop

def test_ensure_event_loop_when_none_set():
    asyncio.set_event_loop(None)
    loop = ensure_event_loop()
    assert loop is not None
    assert not loop.is_closed()
    assert asyncio.get_event_loop() == loop

def test_ensure_event_loop_when_closed():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.close()

    new_loop = ensure_event_loop()
    assert new_loop is not None
    assert not new_loop.is_closed()
    assert new_loop != loop
    assert asyncio.get_event_loop() == new_loop

def test_ensure_event_loop_when_already_active():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    existing_loop = ensure_event_loop()
    assert existing_loop == loop
    assert not existing_loop.is_closed()


def test_post_init_does_not_schedule_expire_temp_actions():
    from unittest.mock import MagicMock
    from bot import post_init

    app = MagicMock()
    app.bot_data = {}
    app.job_queue = MagicMock()

    asyncio.run(post_init(app))

    # Ensure expire_temp_actions is not scheduled in job_queue
    for call in app.job_queue.run_repeating.call_args_list:
        callback = call.args[0]
        assert callback.__name__ != 'expire_temp_actions'


def test_get_conn_and_store_functions(tmp_path, monkeypatch):
    import store
    db_file = str(tmp_path / "test_bot.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    assert store.check_chat_quota(100, "notes") is True
    store.set_global_link("support", "https://example.com/support")
    assert store.get_global_link("support") == "https://example.com/support"

    store.delete_user_data(999)


def test_error_handler_with_message():
    from unittest.mock import AsyncMock, MagicMock
    from telegram import Update, Message
    from bot import error_handler

    mock_msg = AsyncMock(spec=Message)
    update = MagicMock(spec=Update)
    update.effective_message = mock_msg

    context = MagicMock()
    context.error = ValueError("Test exception")

    asyncio.run(error_handler(update, context))

    mock_msg.reply_text.assert_called_once()
    reply_text = mock_msg.reply_text.call_args[0][0]
    assert "Sorry, I couldn't complete that request because an unexpected error occurred 🌸✨ Please try again later!" in reply_text
    assert "Reference ID" not in reply_text


def test_error_handler_without_message():
    from unittest.mock import MagicMock
    from bot import error_handler

    update = MagicMock()
    update.effective_message = None

    context = MagicMock()
    context.error = ValueError("Test exception")

    # Should log without raising any exception
    asyncio.run(error_handler(update, context))


def test_user_command_ping():
    from unittest.mock import AsyncMock, MagicMock
    from common import ping_cmd

    update = MagicMock()
    update.message = AsyncMock()
    context = MagicMock()

    asyncio.run(ping_cmd(update, context))
    update.message.reply_text.assert_called_once()
    assert "🌸 Pong!" in update.message.reply_text.call_args[0][0]


def test_admin_command_ban(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import store
    db_file = str(tmp_path / "test_bot.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    import admin

    update = MagicMock()
    update.effective_chat.id = 123
    update.effective_user.id = 456
    update.message = AsyncMock()

    target_user = MagicMock()
    target_user.id = 789
    target_user.full_name = "Test User"
    update.message.reply_to_message.from_user = target_user

    context = MagicMock()
    context.bot.ban_chat_member = AsyncMock()

    async def mock_admin_only(upd, ctx):
        return True

    monkeypatch.setattr(admin, "admin_only", mock_admin_only)

    asyncio.run(admin.ban_cmd(update, context))
    update.message.reply_text.assert_called_once()
    assert "🌸 Banned Test User successfully! ✨" in update.message.reply_text.call_args[0][0]
