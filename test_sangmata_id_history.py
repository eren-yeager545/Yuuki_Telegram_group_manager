import pytest
from unittest.mock import AsyncMock, MagicMock
import store
import admin
import bot
from telegram import Update, Chat, User

@pytest.fixture(autouse=True)
def clean_db(tmp_path):
    db_file = str(tmp_path / "test_bot.db")
    store.DB_PATH = db_file
    store.init_db()
    yield


def make_user(uid=1001, first_name="John", last_name=None, username="john_doe", is_bot=False):
    u = MagicMock(spec=User)
    u.id = uid
    u.first_name = first_name
    u.last_name = last_name
    u.username = username
    u.is_bot = is_bot
    return u


def make_context():
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


@pytest.mark.asyncio
async def test_first_observation_no_notification():
    chat_id = -100123456
    user = make_user(uid=1001, first_name="Alice", last_name="Smith", username="alice_s")
    context = make_context()

    res = await bot.check_and_track_profile_change(chat_id, user, context)
    assert res is False
    context.bot.send_message.assert_not_called()

    snapshot = store.get_profile_snapshot(chat_id, 1001)
    assert snapshot is not None
    assert snapshot["first_name"] == "Alice"
    assert snapshot["last_name"] == "Smith"
    assert snapshot["username"] == "alice_s"


@pytest.mark.asyncio
async def test_no_profile_change():
    chat_id = -100123456
    user = make_user(uid=1001, first_name="Alice", last_name="Smith", username="alice_s")
    context = make_context()

    # First observation
    await bot.check_and_track_profile_change(chat_id, user, context)
    context.bot.send_message.reset_mock()

    # Second update with identical profile
    res = await bot.check_and_track_profile_change(chat_id, user, context)
    assert res is False
    context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_first_name_changed():
    chat_id = -100123456
    user1 = make_user(uid=1002, first_name="Bob", last_name="Marley", username="bob_m")
    user2 = make_user(uid=1002, first_name="Bobby", last_name="Marley", username="bob_m")
    context = make_context()

    # Initial snapshot
    await bot.check_and_track_profile_change(chat_id, user1, context)

    # Name changed
    res = await bot.check_and_track_profile_change(chat_id, user2, context)
    assert res is True
    context.bot.send_message.assert_called_once()

    args, kwargs = context.bot.send_message.call_args
    assert kwargs["chat_id"] == chat_id
    text = kwargs["text"]
    assert "🔄 <b>Name Changed</b>" in text
    assert '▫️ <b>Old:</b> "Bob Marley"' in text
    assert '▫️ <b>New:</b> "Bobby Marley"' in text
    assert "tg://user?id=1002" in text
    assert "Bobby Marley" in text


@pytest.mark.asyncio
async def test_last_name_changed():
    chat_id = -100123456
    user1 = make_user(uid=1003, first_name="Charlie", last_name=None, username="charlie_u")
    user2 = make_user(uid=1003, first_name="Charlie", last_name="Brown", username="charlie_u")
    context = make_context()

    await bot.check_and_track_profile_change(chat_id, user1, context)
    res = await bot.check_and_track_profile_change(chat_id, user2, context)
    assert res is True

    text = context.bot.send_message.call_args[1]["text"]
    assert "🔄 <b>Name Changed</b>" in text
    assert '▫️ <b>Old:</b> "Charlie"' in text
    assert '▫️ <b>New:</b> "Charlie Brown"' in text


@pytest.mark.asyncio
async def test_username_changed():
    chat_id = -100123456
    user1 = make_user(uid=1004, first_name="Dave", username="dave_old")
    user2 = make_user(uid=1004, first_name="Dave", username="dave_new")
    context = make_context()

    await bot.check_and_track_profile_change(chat_id, user1, context)
    res = await bot.check_and_track_profile_change(chat_id, user2, context)
    assert res is True

    text = context.bot.send_message.call_args[1]["text"]
    assert "🔄 <b>Username Changed</b>" in text
    assert '▫️ <b>Old:</b> "@dave_old"' in text
    assert '▫️ <b>New:</b> "@dave_new"' in text


@pytest.mark.asyncio
async def test_username_removed():
    chat_id = -100123456
    user1 = make_user(uid=1005, first_name="Eve", username="eve_online")
    user2 = make_user(uid=1005, first_name="Eve", username=None)
    context = make_context()

    await bot.check_and_track_profile_change(chat_id, user1, context)
    res = await bot.check_and_track_profile_change(chat_id, user2, context)
    assert res is True

    text = context.bot.send_message.call_args[1]["text"]
    assert "🔄 <b>Username Changed</b>" in text
    assert '▫️ <b>Old:</b> "@eve_online"' in text
    assert '▫️ <b>New:</b> "No username"' in text


@pytest.mark.asyncio
async def test_combined_name_and_username_changed():
    chat_id = -100123456
    user1 = make_user(uid=1006, first_name="Frank", last_name="Old", username="frank_old")
    user2 = make_user(uid=1006, first_name="Frankie", last_name="New", username="frank_new")
    context = make_context()

    await bot.check_and_track_profile_change(chat_id, user1, context)
    res = await bot.check_and_track_profile_change(chat_id, user2, context)
    assert res is True

    text = context.bot.send_message.call_args[1]["text"]
    assert "🔄 <b>Profile Updated</b>" in text
    assert "<b>Name</b>" in text
    assert '▫️ <b>Old:</b> "Frank Old"' in text
    assert '▫️ <b>New:</b> "Frankie New"' in text
    assert "<b>Username</b>" in text
    assert '▫️ <b>Old:</b> "@frank_old"' in text
    assert '▫️ <b>New:</b> "@frank_new"' in text


@pytest.mark.asyncio
async def test_bot_user_ignored():
    chat_id = -100123456
    bot_user = make_user(uid=9999, first_name="MyBot", is_bot=True)
    context = make_context()

    res = await bot.check_and_track_profile_change(chat_id, bot_user, context)
    assert res is False
    assert store.get_profile_snapshot(chat_id, 9999) is None


@pytest.mark.asyncio
async def test_html_special_characters_escaped():
    chat_id = -100123456
    user1 = make_user(uid=1007, first_name="<Tag>", last_name="& More", username="tag_user")
    user2 = make_user(uid=1007, first_name="<NewTag>", last_name="\"Quotes\"", username="tag_user")
    context = make_context()

    await bot.check_and_track_profile_change(chat_id, user1, context)
    await bot.check_and_track_profile_change(chat_id, user2, context)

    text = context.bot.send_message.call_args[1]["text"]
    assert "&lt;Tag&gt; &amp; More" in text
    assert "&lt;NewTag&gt; &quot;Quotes&quot;" in text
    assert "<Tag>" not in text


@pytest.mark.asyncio
async def test_duplicate_update_processing_no_repeated_notification():
    chat_id = -100123456
    user1 = make_user(uid=1008, first_name="Grace", username="grace1")
    user2 = make_user(uid=1008, first_name="Grace", username="grace2")
    context = make_context()

    await bot.check_and_track_profile_change(chat_id, user1, context)

    res1 = await bot.check_and_track_profile_change(chat_id, user2, context)
    assert res1 is True
    assert context.bot.send_message.call_count == 1

    res2 = await bot.check_and_track_profile_change(chat_id, user2, context)
    assert res2 is False
    assert context.bot.send_message.call_count == 1


@pytest.mark.asyncio
async def test_history_removed_and_absent_from_help():
    assert not hasattr(admin, "history_cmd")
    assert not hasattr(store, "get_user_name_history")

    app = MagicMock()
    app.bot_data = {}

    user_cmds = [
        ('start', MagicMock(), 'General Commands', 'Start bot', '/start'),
        ('help', MagicMock(), 'General Commands', 'Get help', '/help'),
    ]
    for spec in user_cmds:
        bot.register_help(app, spec[2], f"/{spec[0]}", spec[3], spec[4])

    help_registry = app.bot_data.get('help_registry', {})
    registered_cmds = [item['command'] for cat in help_registry.values() for item in cat]

    assert "/history" not in registered_cmds


@pytest.mark.asyncio
async def test_id_cmd():
    update = MagicMock()
    update.message.reply_to_message = None
    update.message.entities = []
    update.effective_user.id = 777
    update.effective_chat.id = -100999
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []

    await admin.id_cmd(update, context)
    update.message.reply_text.assert_called_once_with("User ID: 777\nChat ID: -100999")


import mongomock

@pytest.mark.asyncio
async def test_save_profile_snapshot_mongodb(monkeypatch):
    client = mongomock.MongoClient()
    mock_db = client['test_db']
    monkeypatch.setattr(store, 'is_mongo', lambda: True)
    monkeypatch.setattr(store, 'get_mongo_db', lambda: mock_db)

    chat_id = -100888
    user_id = 5555

    # First save
    store.save_profile_snapshot(chat_id, user_id, 'Alice', 'Mongo', 'alice_m')
    snap = store.get_profile_snapshot(chat_id, user_id)
    assert snap is not None
    assert snap['first_name'] == 'Alice'
    assert snap['last_name'] == 'Mongo'
    assert snap['username'] == 'alice_m'

    # Update save
    store.save_profile_snapshot(chat_id, user_id, 'Alice', 'Updated', 'alice_m2')
    snap2 = store.get_profile_snapshot(chat_id, user_id)
    assert snap2['last_name'] == 'Updated'
    assert snap2['username'] == 'alice_m2'


@pytest.mark.asyncio
async def test_track_profile_update_handler_exception_logging(monkeypatch):
    chat = MagicMock(type='supergroup', id=-100123)
    user = make_user(uid=111, is_bot=False)
    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = user
    update.effective_message = MagicMock(new_chat_members=[])

    context = make_context()

    async def raise_err(*args, **kwargs):
        raise RuntimeError("Database connection lost")

    monkeypatch.setattr(bot, 'check_and_track_profile_change', raise_err)
    logged_errors = []

    def mock_log_error(msg, *args, **kwargs):
        logged_errors.append((msg, kwargs.get('exc_info')))

    monkeypatch.setattr(bot.logger, 'error', mock_log_error)

    # Execution should NOT raise exception
    await bot.track_profile_update_handler(update, context)

    assert len(logged_errors) == 1
    assert "Error in track_profile_update_handler" in logged_errors[0][0]
    assert logged_errors[0][1] is True


@pytest.mark.asyncio
async def test_handler_registration_groups(monkeypatch):
    recorded_handlers = []

    class MockApp:
        def __init__(self):
            self.bot_data = {}
        def add_handler(self, handler, group=0):
            recorded_handlers.append((handler, group))

    mock_app = MockApp()
    monkeypatch.setattr(bot, 'add_registered_command', lambda *a, **kw: None)
    monkeypatch.setattr(bot, 'register_help', lambda *a, **kw: None)

    # Simulate setup in bot.py where handlers are registered
    mock_app.add_handler(bot.MessageHandler(bot.filters.ALL & ~bot.filters.StatusUpdate.ALL, bot.night_mode_message_handler), group=-2)
    mock_app.add_handler(bot.TypeHandler(bot.Update, bot.track_profile_update_handler), group=-1)

    nm_handler = next(h for h, g in recorded_handlers if g == -2)
    tp_handler = next(h for h, g in recorded_handlers if g == -1)

    assert nm_handler.callback == bot.night_mode_message_handler
    assert tp_handler.callback == bot.track_profile_update_handler


@pytest.mark.asyncio
async def test_night_mode_on_off_deletion_behavior(monkeypatch):
    chat_id = -100777
    user_id = 8888

    # Ensure user is NOT safe user
    monkeypatch.setattr(store, 'is_safe_user', lambda c, u: False)

    context = make_context()

    # 1. Enable Night Mode
    store.set_setting(chat_id, 'night_mode', 'on')
    assert store.get_setting(chat_id, 'night_mode') == 'on'

    # Create message with sticker
    msg_sticker = MagicMock()
    msg_sticker.message_id = 101
    msg_sticker.sticker = MagicMock(is_animated=False, is_video=False)
    msg_sticker.photo = None
    msg_sticker.video = None
    msg_sticker.document = None
    msg_sticker.animation = None
    msg_sticker.audio = None
    msg_sticker.voice = None
    msg_sticker.video_note = None
    msg_sticker.story = None
    msg_sticker.paid_media = None
    msg_sticker.sender_chat = None
    msg_sticker.forward_from_chat = None
    msg_sticker.is_automatic_forward = False
    msg_sticker.text = None
    msg_sticker.caption = None
    msg_sticker.entities = []
    msg_sticker.delete = AsyncMock()

    up_sticker = MagicMock(spec=Update)
    up_sticker.message = msg_sticker
    up_sticker.edited_message = None
    up_sticker.effective_chat = MagicMock(type='supergroup', id=chat_id)
    up_sticker.effective_user = make_user(uid=user_id, is_bot=False)
    up_sticker.effective_message = msg_sticker

    deleted_sticker = await bot.process_night_mode(up_sticker, context)
    assert deleted_sticker is True
    msg_sticker.delete.assert_called_once()

    # Create message with link
    msg_link = MagicMock()
    msg_link.message_id = 102
    msg_link.sticker = None
    msg_link.photo = None
    msg_link.video = None
    msg_link.document = None
    msg_link.animation = None
    msg_link.audio = None
    msg_link.voice = None
    msg_link.video_note = None
    msg_link.story = None
    msg_link.paid_media = None
    msg_link.sender_chat = None
    msg_link.forward_from_chat = None
    msg_link.is_automatic_forward = False
    msg_link.text = 'Check out https://example.com'
    msg_link.caption = None
    entity_url = MagicMock(type='url', offset=10, length=19)
    msg_link.entities = [entity_url]
    msg_link.delete = AsyncMock()

    up_link = MagicMock(spec=Update)
    up_link.message = msg_link
    up_link.edited_message = None
    up_link.effective_chat = MagicMock(type='supergroup', id=chat_id)
    up_link.effective_user = make_user(uid=user_id, is_bot=False)
    up_link.effective_message = msg_link

    deleted_link = await bot.process_night_mode(up_link, context)
    assert deleted_link is True
    msg_link.delete.assert_called_once()

    # 2. Disable Night Mode
    store.set_setting(chat_id, 'night_mode', 'off')
    assert store.get_setting(chat_id, 'night_mode') == 'off'

    msg_sticker.delete.reset_mock()
    deleted_off = await bot.process_night_mode(up_sticker, context)
    assert deleted_off is False
    msg_sticker.delete.assert_not_called()
