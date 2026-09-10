import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram.error import RetryAfter, TelegramError

import store
from admin import tag_cmd
from helpers import is_admin
import bot as bot_module


def create_mock_update_and_context(chat_id=-100123, user_id=111, first_name="Alice", chat_type="supergroup", is_admin_status=True, is_owner_status=False, is_sudo_status=False):
    update = MagicMock()
    context = MagicMock()

    msg = AsyncMock()
    update.effective_message = msg
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    update.effective_chat.title = "Test Group"
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.effective_user.full_name = first_name
    update.effective_user.username = f"user_{user_id}"
    update.effective_user.is_bot = False

    msg.text = "/tag"
    msg.caption = None
    msg.message_id = 999
    msg.reply_to_message = None

    context.args = []
    context.bot.id = 999999

    member = MagicMock()
    member.status = "administrator" if is_admin_status else "member"
    context.bot.get_chat_member = AsyncMock(return_value=member)
    context.bot.send_message = AsyncMock(return_value=MagicMock())

    return update, context


@pytest.mark.asyncio
async def test_tag_private_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    update, context = create_mock_update_and_context(chat_type="private")
    await tag_cmd(update, context)

    update.effective_message.reply_text.assert_called_once_with("🌸 This command can only be used inside a group.")


@pytest.mark.asyncio
async def test_tag_normal_user_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    update, context = create_mock_update_and_context(user_id=555, is_admin_status=False)
    await tag_cmd(update, context)

    update.effective_message.reply_text.assert_called_once()
    args, kwargs = update.effective_message.reply_text.call_args
    assert "Only group admins and the owner can use tag everyone" in args[0]
    context.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_tag_owner_success(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Member 1", "m1")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=99999, is_admin_status=False)
    monkeypatch.setattr("helpers.is_owner_or_sudo", lambda uid: uid == 99999)

    await tag_cmd(update, context)

    context.bot.send_message.assert_called_once()
    send_args = context.bot.send_message.call_args[1]
    assert "Attention everyone!" in send_args['text']
    assert "Member 1" in send_args['text']


@pytest.mark.asyncio
async def test_tag_admin_success(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Bob", "bob")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    await tag_cmd(update, context)

    context.bot.send_message.assert_called_once()
    send_args = context.bot.send_message.call_args[1]
    assert "Attention everyone!" in send_args['text']
    assert "Bob" in send_args['text']


@pytest.mark.asyncio
async def test_tag_with_custom_message(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Charlie", "charlie")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    update.effective_message.text = "/tag Good morning everyone 🌸"

    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    assert "Good morning everyone 🌸" in send_args['text']


@pytest.mark.asyncio
async def test_tag_without_message_uses_default(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "David", "david")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    update.effective_message.text = "/tag"

    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    assert "Hey everyone! 👋" in send_args['text']


@pytest.mark.asyncio
async def test_tag_replied_message(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Eve", "eve")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    update.effective_message.text = "/tag"

    replied_msg = MagicMock()
    replied_msg.text = "Important announcement!"
    replied_msg.caption = None
    update.effective_message.reply_to_message = replied_msg

    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    assert "Important announcement!" in send_args['text']


@pytest.mark.asyncio
async def test_at_all_trigger(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Frank", "frank")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    update.effective_message.text = "@all Lunch time 🍕"

    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    assert "Lunch time 🍕" in send_args['text']


@pytest.mark.asyncio
async def test_tag_excludes_bots_and_self(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Human User", "human", is_bot=False)
    store.upsert_user(102, "Bot User", "bot1", is_bot=True)
    store.upsert_user(999999, "Self Bot", "selfbot", is_bot=False)

    store.touch_member(-100123, 101)
    store.touch_member(-100123, 102)
    store.touch_member(-100123, 999999)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    context.bot.id = 999999

    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    assert "Human User" in send_args['text']
    assert "Bot User" not in send_args['text']
    assert "Self Bot" not in send_args['text']


@pytest.mark.asyncio
async def test_tag_excludes_deleted_users(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Active User", "active")
    store.upsert_user(102, "Deleted Account", None)
    store.touch_member(-100123, 101)
    store.touch_member(-100123, 102)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    assert "Active User" in send_args['text']
    assert "Deleted Account" not in send_args['text']


@pytest.mark.asyncio
async def test_tag_large_member_list_chunking(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    for i in range(1, 25):
        store.upsert_user(100 + i, f"User{i}", f"user{i}")
        store.touch_member(-100123, 100 + i)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    await tag_cmd(update, context)

    assert context.bot.send_message.call_count == 3


@pytest.mark.asyncio
async def test_tag_empty_member_list(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    await tag_cmd(update, context)

    context.bot.send_message.assert_not_called()
    update.effective_message.reply_text.assert_called_once_with("😅 I couldn't find any members to tag right now.")


@pytest.mark.asyncio
async def test_tag_flood_wait_handling(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Grace", "grace")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)

    call_count = 0
    async def mock_send(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RetryAfter(retry_after=0.1)
        return MagicMock()

    context.bot.send_message = AsyncMock(side_effect=mock_send)

    await tag_cmd(update, context)

    assert call_count == 2
    update.effective_message.reply_text.assert_called_once()
    reply_args = update.effective_message.reply_text.call_args[0][0]
    assert "Tag completed successfully!" in reply_args


@pytest.mark.asyncio
async def test_tag_special_characters_in_names(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "<b>Hack</b> & <script>", "hacker")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    update.effective_user.first_name = "<b>Admin</b> & Co"

    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    text = send_args['text']
    assert "&lt;b&gt;Hack&lt;/b&gt; &amp; &lt;script&gt;" in text
    assert "<b>Admin</b>" not in text


@pytest.mark.asyncio
async def test_tag_user_left_group(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Member Present", "present")
    store.upsert_user(102, "Member Left", "left")

    store.touch_member(-100123, 101, status='member')
    store.touch_member(-100123, 102, status='left')

    update, context = create_mock_update_and_context(user_id=222, is_admin_status=True)
    await tag_cmd(update, context)

    send_args = context.bot.send_message.call_args[1]
    assert "Member Present" in send_args['text']
    assert "Member Left" not in send_args['text']


@pytest.mark.asyncio
async def test_tag_promoted_demoted_user(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', str(tmp_path / "test_tag.db"))
    store.init_db()

    store.upsert_user(101, "Member1", "m1")
    store.touch_member(-100123, 101)

    update, context = create_mock_update_and_context(user_id=333, is_admin_status=False)
    await tag_cmd(update, context)
    context.bot.send_message.assert_not_called()

    # User promoted to admin
    promoted_member = MagicMock()
    promoted_member.status = "administrator"
    context.bot.get_chat_member = AsyncMock(return_value=promoted_member)

    await tag_cmd(update, context)
    context.bot.send_message.assert_called_once()
