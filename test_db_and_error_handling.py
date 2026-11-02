import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import Update, Message, User, Chat, MessageEntity
import bot
import admin
import store
import config


@pytest.mark.asyncio
async def test_error_handler_suppresses_generic_reply_for_normal_group_message():
    """Verify that an unexpected exception on an ordinary group message does NOT trigger Oopsie! message."""
    mock_msg = AsyncMock(spec=Message)
    mock_msg.text = "Hello everyone in the group!"
    mock_msg.caption = None
    mock_msg.entities = []

    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = -1001234567
    mock_chat.type = "supergroup"

    mock_user = MagicMock(spec=User)
    mock_user.id = 98765
    mock_user.is_bot = False

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.message = mock_msg
    update.effective_chat = mock_chat
    update.effective_user = mock_user
    update.callback_query = None
    update.my_chat_member = None
    update.chat_member = None
    update.chat_join_request = None

    context = MagicMock()
    context.error = ValueError("Database connection timeout or internal failure")

    await bot.error_handler(update, context)

    # Must NOT reply to the message
    mock_msg.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_error_handler_replies_for_command_failure():
    """Verify that an unexpected exception on a user-initiated command triggers the generic error response."""
    mock_msg = AsyncMock(spec=Message)
    mock_msg.text = "/stats"
    mock_msg.caption = None
    ent = MagicMock(spec=MessageEntity)
    ent.type = "bot_command"
    ent.offset = 0
    ent.length = 6
    mock_msg.entities = [ent]

    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = -1001234567
    mock_chat.type = "supergroup"

    mock_user = MagicMock(spec=User)
    mock_user.id = 98765

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.message = mock_msg
    update.effective_chat = mock_chat
    update.effective_user = mock_user
    update.callback_query = None
    update.my_chat_member = None
    update.chat_member = None
    update.chat_join_request = None

    context = MagicMock()
    context.error = RuntimeError("Command handler failed")

    await bot.error_handler(update, context)

    # Must reply to user command failure
    mock_msg.reply_text.assert_called_once()
    assert "Oopsie!" in mock_msg.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_error_handler_replies_for_callback_query_failure():
    """Verify that an unexpected exception on interactive callback query triggers the generic error response."""
    mock_msg = AsyncMock(spec=Message)
    mock_cb = MagicMock()
    mock_cb.message = mock_msg

    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = -1001234567
    mock_user = MagicMock(spec=User)
    mock_user.id = 98765

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.callback_query = mock_cb
    update.effective_chat = mock_chat
    update.effective_user = mock_user
    update.message = None
    update.my_chat_member = None
    update.chat_member = None
    update.chat_join_request = None

    context = MagicMock()
    context.error = RuntimeError("Callback failure")

    await bot.error_handler(update, context)

    mock_msg.reply_text.assert_called_once()
    assert "Oopsie!" in mock_msg.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_message_router_catches_internal_exception(monkeypatch):
    """Verify that exceptions in internal message routing do not raise unhandled exceptions out of message_router."""
    mock_msg = AsyncMock(spec=Message)
    mock_msg.message_id = 111
    mock_msg.text = "Just a test message"
    mock_msg.caption = None
    mock_msg.entities = []

    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = -1009999
    mock_chat.type = "supergroup"
    mock_chat.title = "Test Group"
    mock_chat.username = "testgroup"

    mock_user = MagicMock(spec=User)
    mock_user.id = 8888
    mock_user.is_bot = False
    mock_user.full_name = "Normal User"
    mock_user.username = "normal_user"

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.effective_chat = mock_chat
    update.effective_user = mock_user

    context = MagicMock()

    # Force internal router to raise an error
    monkeypatch.setattr(bot, "upsert_group", MagicMock(side_effect=Exception("Database lock error")))

    # Should not raise exception
    await bot.message_router(update, context)
    mock_msg.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_db_command_denied_for_unauthorized_user(monkeypatch):
    """Verify that /db denies access to regular users, sudo users, and admins who are not bot owner."""
    owner_id = 123456789
    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(admin, "OWNER_IDS", [owner_id])

    mock_msg = AsyncMock(spec=Message)
    mock_user = MagicMock(spec=User)
    mock_user.id = 999999999  # Sudo or non-owner user

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.effective_user = mock_user

    context = MagicMock()

    with patch("store.get_db_stats") as mock_get_stats:
        await admin.db_cmd(update, context)
        # Verify query was NOT called
        mock_get_stats.assert_not_called()

    mock_msg.reply_text.assert_called_once()
    assert "reserved for the bot owner" in mock_msg.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_db_command_executed_by_owner_sqlite(monkeypatch):
    """Verify that /db runs for the bot owner and outputs live SQLite statistics."""
    owner_id = 123456789
    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(admin, "OWNER_IDS", [owner_id])

    mock_msg = AsyncMock(spec=Message)
    mock_user = MagicMock(spec=User)
    mock_user.id = owner_id

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.effective_user = mock_user

    context = MagicMock()

    await admin.db_cmd(update, context)

    mock_msg.reply_html.assert_called_once()
    reply_text = mock_msg.reply_html.call_args[0][0]

    assert "Database Statistics" in reply_text
    assert "Total Documents:" in reply_text
    assert "Database Storage Used:" in reply_text
    assert "Index Storage:" in reply_text
    assert "Total Storage Capacity:" in reply_text
    assert "Available Storage:" in reply_text
    assert "Storage Usage:" in reply_text
    assert "Indexes:" in reply_text
    assert "Connections:" in reply_text
    assert "Table Breakdown" in reply_text


@pytest.mark.asyncio
async def test_db_command_executed_by_owner_mongo(monkeypatch):
    """Verify that /db works correctly with MongoDB backend."""
    import mongomock

    owner_id = 123456789
    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(admin, "OWNER_IDS", [owner_id])

    mock_client = mongomock.MongoClient()
    mock_db = mock_client["yuuki_bot"]
    mock_db["users"].insert_one({"user_id": 1, "full_name": "Test"})
    mock_db["groups"].insert_one({"chat_id": -1001, "title": "Test Group"})

    # Patch ping and dbStats on mongomock
    def mock_command(cmd, *args, **kwargs):
        if cmd == "ping":
            return {"ok": 1.0}
        if cmd == "serverStatus":
            return {"version": "7.0.5", "connections": {"current": 2, "available": 100}}
        if cmd == "dbStats":
            return {"storageSize": 40960, "indexes": 3, "indexSize": 8192}
        if cmd == "buildInfo":
            return {"version": "7.0.5"}
        return {}

    mock_db.command = mock_command

    monkeypatch.setattr(store, "is_mongo", lambda: True)
    monkeypatch.setattr(store, "get_mongo_db", lambda: mock_db)

    mock_msg = AsyncMock(spec=Message)
    mock_user = MagicMock(spec=User)
    mock_user.id = owner_id

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.effective_user = mock_user

    context = MagicMock()

    await admin.db_cmd(update, context)

    mock_msg.reply_html.assert_called_once()
    reply_text = mock_msg.reply_html.call_args[0][0]

    assert "Database Statistics" in reply_text
    assert "Total Documents:" in reply_text
    assert "Database Storage Used:" in reply_text
    assert "Index Storage:" in reply_text
    assert "Total Storage Capacity:" in reply_text
    assert "Available Storage:" in reply_text
    assert "Storage Usage:" in reply_text
    assert "Indexes:" in reply_text
    assert "Connections:" in reply_text
    assert "Collection Breakdown" in reply_text
    assert '"users"' in reply_text


@pytest.mark.asyncio
async def test_db_command_configured_limit_mb(monkeypatch):
    """Verify that DB_STORAGE_LIMIT_MB is properly reflected in /db output."""
    owner_id = 123456789
    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(admin, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(config, "DB_STORAGE_LIMIT_MB", 500)

    mock_msg = AsyncMock(spec=Message)
    mock_user = MagicMock(spec=User)
    mock_user.id = owner_id

    update = MagicMock(spec=Update)
    update.effective_message = mock_msg
    update.effective_user = mock_user

    context = MagicMock()

    await admin.db_cmd(update, context)

    mock_msg.reply_html.assert_called_once()
    reply_text = mock_msg.reply_html.call_args[0][0]

    assert "Total Storage Capacity:</b> 500 MB (configured limit)" in reply_text
    assert "(estimated)" in reply_text
