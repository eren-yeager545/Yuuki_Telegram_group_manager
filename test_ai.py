import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, User, Chat, MessageEntity
import config
from src.ai.context import ContextManager, format_short_response
from src.ai.rate_limit import AIRateLimiter
from src.ai.persona import YUKI_PERSONA
from src.ai.providers.base import AIResponse
from src.ai.providers.gemini import GeminiProvider
from src.ai.providers.groq import GroqProvider
from src.ai.providers.openrouter import OpenRouterProvider
from src.ai.manager import AIProviderManager, FALLBACK_YUKI_RESPONSE
from handlers.ai_chat import should_trigger_yuki, handle_ai_chat


def test_format_short_response():
    # Test short text remains intact
    assert format_short_response("H-hi! 🌸 How are you?", max_words=50) == "H-hi! 🌸 How are you?"

    # Test sentence trimming
    long_text = "Sentence one is here. Sentence two is here. Sentence three is here. Sentence four is here."
    shortened = format_short_response(long_text, max_words=8)
    assert shortened == "Sentence one is here. Sentence two is here."


def test_context_manager():
    cm = ContextManager(max_messages=3)
    cm.add_message(101, "user", "Hello", user_name="Alice")
    cm.add_message(101, "assistant", "Hi there!")
    cm.add_message(101, "user", "How are you?", user_name="Alice")
    cm.add_message(101, "assistant", "I am great!")

    ctx = cm.get_context(101)
    assert len(ctx) == 3
    assert ctx[0]["content"] == "Hi there!"
    assert ctx[-1]["content"] == "I am great!"

    cm.clear_context(101)
    assert len(cm.get_context(101)) == 0


def test_rate_limiter():
    limiter = AIRateLimiter(user_cooldown=1.0, chat_cooldown=0.5, max_concurrent=2)

    allowed, msg = limiter.check_rate_limit(user_id=1, chat_id=100)
    assert allowed is True
    assert msg is None

    limiter.update_rate_limit(user_id=1, chat_id=100)

    # Immediately check again -> should block
    allowed_again, msg_again = limiter.check_rate_limit(user_id=1, chat_id=100)
    assert allowed_again is False
    assert "one question at a time" in msg_again


def test_should_trigger_yuki():
    bot_user = User(id=999, first_name="YukiBot", is_bot=True, username="YukiBot")

    # 1. Private chat
    priv_chat = Chat(id=1, type="private")
    msg_priv = MagicMock(spec=Message)
    msg_priv.text = "Hello Yuki!"
    msg_priv.caption = None
    msg_priv.entities = []
    msg_priv.reply_to_message = None

    upd_priv = MagicMock(spec=Update)
    upd_priv.effective_message = msg_priv
    upd_priv.effective_chat = priv_chat

    triggered, prompt = should_trigger_yuki(upd_priv, bot_username="YukiBot", bot_id=999)
    assert triggered is True
    assert prompt == "Hello Yuki!"

    # 2. Normal group message without mention -> should IGNORE
    group_chat = Chat(id=-100, type="supergroup")
    msg_normal = MagicMock(spec=Message)
    msg_normal.text = "What is everyone doing today?"
    msg_normal.caption = None
    msg_normal.entities = []
    msg_normal.reply_to_message = None

    upd_normal = MagicMock(spec=Update)
    upd_normal.effective_message = msg_normal
    upd_normal.effective_chat = group_chat

    triggered, prompt = should_trigger_yuki(upd_normal, bot_username="YukiBot", bot_id=999)
    assert triggered is False

    # 3. Mention entity
    entity = MessageEntity(type="mention", offset=0, length=8)
    msg_mention = MagicMock(spec=Message)
    msg_mention.text = "@YukiBot how are you?"
    msg_mention.caption = None
    msg_mention.entities = [entity]
    msg_mention.reply_to_message = None

    upd_mention = MagicMock(spec=Update)
    upd_mention.effective_message = msg_mention
    upd_mention.effective_chat = group_chat

    triggered, prompt = should_trigger_yuki(upd_mention, bot_username="YukiBot", bot_id=999)
    assert triggered is True
    assert prompt == "how are you?"

    # 4. Reply to Yuki message
    reply_msg = MagicMock(spec=Message)
    reply_msg.from_user = bot_user

    msg_reply = MagicMock(spec=Message)
    msg_reply.text = "I love anime too!"
    msg_reply.caption = None
    msg_reply.entities = []
    msg_reply.reply_to_message = reply_msg

    upd_reply = MagicMock(spec=Update)
    upd_reply.effective_message = msg_reply
    upd_reply.effective_chat = group_chat

    triggered, prompt = should_trigger_yuki(upd_reply, bot_username="YukiBot", bot_id=999)
    assert triggered is True
    assert prompt == "I love anime too!"


@pytest.mark.asyncio
async def test_provider_manager_rotation_and_failover():
    mgr = AIProviderManager(
        provider_order=["gemini", "groq", "openrouter"],
        gemini_keys=["gemini_k1", "gemini_k2"],
        groq_keys=["groq_k1"],
        openrouter_keys=["openrouter_k1"],
        key_cooldown_seconds=60.0
    )

    async def mock_gemini_resp(api_key, **kwargs):
        if api_key == "gemini_k1":
            raise RuntimeError("Rate limit on key 1")
        return AIResponse(text="Success from Gemini key 2!", provider="gemini", model="gemini-2.5-flash")

    mgr.providers["gemini"].generate_response_with_key = AsyncMock(side_effect=mock_gemini_resp)

    resp = await mgr.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="test")
    assert resp.text == "Success from Gemini key 2!"
    assert resp.provider == "gemini"


@pytest.mark.asyncio
async def test_provider_manager_fallback_on_gemini_read_timeout():
    mgr = AIProviderManager(
        provider_order=["gemini", "groq"],
        gemini_keys=["gemini_k1"],
        groq_keys=["groq_k1"],
        openrouter_keys=[],
        key_cooldown_seconds=60.0
    )

    # Gemini fails with ReadTimeout
    request_mock = MagicMock(spec=httpx.Request)
    mgr.providers["gemini"].generate_response_with_key = AsyncMock(
        side_effect=httpx.ReadTimeout("ReadTimeout after 60s", request=request_mock)
    )

    # Groq succeeds
    mgr.providers["groq"].generate_response_with_key = AsyncMock(
        return_value=AIResponse(text="Success from Groq fallback!", provider="groq", model="llama-3.3-70b-versatile")
    )

    resp = await mgr.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="test")
    assert resp.text == "Success from Groq fallback!"
    assert resp.provider == "groq"


@pytest.mark.asyncio
async def test_provider_manager_all_fail_fallback():
    mgr = AIProviderManager(
        provider_order=["gemini"],
        gemini_keys=["key1"],
        groq_keys=[],
        openrouter_keys=[]
    )

    mgr.providers["gemini"].generate_response_with_key = AsyncMock(side_effect=RuntimeError("API Down"))

    resp = await mgr.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="test")
    assert resp.text == FALLBACK_YUKI_RESPONSE
    assert resp.provider == "fallback"


def test_game_independence():
    import tictactoe
    assert not hasattr(tictactoe, "ai_manager")
    assert not hasattr(tictactoe, "handle_ai_chat")
    assert tictactoe.check_winner(["X", "X", "X", " ", " ", " ", " ", " ", " "]) == "X"


@pytest.mark.asyncio
async def test_provider_manager_no_keys_configured():
    mgr = AIProviderManager(
        provider_order=["gemini", "groq", "openrouter"],
        gemini_keys=[],
        groq_keys=[],
        openrouter_keys=[]
    )

    resp = await mgr.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="test")
    assert resp.text == FALLBACK_YUKI_RESPONSE
    assert resp.provider == "fallback"
    assert resp.model == "none"


@pytest.mark.asyncio
async def test_gemini_provider_generate_response_success():
    provider = GeminiProvider(api_keys=["test_key_123"], model="gemini-2.5-flash")

    mock_resp_data = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Hello desu~! 🌸"}],
                    "role": "model"
                },
                "finishReason": "STOP"
            }
        ],
        "usageMetadata": {"totalTokenCount": 15}
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = mock_resp_data

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        messages = [
            {"role": "user", "content": "Hi Yuki!", "name": "Alice"}
        ]
        resp = await provider.generate_response_with_key(
            api_key="test_key_123",
            messages=messages,
            system_prompt="You are Yuki"
        )

        assert resp.text == "Hello desu~! 🌸"
        assert resp.provider == "gemini"
        assert resp.model == "gemini-2.5-flash"

        call_args = mock_post.call_args
        assert "v1beta/models/gemini-2.5-flash:generateContent" in call_args[0][0]
        assert call_args[1]["headers"]["x-goog-api-key"] == "test_key_123"

        payload = call_args[1]["json"]
        assert payload["systemInstruction"]["parts"][0]["text"] == "You are Yuki"
        assert payload["contents"][0]["role"] == "user"
        assert "[Alice]: Hi Yuki!" in payload["contents"][0]["parts"][0]["text"]
        assert payload["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0


@pytest.mark.asyncio
async def test_gemini_provider_read_timeout_handled():
    provider = GeminiProvider(api_keys=["key1"], model="gemini-2.5-flash")
    req_mock = MagicMock(spec=httpx.Request)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("Read timeout", request=req_mock)

        with pytest.raises(httpx.ReadTimeout):
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system",
                timeout=20.0
            )


@pytest.mark.asyncio
async def test_gemini_provider_connect_timeout_handled():
    provider = GeminiProvider(api_keys=["key1"], model="gemini-2.5-flash")
    req_mock = MagicMock(spec=httpx.Request)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectTimeout("Connect timeout", request=req_mock)

        with pytest.raises(httpx.ConnectTimeout):
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system"
            )


@pytest.mark.asyncio
async def test_gemini_provider_custom_model_and_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    provider = GeminiProvider(api_keys=["k1"])
    assert provider.model == "gemini-2.5-flash"

    provider_explicit = GeminiProvider(api_keys=["k1"], model="gemini-1.5-flash")
    assert provider_explicit.model == "gemini-1.5-flash"


@pytest.mark.asyncio
async def test_gemini_provider_error_masking_no_key_leak():
    secret_key = "AIzaSySECRET_KEY_12345"
    provider = GeminiProvider(api_keys=[secret_key], model="gemini-2.5-flash")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 404
    mock_response.json.return_value = {
        "error": {
            "code": 404,
            "message": f"models/gemini-2.5-flash is not found for key {secret_key}"
        }
    }
    mock_response.text = f"Error with key {secret_key}"
    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    mock_response.request = mock_request

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await provider.generate_response_with_key(
                api_key=secret_key,
                messages=[{"role": "user", "content": "hello"}],
                system_prompt=""
            )

        err_str = str(exc_info.value)
        assert secret_key not in err_str
        assert "[REDACTED]" in err_str or "404" in err_str



@pytest.mark.asyncio
async def test_addpack_cmd():
    from store import init_db
    init_db()
    from admin import addpack_cmd

    msg = AsyncMock(spec=Message)
    chat = Chat(id=-1001, type="supergroup")
    user = User(id=123, first_name="User", is_bot=False)
    upd = MagicMock(spec=Update)
    upd.effective_message = msg
    upd.message = msg
    upd.effective_chat = chat
    upd.effective_user = user
    ctx = MagicMock()

    # 1. Non-admin check
    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = False
        await addpack_cmd(upd, ctx)
        msg.reply_text.assert_called_with("Gomen ne~ 🌸 /addpack is only for admins desu!")

    # 2. Admin success replying to sticker
    sticker = MagicMock()
    sticker.set_name = "test_pack_set"
    reply_msg = MagicMock(spec=Message)
    reply_msg.sticker = sticker
    msg.reply_to_message = reply_msg

    s1 = MagicMock()
    s1.file_id = "stk_1"
    s1.file_unique_id = "uniq_1"
    s1.emoji = "🌸"
    s1.type = "regular"

    sticker_set = MagicMock()
    sticker_set.title = "Test Pack Title"
    sticker_set.stickers = [s1]

    ctx.bot.get_sticker_set = AsyncMock(return_value=sticker_set)

    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = True
        await addpack_cmd(upd, ctx)
        ctx.bot.get_sticker_set.assert_called_with("test_pack_set")
        msg.reply_text.assert_called()

    # Check database
    from store import get_sticker_pack
    pack = get_sticker_pack("test_pack_set")
    assert pack is not None
    assert pack['title'] == "Test Pack Title"
    assert len(pack['stickers']) == 1
    assert pack['stickers'][0]['file_id'] == "stk_1"


@pytest.mark.asyncio
async def test_unban_cmd_enhanced():
    from admin import unban_cmd

    chat = Chat(id=-1001, type="supergroup")
    user = User(id=1, first_name="Admin", is_bot=False)
    msg = AsyncMock(spec=Message)
    upd = MagicMock(spec=Update)
    upd.effective_chat = chat
    upd.effective_user = user
    upd.effective_message = msg
    upd.message = msg
    ctx = MagicMock()

    # Admin check failure
    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin:
        mock_is_admin.return_value = False
        await unban_cmd(upd, ctx)
        msg.reply_text.assert_called_with("Gomen ne~ 🌸 /unban command is only for admins desu!")

    # Success with reply target
    target_user = User(id=888, first_name="BannedUser", is_bot=False, username="banned_user")
    with patch("admin.is_admin", new_callable=AsyncMock) as mock_is_admin,          patch("admin.resolve_target_user", new_callable=AsyncMock) as mock_target:
        mock_is_admin.return_value = True
        mock_target.return_value = target_user
        ctx.bot.unban_chat_member = AsyncMock()

        await unban_cmd(upd, ctx)
        ctx.bot.unban_chat_member.assert_called_with(-1001, 888, only_if_banned=True)
        msg.reply_html.assert_called()
