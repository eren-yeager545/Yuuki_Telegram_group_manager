import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, User, Chat, Message


def test_purge_reply_defaults_to_replied_user(monkeypatch, tmp_path):
    import store
    import admin

    db_file = str(tmp_path / "test_purge_reply_user.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    # User 555 and User 666 messages recorded
    store.record_user_message(-1001, 555, 100)
    store.record_user_message(-1001, 666, 101)
    store.record_user_message(-1001, 555, 102)
    store.record_user_message(-1001, 666, 103)
    store.record_user_message(-1001, 555, 104)

    bot = AsyncMock()
    bot.delete_messages = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    context = MagicMock()
    context.bot = bot
    context.args = []

    chat = Chat(id=-1001, type="supergroup")
    admin_user = User(id=100, first_name="Admin", is_bot=False)

    user_a = User(id=555, first_name="UserA", is_bot=False)
    reply_msg = MagicMock(spec=Message)
    reply_msg.message_id = 100
    reply_msg.from_user = user_a

    cmd_msg = MagicMock(spec=Message)
    cmd_msg.message_id = 105
    cmd_msg.reply_to_message = reply_msg
    cmd_msg.entities = []

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = admin_user
    update.message = cmd_msg

    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))

    asyncio.run(admin.purge_cmd(update, context))

    bot.delete_messages.assert_called_once()
    call_args = bot.delete_messages.call_args
    assert call_args.kwargs["chat_id"] == -1001
    # ONLY User A's messages in range (100, 102, 104) are purged
    assert call_args.kwargs["message_ids"] == [100, 102, 104]

    bot.send_message.assert_called_once()
    assert "Purged 3 message(s)" in bot.send_message.call_args.kwargs["text"]


def test_purge_user_targeted_mode(monkeypatch, tmp_path):
    import store
    import admin

    db_file = str(tmp_path / "test_purge_targeted.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    # Record messages for User A (555) and User B (666)
    store.record_user_message(-1001, 555, 101)
    store.record_user_message(-1001, 666, 102)
    store.record_user_message(-1001, 555, 103)
    store.record_user_message(-1001, 666, 104)
    store.record_user_message(-1001, 555, 105)

    bot = AsyncMock()
    bot.delete_messages = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    context = MagicMock()
    context.bot = bot
    context.args = ["@user_a"]

    chat = Chat(id=-1001, type="supergroup")
    admin_user = User(id=100, first_name="Admin", is_bot=False)
    user_a = User(id=555, first_name="UserA", username="user_a", is_bot=False)

    reply_msg = MagicMock(spec=Message)
    reply_msg.message_id = 100
    reply_msg.from_user = User(id=777, first_name="Other", is_bot=False)

    cmd_msg = MagicMock(spec=Message)
    cmd_msg.message_id = 106
    cmd_msg.reply_to_message = reply_msg
    cmd_msg.entities = []

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = admin_user
    update.message = cmd_msg

    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))
    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=user_a))

    asyncio.run(admin.purge_cmd(update, context))

    bot.delete_messages.assert_called_once()
    deleted_ids = bot.delete_messages.call_args.kwargs["message_ids"]

    # Must contain ONLY User A's messages (101, 103, 105)
    assert deleted_ids == [101, 103, 105]
    assert 102 not in deleted_ids
    assert 104 not in deleted_ids

    # Check database: 102 and 104 must still be present in user_messages for User B
    user_b_msgs = store.get_user_messages(-1001, 666)
    assert 102 in user_b_msgs
    assert 104 in user_b_msgs

    # User A's purged messages must be removed from DB
    user_a_msgs = store.get_user_messages(-1001, 555)
    assert 101 not in user_a_msgs
    assert 103 not in user_a_msgs
    assert 105 not in user_a_msgs


def test_purge_api_fallback_on_failure(monkeypatch, tmp_path):
    import store
    import admin

    db_file = str(tmp_path / "test_purge_fallback.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    user_a_id = 555
    store.record_user_message(-1001, user_a_id, 100)
    store.record_user_message(-1001, user_a_id, 101)
    store.record_user_message(-1001, user_a_id, 102)

    bot = AsyncMock()
    # Batch delete fails (e.g. contains messages older than 48 hours)
    bot.delete_messages = AsyncMock(side_effect=Exception("Message can't be deleted"))

    # Individual delete succeeds for 100 and 101, but fails for 102
    async def mock_delete_message(chat_id, message_id):
        if message_id == 102:
            raise Exception("Message to delete not found")
        return True

    bot.delete_message = AsyncMock(side_effect=mock_delete_message)
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    context = MagicMock()
    context.bot = bot
    context.args = []

    chat = Chat(id=-1001, type="supergroup")
    admin_user = User(id=100, first_name="Admin", is_bot=False)

    reply_msg = MagicMock(spec=Message)
    reply_msg.message_id = 100
    reply_msg.from_user = User(id=555, first_name="UserA", is_bot=False)

    cmd_msg = MagicMock(spec=Message)
    cmd_msg.message_id = 102
    cmd_msg.reply_to_message = reply_msg

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = admin_user
    update.message = cmd_msg

    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))

    asyncio.run(admin.purge_cmd(update, context))

    assert bot.delete_message.call_count == 3
    # Exactly 2 messages (100 and 101) succeeded
    assert "Purged 2 message(s)" in bot.send_message.call_args.kwargs["text"]


def test_purge_without_reply(monkeypatch, tmp_path):
    import store
    import admin

    db_file = str(tmp_path / "test_purge_noreply.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    bot = AsyncMock()

    context = MagicMock()
    context.bot = bot
    context.args = []

    chat = Chat(id=-1001, type="supergroup")
    admin_user = User(id=100, first_name="Admin", is_bot=False)

    cmd_msg = MagicMock(spec=Message)
    cmd_msg.message_id = 105
    cmd_msg.reply_to_message = None
    cmd_msg.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = admin_user
    update.message = cmd_msg

    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))

    asyncio.run(admin.purge_cmd(update, context))

    cmd_msg.reply_text.assert_called_once()
    assert "Please reply to a message" in cmd_msg.reply_text.call_args[0][0]


def test_dban_successful_ban_and_purge(monkeypatch, tmp_path):
    import store
    import admin

    db_file = str(tmp_path / "test_dban_success.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    target_id = 555
    store.record_user_message(-1001, target_id, 201)
    store.record_user_message(-1001, target_id, 202)
    store.record_user_message(-1001, target_id, 203)

    bot = AsyncMock()
    bot.id = 9999
    bot.ban_chat_member = AsyncMock()
    bot.delete_messages = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.send_message = AsyncMock()

    context = MagicMock()
    context.bot = bot

    chat = Chat(id=-1001, type="supergroup")
    admin_user = User(id=100, first_name="Admin", is_bot=False)
    target_user = User(id=target_id, first_name="Spammer", username="spammer", is_bot=False)

    cmd_msg = MagicMock(spec=Message)
    cmd_msg.message_id = 999
    cmd_msg.reply_to_message = None

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = admin_user
    update.message = cmd_msg

    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))
    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=target_user))
    monkeypatch.setattr(admin, "is_target_admin", AsyncMock(return_value=False))

    asyncio.run(admin.dban_cmd(update, context))

    bot.ban_chat_member.assert_called_once_with(-1001, target_id, revoke_messages=True)
    bot.delete_messages.assert_called_once()
    deleted_mids = bot.delete_messages.call_args.kwargs["message_ids"]
    assert deleted_mids == [201, 202, 203]

    assert store.get_user_messages(-1001, target_id) == []
    bot.send_message.assert_called_once()
    assert "3 of their message(s) were purged" in bot.send_message.call_args.kwargs["text"]


def test_dban_failed_ban_does_not_purge(monkeypatch, tmp_path):
    import store
    import admin

    db_file = str(tmp_path / "test_dban_fail.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    target_id = 555
    store.record_user_message(-1001, target_id, 201)
    store.record_user_message(-1001, target_id, 202)

    bot = AsyncMock()
    bot.id = 9999
    # Ban fails due to missing permissions
    bot.ban_chat_member = AsyncMock(side_effect=Exception("Telegram API Error: Not enough rights"))
    bot.delete_messages = AsyncMock()
    bot.delete_message = AsyncMock()

    context = MagicMock()
    context.bot = bot

    chat = Chat(id=-1001, type="supergroup")
    admin_user = User(id=100, first_name="Admin", is_bot=False)
    target_user = User(id=target_id, first_name="Target", is_bot=False)

    cmd_msg = MagicMock(spec=Message)
    cmd_msg.message_id = 999
    cmd_msg.reply_to_message = None

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = admin_user
    update.message = cmd_msg

    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))
    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=target_user))
    monkeypatch.setattr(admin, "is_target_admin", AsyncMock(return_value=False))

    safe_reply_mock = AsyncMock()
    monkeypatch.setattr(admin, "safe_reply_error", safe_reply_mock)

    asyncio.run(admin.dban_cmd(update, context))

    safe_reply_mock.assert_called_once()
    bot.delete_messages.assert_not_called()

    # Messages MUST NOT be purged from DB if ban failed
    remaining_msgs = store.get_user_messages(-1001, target_id)
    assert remaining_msgs == [202, 201]


def test_dban_admin_and_self_protection(monkeypatch, tmp_path):
    import store
    import admin

    db_file = str(tmp_path / "test_dban_protect.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    bot = AsyncMock()
    bot.id = 9999

    context = MagicMock()
    context.bot = bot

    chat = Chat(id=-1001, type="supergroup")
    admin_user = User(id=100, first_name="Admin", is_bot=False)
    target_admin = User(id=200, first_name="OtherAdmin", is_bot=False)

    cmd_msg = MagicMock(spec=Message)
    cmd_msg.message_id = 999
    cmd_msg.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = admin_user
    update.message = cmd_msg

    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))

    # 1. Commander trying to ban self
    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=admin_user))
    asyncio.run(admin.dban_cmd(update, context))
    assert "You can't ban yourself" in cmd_msg.reply_text.call_args[0][0]

    # 2. Commander trying to ban bot
    bot_user = User(id=9999, first_name="Bot", is_bot=True)
    cmd_msg.reply_text.reset_mock()
    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=bot_user))
    asyncio.run(admin.dban_cmd(update, context))
    assert "please don't ban me" in cmd_msg.reply_text.call_args[0][0]

    # 3. Commander trying to ban group admin
    cmd_msg.reply_text.reset_mock()
    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=target_admin))
    monkeypatch.setattr(admin, "is_target_admin", AsyncMock(return_value=True))
    asyncio.run(admin.dban_cmd(update, context))
    assert "This user is a group admin" in cmd_msg.reply_text.call_args[0][0]
