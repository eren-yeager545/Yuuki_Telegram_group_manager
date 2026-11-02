import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery, InlineKeyboardMarkup

import store
from src.ai.persona import (
    YUKI_PERSONA,
    RUKIA_PERSONA,
    RENJI_PERSONA,
    ZERO_TWO_PERSONA,
    PERSONAS,
    get_persona_prompt,
    get_persona_metadata
)
from handlers.ai_chat import (
    build_persona_selection_keyboard,
    send_persona_selection_menu,
    persona_callback_handler,
    show_my_persona,
    reset_my_persona,
    handle_ai_chat,
    context_manager,
    deduplicator,
    rate_limiter
)
from common import ai_cmd, mypersona_cmd, resetpersona_cmd


_msg_counter = 100

def make_mock_update(user_id=123, chat_id=-1001, text="/ai", is_group=True, username="alice", first_name="Alice", message_id=None):
    global _msg_counter
    if message_id is None:
        _msg_counter += 1
        message_id = _msg_counter

    upd = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = username
    user.first_name = first_name
    user.full_name = first_name
    user.is_bot = False

    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = "supergroup" if is_group else "private"
    chat.title = "Test Group"

    msg = MagicMock(spec=Message)
    msg.message_id = message_id
    msg.text = text
    msg.caption = None
    msg.from_user = user
    msg.chat = chat
    msg.chat_id = chat_id
    msg.sticker = None
    msg.reply_to_message = None
    msg.entities = []
    msg.reply_text = AsyncMock()

    upd.effective_user = user
    upd.effective_chat = chat
    upd.effective_message = msg
    upd.message = msg
    upd.callback_query = None
    return upd


def make_mock_callback_update(user_id=123, target_user_id=123, persona="rukia", chat_id=-1001):
    upd = MagicMock(spec=Update)
    user = MagicMock(spec=User)
    user.id = user_id
    user.username = "testuser"
    user.first_name = "Test"
    user.is_bot = False

    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = "supergroup"

    msg = MagicMock(spec=Message)
    msg.message_id = 101
    msg.chat_id = chat_id

    query = MagicMock(spec=CallbackQuery)
    query.id = "cb_123"
    query.data = f"persona:select:{persona}:{target_user_id}"
    query.from_user = user
    query.message = msg
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    upd.effective_user = user
    upd.effective_chat = chat
    upd.effective_message = msg
    upd.callback_query = query
    return upd


@pytest.fixture(autouse=True)
def init_clean_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_persona.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()
    yield


def test_default_persona_is_yuki():
    """Default persona for new/unconfigured users is 'yuki'."""
    assert store.get_user_persona(999999) == "yuki"
    assert store.get_user_persona(0) == "yuki"
    assert store.get_user_persona(None) == "yuki"


def test_persona_metadata_and_prompts():
    """Verify all 4 personas exist with required characteristics."""
    assert set(PERSONAS.keys()) == {"yuki", "rukia", "renji", "zero_two"}

    # Yuki
    assert get_persona_metadata("yuki")["name"] == "Yuki"
    assert get_persona_prompt("yuki") == YUKI_PERSONA
    assert "𝑨𝒍𝒃𝒆𝒓𝒕" in get_persona_prompt("yuki")

    # Rukia
    rukia_p = get_persona_prompt("rukia")
    assert "Rukia Kuchiki" in rukia_p
    assert "Bleach" in rukia_p
    assert "Hinglish" in rukia_p

    # Renji
    renji_p = get_persona_prompt("renji")
    assert "Renji Abarai" in renji_p
    assert "Zabimaru" in renji_p
    assert "Hinglish" in renji_p

    # Zero Two
    zt_p = get_persona_prompt("zero_two")
    assert "Zero Two" in zt_p
    assert "Darling in the Franxx" in zt_p
    assert "Aadi" in zt_p
    assert "Hinglish" in zt_p


def test_persona_persistence_sqlite():
    """Test saving, retrieving, and resetting persona in SQLite."""
    user_id = 777
    assert store.get_user_persona(user_id) == "yuki"

    assert store.set_user_persona(user_id, "renji") is True
    assert store.get_user_persona(user_id) == "renji"

    assert store.set_user_persona(user_id, "zero_two") is True
    assert store.get_user_persona(user_id) == "zero_two"

    # Invalid persona should not be set
    assert store.set_user_persona(user_id, "invalid_persona") is False
    assert store.get_user_persona(user_id) == "zero_two"

    # Reset
    assert store.reset_user_persona(user_id) is True
    assert store.get_user_persona(user_id) == "yuki"


def test_persona_persistence_mongomock(monkeypatch):
    """Test saving, retrieving, and resetting persona in MongoDB mock."""
    import mongomock
    mock_client = mongomock.MongoClient()
    mock_db = mock_client['test_persona_mongo']

    monkeypatch.setattr(store, "_mongo_db", mock_db)
    monkeypatch.setattr(store, "is_mongo", lambda: True)
    store.init_db()

    user_id = 888
    assert store.get_user_persona(user_id) == "yuki"
    assert store.set_user_persona(user_id, "rukia") is True
    assert store.get_user_persona(user_id) == "rukia"

    assert store.reset_user_persona(user_id) is True
    assert store.get_user_persona(user_id) == "yuki"


def test_delete_user_data_cleans_persona():
    """Test that delete_user_data purges persona entries."""
    user_id = 999
    store.set_user_persona(user_id, "renji")
    assert store.get_user_persona(user_id) == "renji"

    store.delete_user_data(user_id)
    assert store.get_user_persona(user_id) == "yuki"


@pytest.mark.asyncio
async def test_ai_cmd_displays_inline_keyboard():
    """Sending /ai without arguments renders the 4-persona keyboard."""
    upd = make_mock_update(user_id=123, text="/ai")
    ctx = MagicMock()
    ctx.args = []

    await ai_cmd(upd, ctx)

    upd.message.reply_text.assert_called_once()
    args, kwargs = upd.message.reply_text.call_args
    assert "Choose Your AI Persona" in args[0]
    kb: InlineKeyboardMarkup = kwargs.get("reply_markup")
    assert kb is not None
    assert len(kb.inline_keyboard) == 2  # 2 rows
    assert len(kb.inline_keyboard[0]) == 2  # 2 per row
    assert len(kb.inline_keyboard[1]) == 2

    # Check button labels
    assert "Yuki" in kb.inline_keyboard[0][0].text
    assert "Rukia" in kb.inline_keyboard[0][1].text
    assert "Renji" in kb.inline_keyboard[1][0].text
    assert "Zero Two" in kb.inline_keyboard[1][1].text


@pytest.mark.asyncio
async def test_persona_callback_selection_and_authorization():
    """Test clicking persona buttons and authorization validation."""
    # 1. Successful selection by authorized user
    upd = make_mock_callback_update(user_id=123, target_user_id=123, persona="rukia")
    ctx = MagicMock()

    await persona_callback_handler(upd, ctx)

    assert store.get_user_persona(123) == "rukia"
    upd.callback_query.answer.assert_called_with("Selected Rukia Kuchiki! ❄️")
    upd.callback_query.edit_message_text.assert_called_once()
    edit_args, edit_kwargs = upd.callback_query.edit_message_text.call_args
    assert "Rukia Kuchiki" in edit_kwargs["text"]

    # 2. Unauthorized click by a different user
    upd_unauth = make_mock_callback_update(user_id=456, target_user_id=123, persona="renji")
    await persona_callback_handler(upd_unauth, ctx)

    # Persona for target user and clicker should not change
    assert store.get_user_persona(123) == "rukia"
    assert store.get_user_persona(456) == "yuki"
    upd_unauth.callback_query.answer.assert_called_once()
    alert_text = upd_unauth.callback_query.answer.call_args[0][0]
    assert "opened for another user" in alert_text


@pytest.mark.asyncio
async def test_mypersona_and_resetpersona_commands():
    """Test /mypersona and /resetpersona commands."""
    user_id = 555
    store.set_user_persona(user_id, "zero_two")

    upd = make_mock_update(user_id=user_id, text="/mypersona")
    ctx = MagicMock()

    # /mypersona
    await mypersona_cmd(upd, ctx)
    upd.message.reply_text.assert_called_once()
    assert "Zero Two" in upd.message.reply_text.call_args[0][0]

    # /resetpersona
    upd_reset = make_mock_update(user_id=user_id, text="/resetpersona")
    await resetpersona_cmd(upd_reset, ctx)
    assert store.get_user_persona(user_id) == "yuki"
    upd_reset.message.reply_text.assert_called_once()
    assert "reset to 💗 Yuki" in upd_reset.message.reply_text.call_args[0][0] or "Yuki" in upd_reset.message_reply_text if hasattr(upd_reset, "message_reply_text") else "Yuki" in upd_reset.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_group_chat_persona_isolation():
    """Verify different users in the same group get their respective persona instructions."""
    from handlers.ai_chat import provider_manager
    from src.ai.providers.base import AIResponse

    user_a = 1001
    user_b = 1002
    user_c = 1003
    user_d = 1004

    store.set_user_persona(user_a, "yuki")
    store.set_user_persona(user_b, "rukia")
    store.set_user_persona(user_c, "renji")
    store.set_user_persona(user_d, "zero_two")

    captured_prompts = []

    async def mock_chat(messages, system_prompt, **kwargs):
        captured_prompts.append(system_prompt)
        return AIResponse(text="Test reply", provider="mock", model="mock")

    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()

    with patch.object(provider_manager, "chat", side_effect=mock_chat), \
         patch("handlers.ai_chat.send_reply_with_custom_emoji", new_callable=AsyncMock):

        # Reset rate limiter to avoid chat-level cooldown in tests
        rate_limiter._last_chat_time.clear()
        rate_limiter._last_user_time.clear()

        # User A in group
        upd_a = make_mock_update(user_id=user_a, chat_id=-999, text="Hello!")
        await handle_ai_chat(upd_a, ctx, prompt_override="Hello from A")

        rate_limiter._last_chat_time.clear()
        # User B in same group
        upd_b = make_mock_update(user_id=user_b, chat_id=-999, text="Hello!")
        await handle_ai_chat(upd_b, ctx, prompt_override="Hello from B")

        rate_limiter._last_chat_time.clear()
        # User C in same group
        upd_c = make_mock_update(user_id=user_c, chat_id=-999, text="Hello!")
        await handle_ai_chat(upd_c, ctx, prompt_override="Hello from C")

        rate_limiter._last_chat_time.clear()
        # User D in same group
        upd_d = make_mock_update(user_id=user_d, chat_id=-999, text="Hello!")
        await handle_ai_chat(upd_d, ctx, prompt_override="Hello from D")

    assert len(captured_prompts) == 4
    assert captured_prompts[0] == YUKI_PERSONA
    assert captured_prompts[1] == RUKIA_PERSONA
    assert captured_prompts[2] == RENJI_PERSONA
    assert captured_prompts[3] == ZERO_TWO_PERSONA


@pytest.mark.asyncio
async def test_same_user_consistency_across_group_and_private():
    """Verify that a user's chosen persona applies consistently across private and group chats."""
    from handlers.ai_chat import provider_manager
    from src.ai.providers.base import AIResponse

    user_id = 2001
    store.set_user_persona(user_id, "rukia")

    prompts = []

    async def mock_chat(messages, system_prompt, **kwargs):
        prompts.append(system_prompt)
        return AIResponse(text="Reply", provider="mock", model="mock")

    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()

    with patch.object(provider_manager, "chat", side_effect=mock_chat), \
         patch("handlers.ai_chat.send_reply_with_custom_emoji", new_callable=AsyncMock):

        rate_limiter._last_chat_time.clear()
        rate_limiter._last_user_time.clear()

        # Group chat
        upd_grp = make_mock_update(user_id=user_id, chat_id=-555, is_group=True, text="Group message")
        await handle_ai_chat(upd_grp, ctx, prompt_override="Group message")

        rate_limiter._last_chat_time.clear()
        rate_limiter._last_user_time.clear()

        # Private chat
        upd_pm = make_mock_update(user_id=user_id, chat_id=user_id, is_group=False, text="PM message")
        await handle_ai_chat(upd_pm, ctx, prompt_override="PM message")

    assert len(prompts) == 2
    assert prompts[0] == RUKIA_PERSONA
    assert prompts[1] == RUKIA_PERSONA
