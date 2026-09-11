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
