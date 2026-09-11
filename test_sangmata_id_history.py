import os
import pytest
from unittest.mock import AsyncMock, MagicMock
import store
import admin

@pytest.fixture(autouse=True)
def clean_db(tmp_path):
    db_file = str(tmp_path / "test_bot.db")
    store.DB_PATH = db_file
    store.init_db()
    yield

@pytest.mark.asyncio
async def test_upsert_user_initial_name_and_dedup():
    # 1. New user -> initial name saved
    store.upsert_user(1001, "Original Name", "user1001")
    hist = store.get_user_name_history(1001)
    assert hist == ["Original Name"]

    # 4. Same name repeatedly -> no duplicates
    store.upsert_user(1001, "Original Name", "user1001")
    store.upsert_user(1001, "Original Name", "user1001_newuname")
    hist_after_repeat = store.get_user_name_history(1001)
    assert hist_after_repeat == ["Original Name"]

@pytest.mark.asyncio
async def test_user_name_changes_multiple_times():
    # 2. User changes name -> old name appears in history
    store.upsert_user(2002, "First Name", "uname")
    store.upsert_user(2002, "Second Name", "uname")
    assert store.get_user_name_history(2002) == ["First Name", "Second Name"]

    # 3. User changes name multiple times -> all distinct previous names remain
    store.upsert_user(2002, "Third Name", "uname_changed")
    assert store.get_user_name_history(2002) == ["First Name", "Second Name", "Third Name"]

@pytest.mark.asyncio
async def test_id_without_target():
    # 8. "/id" without target returns the command user's ID
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

@pytest.mark.asyncio
async def test_id_by_reply():
    # 7. "/id" by reply returns the correct user's ID
    update = MagicMock()
    reply_msg = MagicMock()
    replied_user = MagicMock()
    replied_user.id = 888
    reply_msg.from_user = replied_user

    update.message.reply_to_message = reply_msg
    update.message.entities = []
    update.effective_user.id = 777
    update.effective_chat.id = -100999
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []

    await admin.id_cmd(update, context)
    update.message.reply_text.assert_called_once_with("User ID: 888\nChat ID: -100999")

@pytest.mark.asyncio
async def test_history_without_target():
    # 6. "/history" without target works (caller's own history)
    update = MagicMock()
    update.message.reply_to_message = None
    update.message.entities = []
    update.effective_user.id = 3003
    update.effective_user.full_name = "Alice In Chains"
    update.effective_user.first_name = "Alice In Chains"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []

    # Setup history: Old name, then current name
    store.record_user_name(3003, "Alice In Wonder")
    store.record_user_name(3003, "Alice In Chains")

    await admin.history_cmd(update, context)
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]

    assert "📖 Name History" in text
    assert '👤 Current: "Alice In Chains"' in text
    assert 'Previous names:' in text
    assert '1. "Alice In Wonder"' in text
    assert "Moderation" not in text
    assert "modlog" not in text

@pytest.mark.asyncio
async def test_history_by_reply():
    # 5. "/history" by reply works
    update = MagicMock()
    reply_msg = MagicMock()
    target_user = MagicMock()
    target_user.id = 4004
    target_user.full_name = "Bob Marley New"
    target_user.first_name = "Bob Marley New"
    reply_msg.from_user = target_user

    update.message.reply_to_message = reply_msg
    update.message.entities = []
    update.effective_user.id = 111
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []

    store.record_user_name(4004, "Bob Marley Old")
    store.record_user_name(4004, "Bob Marley Middle")

    await admin.history_cmd(update, context)
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]

    assert "📖 Name History" in text
    assert '👤 Current: "Bob Marley New"' in text
    assert '1. "Bob Marley Old"' in text
    assert '2. "Bob Marley Middle"' in text

@pytest.mark.asyncio
async def test_history_no_previous_names():
    # 9. No previous names found format
    update = MagicMock()
    update.message.reply_to_message = None
    update.message.entities = []
    update.effective_user.id = 5005
    update.effective_user.full_name = "Fresh User"
    update.effective_user.first_name = "Fresh User"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []

    await admin.history_cmd(update, context)
    text = update.message.reply_text.call_args[0][0]

    assert "📖 Name History" in text
    assert '👤 Current: "Fresh User"' in text
    assert "✨ No previous names found for this user yet." in text
    assert "Moderation" not in text
