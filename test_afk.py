import datetime
import html
import time
from unittest.mock import MagicMock, AsyncMock
import pytest
from telegram import MessageEntity

import store
import bot as bot_module
from handlers.afk import afk_cmd, format_afk_duration, DEFAULT_AFK_REASONS, extract_mentioned_afk_users


def test_format_afk_duration():
    assert format_afk_duration(12) == "12 seconds"
    assert format_afk_duration(180) == "3 minutes"
    assert format_afk_duration(4500) == "1 hour 15 minutes"
    assert format_afk_duration(7200) == "2 hours"
    assert format_afk_duration(100800) == "1 day 4 hours"
    assert format_afk_duration(0) == "0 seconds"


@pytest.mark.asyncio
async def test_afk_cmd_group_with_reason_and_emojis(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    update = MagicMock()
    context = MagicMock()
    msg = AsyncMock()
    update.effective_message = msg
    update.effective_chat.id = -10012345
    update.effective_chat.type = "supergroup"
    update.effective_user.id = 111
    update.effective_user.first_name = "Alice"
    context.args = ["sleeping", "😴✨"]

    await afk_cmd(update, context)

    afk_rec = store.get_afk(111)
    assert afk_rec is not None
    assert afk_rec[0] == "sleeping 😴✨"


@pytest.mark.asyncio
async def test_afk_cmd_group_without_reason_uses_default(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    update = MagicMock()
    context = MagicMock()
    msg = AsyncMock()
    update.effective_message = msg
    update.effective_chat.id = -10012345
    update.effective_chat.type = "supergroup"
    update.effective_user.id = 222
    update.effective_user.first_name = "Bob"
    context.args = []

    await afk_cmd(update, context)

    afk_rec = store.get_afk(222)
    assert afk_rec is not None
    assert afk_rec[0] in DEFAULT_AFK_REASONS


@pytest.mark.asyncio
async def test_afk_cmd_twice_updates_record(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    store.set_afk(333, "reason 1", 1000)
    assert store.get_afk(333)[0] == "reason 1"

    update = MagicMock()
    context = MagicMock()
    msg = AsyncMock()
    update.effective_message = msg
    update.effective_chat.id = -100
    update.effective_chat.type = "supergroup"
    update.effective_user.id = 333
    update.effective_user.first_name = "Charlie"
    context.args = ["reason", "2"]

    await afk_cmd(update, context)

    afk_rec = store.get_afk(333)
    assert afk_rec[0] == "reason 2"
    assert afk_rec[1] > 1000


@pytest.mark.asyncio
async def test_afk_global_across_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    # User set AFK in group A (-100111)
    store.set_afk(444, "sleeping", int(time.time()))

    # Should be AFK globally (retrievable anywhere)
    assert store.get_afk(444) is not None


@pytest.mark.asyncio
async def test_afk_mention_by_username_and_no_username(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    store.upsert_user(555, "Dave", "dave_user")
    store.set_afk(555, "away eating", int(time.time()) - 100)

    store.upsert_user(556, "NoUser", None)
    store.set_afk(556, "no username afk", int(time.time()) - 200)

    # 1. Mention username
    update1 = MagicMock()
    msg1 = MagicMock()
    update1.effective_message = msg1
    msg1.from_user.id = 999  # Sender is someone else
    msg1.reply_to_message = None

    entity1 = MagicMock()
    entity1.type = MessageEntity.MENTION
    entity1.offset = 6
    entity1.length = 10
    msg1.entities = [entity1]
    msg1.caption_entities = []
    msg1.text = "Hello @dave_user how are you?"

    afk_users1 = extract_mentioned_afk_users(update1, -100)
    assert len(afk_users1) == 1
    assert afk_users1[0][0] == 555
    assert afk_users1[0][3] == "away eating"

    # 2. Text mention for no username
    update2 = MagicMock()
    msg2 = MagicMock()
    update2.effective_message = msg2
    msg2.from_user.id = 999
    msg2.reply_to_message = None

    entity2 = MagicMock()
    entity2.type = MessageEntity.TEXT_MENTION
    u_obj = MagicMock()
    u_obj.is_bot = False
    u_obj.id = 556
    u_obj.first_name = "NoUser"
    u_obj.username = None
    entity2.user = u_obj

    msg2.entities = [entity2]
    msg2.caption_entities = []
    msg2.text = "Hello NoUser"

    afk_users2 = extract_mentioned_afk_users(update2, -100)
    assert len(afk_users2) == 1
    assert afk_users2[0][0] == 556
    assert afk_users2[0][1] == "NoUser"


@pytest.mark.asyncio
async def test_afk_self_mention_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    store.upsert_user(555, "Dave", "dave_user")
    store.set_afk(555, "away eating", int(time.time()) - 100)

    update = MagicMock()
    msg = MagicMock()
    update.effective_message = msg
    msg.from_user.id = 555  # Sender is the AFK user themselves
    msg.reply_to_message = None

    entity = MagicMock()
    entity.type = MessageEntity.MENTION
    entity.offset = 6
    entity.length = 10
    msg.entities = [entity]
    msg.caption_entities = []
    msg.text = "Hello @dave_user how are you?"

    afk_users = extract_mentioned_afk_users(update, -100)
    assert len(afk_users) == 0


@pytest.mark.asyncio
async def test_afk_multiple_mentions(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    store.upsert_user(101, "User1", "user1")
    store.upsert_user(102, "User2", "user2")
    store.set_afk(101, "reason 1", int(time.time()) - 10)
    store.set_afk(102, "reason 2", int(time.time()) - 20)

    update = MagicMock()
    msg = MagicMock()
    update.effective_message = msg
    msg.from_user.id = 999
    msg.reply_to_message = None

    e1 = MagicMock()
    e1.type = MessageEntity.MENTION
    e1.offset = 0
    e1.length = 6

    e2 = MagicMock()
    e2.type = MessageEntity.MENTION
    e2.offset = 7
    e2.length = 6

    msg.entities = [e1, e2]
    msg.caption_entities = []
    msg.text = "@user1 @user2 hi guys"

    afk_users = extract_mentioned_afk_users(update, -100)
    assert len(afk_users) == 2
    uids = [u[0] for u in afk_users]
    assert 101 in uids
    assert 102 in uids


@pytest.mark.asyncio
async def test_afk_mention_by_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    store.set_afk(666, "busy working", int(time.time()) - 300)

    update = MagicMock()
    msg = MagicMock()
    update.effective_message = msg
    msg.from_user.id = 999
    msg.entities = []
    msg.caption_entities = []

    replied_user = MagicMock()
    replied_user.is_bot = False
    replied_user.id = 666
    replied_user.first_name = "Eve"
    replied_user.username = None

    msg.reply_to_message.from_user = replied_user

    afk_users = extract_mentioned_afk_users(update, -100)
    assert len(afk_users) == 1
    assert afk_users[0][0] == 666
    assert afk_users[0][1] == "Eve"


@pytest.mark.asyncio
async def test_message_router_afk_return_and_subsequent_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    chat_id = -100999
    user_id = 777

    store.set_afk(user_id, "out door", int(time.time()) - 60)

    context = MagicMock()

    # 1. Sending /afk should NOT trigger return
    update1 = MagicMock()
    msg1 = AsyncMock()
    update1.effective_message = msg1
    update1.effective_chat.id = chat_id
    update1.effective_chat.type = "supergroup"
    update1.effective_chat.title = "Test Supergroup"
    update1.effective_chat.username = None
    update1.effective_user.id = user_id
    update1.effective_user.is_bot = False
    update1.effective_user.first_name = "Frank"
    update1.effective_user.full_name = "Frank User"
    update1.effective_user.username = None
    msg1.from_user.id = user_id
    msg1.from_user.is_bot = False
    msg1.message_id = 1001
    msg1.text = "/afk studying again"
    msg1.caption = None
    msg1.entities = []
    msg1.caption_entities = []
    msg1.reply_to_message = None
    msg1.sticker = None
    msg1.voice = None
    msg1.photo = None
    msg1.video = None
    msg1.document = None
    msg1.audio = None
    msg1.animation = None
    msg1.poll = None
    msg1.forward_origin = None
    msg1.video_note = None
    msg1.contact = None

    monkeypatch.setattr(bot_module, "handle_broadcast_content", AsyncMock(return_value=False))

    await bot_module.message_router(update1, context)
    assert store.get_afk(user_id) is not None

    # 2. Sending normal message in ANY group SHOULD return user and clear AFK globally
    msg2 = AsyncMock()
    update2 = MagicMock()
    update2.effective_message = msg2
    update2.effective_chat.id = -100888  # Different group!
    update2.effective_chat.type = "supergroup"
    update2.effective_chat.title = "Other Group"
    update2.effective_chat.username = None
    update2.effective_user.id = user_id
    update2.effective_user.is_bot = False
    update2.effective_user.first_name = "Frank"
    update2.effective_user.full_name = "Frank User"
    update2.effective_user.username = None
    msg2.from_user.id = user_id
    msg2.from_user.is_bot = False
    msg2.message_id = 1002
    msg2.text = "Hello everyone!"
    msg2.caption = None
    msg2.entities = []
    msg2.caption_entities = []
    msg2.reply_to_message = None
    msg2.sticker = None
    msg2.voice = None
    msg2.photo = None
    msg2.video = None
    msg2.document = None
    msg2.audio = None
    msg2.animation = None
    msg2.poll = None
    msg2.forward_origin = None
    msg2.video_note = None
    msg2.contact = None

    await bot_module.message_router(update2, context)

    msg2.reply_text.assert_called_once()
    reply_arg = msg2.reply_text.call_args[0][0]
    assert "Welcome back" in reply_arg
    assert store.get_afk(user_id) is None

    # 3. Subsequent normal message should NOT reply with welcome back again
    msg3 = AsyncMock()
    update3 = MagicMock()
    update3.effective_message = msg3
    update3.effective_chat.id = chat_id
    update3.effective_chat.type = "supergroup"
    update3.effective_chat.title = "Test Supergroup"
    update3.effective_chat.username = None
    update3.effective_user.id = user_id
    update3.effective_user.is_bot = False
    update3.effective_user.first_name = "Frank"
    update3.effective_user.full_name = "Frank User"
    update3.effective_user.username = None
    msg3.from_user.id = user_id
    msg3.from_user.is_bot = False
    msg3.message_id = 1003
    msg3.text = "Second message"
    msg3.caption = None
    msg3.entities = []
    msg3.caption_entities = []
    msg3.reply_to_message = None
    msg3.sticker = None
    msg3.voice = None
    msg3.photo = None
    msg3.video = None
    msg3.document = None
    msg3.audio = None
    msg3.animation = None
    msg3.poll = None
    msg3.forward_origin = None
    msg3.video_note = None
    msg3.contact = None

    await bot_module.message_router(update3, context)
    msg3.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_database_failure_resilience(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    # Raise Exception on store.get_afk
    monkeypatch.setattr(store, 'get_afk', MagicMock(side_effect=Exception("Database error simulation")))

    update = MagicMock()
    context = MagicMock()
    msg = AsyncMock()
    update.effective_message = msg
    update.effective_chat.id = -100
    update.effective_chat.type = "supergroup"
    update.effective_chat.title = "Group"
    update.effective_chat.username = None
    update.effective_user.id = 999
    update.effective_user.is_bot = False
    update.effective_user.first_name = "ErrorTest"
    update.effective_user.full_name = "ErrorTest User"
    update.effective_user.username = None
    msg.from_user.id = 999
    msg.from_user.is_bot = False
    msg.message_id = 2001
    msg.text = "Testing error resilience"
    msg.caption = None
    msg.entities = []
    msg.caption_entities = []
    msg.reply_to_message = None
    msg.sticker = None
    msg.voice = None
    msg.photo = None
    msg.video = None
    msg.document = None
    msg.audio = None
    msg.animation = None
    msg.poll = None
    msg.forward_origin = None
    msg.video_note = None
    msg.contact = None

    monkeypatch.setattr(bot_module, "handle_broadcast_content", AsyncMock(return_value=False))

    # Should handle gracefully without raising unhandled error
    await bot_module.message_router(update, context)


@pytest.mark.asyncio
async def test_datadel_clears_afk(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_afk.db"))
    store.init_db()

    store.set_afk(888, "sleeping", int(time.time()))
    assert store.get_afk(888) is not None

    store.delete_user_data(888)
    assert store.get_afk(888) is None
