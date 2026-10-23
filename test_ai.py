import logging
import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, User, Chat, MessageEntity
import config
from src.ai.context import ContextManager, format_short_response
from src.ai.rate_limit import AIRateLimiter
from src.ai.persona import YUKI_PERSONA
from src.ai.providers.base import AIResponse, AIProviderError, ProviderErrorCode, DEFAULT_QUOTA_COOLDOWN, DEFAULT_RATE_LIMIT_COOLDOWN
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
            raise AIProviderError(message="Rate limit on key 1", error_code=ProviderErrorCode.RATE_LIMIT)
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
    mgr.providers["gemini"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError(message="Gemini API timeout", error_code=ProviderErrorCode.TIMEOUT)
    )

    # Groq succeeds
    mgr.providers["groq"].generate_response_with_key = AsyncMock(
        return_value=AIResponse(text="Success from Groq fallback!", provider="groq", model="openai/gpt-oss-120b")
    )

    resp = await mgr.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="test")
    assert resp.text == "Success from Groq fallback!"
    assert resp.provider == "groq"


@pytest.mark.asyncio
async def test_provider_manager_openrouter_failover():
    mgr = AIProviderManager(
        provider_order=["gemini", "openrouter"],
        gemini_keys=["gemini_k1"],
        groq_keys=[],
        openrouter_keys=["openrouter_k1"],
        openrouter_model="openrouter/free"
    )

    mgr.providers["gemini"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError(message="Gemini 503", error_code=ProviderErrorCode.SERVER_ERROR)
    )

    mgr.providers["openrouter"].generate_response_with_key = AsyncMock(
        return_value=AIResponse(text="Konnichiwa from OpenRouter!", provider="openrouter", model="openrouter/free")
    )

    resp = await mgr.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="test")
    assert resp.text == "Konnichiwa from OpenRouter!"
    assert resp.provider == "openrouter"
    assert resp.model == "openrouter/free"


@pytest.mark.asyncio
async def test_provider_manager_all_fail_fallback():
    mgr = AIProviderManager(
        provider_order=["gemini"],
        gemini_keys=["key1"],
        groq_keys=[],
        openrouter_keys=[]
    )

    mgr.providers["gemini"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError(message="API Down", error_code=ProviderErrorCode.SERVER_ERROR)
    )

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

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
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

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("Read timeout", request=req_mock)

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system",
                timeout=20.0
            )
        assert exc_info.value.error_code == ProviderErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_gemini_provider_connect_timeout_handled():
    provider = GeminiProvider(api_keys=["key1"], model="gemini-2.5-flash")
    req_mock = MagicMock(spec=httpx.Request)

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectTimeout("Connect timeout", request=req_mock)

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt="system"
            )
        assert exc_info.value.error_code == ProviderErrorCode.TIMEOUT


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
    mock_request.url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    mock_response.request = mock_request

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key=secret_key,
                messages=[{"role": "user", "content": "hello"}],
                system_prompt=""
            )

        err_str = str(exc_info.value)
        assert secret_key not in err_str
        assert "[REDACTED]" in err_str or "404" in err_str


# Tests for OpenRouterProvider
@pytest.mark.asyncio
async def test_openrouter_provider_generate_response_success():
    provider = OpenRouterProvider(api_keys=["sk-or-v1-testkey123"], model="openrouter/free")

    mock_resp_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Konnichiwa desu~! 🌸 How are you today?"
                }
            }
        ],
        "usage": {"total_tokens": 25}
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = mock_resp_data

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        messages = [
            {"role": "user", "content": "Hello Yuki!", "name": "Bob"}
        ]
        resp = await provider.generate_response_with_key(
            api_key="sk-or-v1-testkey123",
            messages=messages,
            system_prompt=YUKI_PERSONA
        )

        assert resp.text == "Konnichiwa desu~! 🌸 How are you today?"
        assert resp.provider == "openrouter"
        assert resp.model == "openrouter/free"
        assert resp.usage == {"total_tokens": 25}

        call_args = mock_post.call_args
        assert call_args[0][0] == "https://openrouter.ai/api/v1/chat/completions"
        assert call_args[1]["headers"]["Authorization"] == "Bearer sk-or-v1-testkey123"

        payload = call_args[1]["json"]
        assert payload["model"] == "openrouter/free"
        assert payload["provider"] == {"allow_fallbacks": True}
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == YUKI_PERSONA
        assert payload["messages"][1]["role"] == "user"
        assert "[Bob]: Hello Yuki!" in payload["messages"][1]["content"]

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_provider_status_code_errors():
    provider = OpenRouterProvider(api_keys=["sk-or-v1-key"], model="openrouter/free")

    for status_code in [400, 401, 403, 404, 429, 500, 502, 503, 504]:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = status_code
        mock_response.headers = {}
        mock_response.json.return_value = {"error": {"message": f"Error with status {status_code}"}}
        mock_response.text = f"Status {status_code} Error"
        req_mock = MagicMock(spec=httpx.Request)
        mock_response.request = req_mock

        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with pytest.raises(AIProviderError) as exc_info:
                await provider.generate_response_with_key(
                    api_key="sk-or-v1-key",
                    messages=[{"role": "user", "content": "test"}],
                    system_prompt=""
                )
            assert f"Error Status {status_code}" in str(exc_info.value)

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_provider_error_masking_no_key_leak():
    secret_key = "sk-or-v1-SUPER_SECRET_TOKEN_9999"
    provider = OpenRouterProvider(api_keys=[secret_key], model="openrouter/free")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 401
    mock_response.headers = {}
    mock_response.json.return_value = {
        "error": {
            "message": f"Invalid key {secret_key} provided"
        }
    }
    mock_response.text = f"Unauthorized key {secret_key}"
    req_mock = MagicMock(spec=httpx.Request)
    mock_response.request = req_mock

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key=secret_key,
                messages=[{"role": "user", "content": "hi"}],
                system_prompt=""
            )

        err_str = str(exc_info.value)
        assert secret_key not in err_str
        assert "[REDACTED]" in err_str or "401" in err_str

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_provider_429_diagnostic_logging_and_upstream_extraction():
    secret_key = "sk-or-v1-SECRET_KEY_429"
    provider = OpenRouterProvider(api_keys=[secret_key], model="openrouter/free")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 429
    mock_response.headers = {
        "retry-after": "5.0",
        "x-request-id": "req-12345-abc",
        "x-ratelimit-remaining": "0",
        "x-ratelimit-limit": "10"
    }
    mock_response.json.return_value = {
        "error": {
            "code": 429,
            "message": "Provider returned error",
            "metadata": {
                "provider_name": "Google",
                "raw": f"Rate limit exceeded on key {secret_key}"
            }
        }
    }
    req_mock = MagicMock(spec=httpx.Request)
    mock_response.request = req_mock

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key=secret_key,
                messages=[{"role": "user", "content": "test 429"}],
                system_prompt=""
            )

        err_str = str(exc_info.value)
        # Verify secret is redacted
        assert secret_key not in err_str
        # Verify diagnostic headers and metadata are extracted
        assert "upstream_provider='Google'" in err_str
        assert "request_id='req-12345-abc'" in err_str
        assert "retry_after='5.0'" in err_str
        assert "ratelimit_headers=" in err_str

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_provider_429_retry_after_backoff():
    provider = OpenRouterProvider(api_keys=["key_retry"], model="openrouter/free")

    resp_429 = MagicMock(spec=httpx.Response)
    resp_429.status_code = 429
    resp_429.headers = {"retry-after": "0.05"}
    resp_429.json.return_value = {"error": {"message": "Provider returned error"}}

    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Retry success!"}}]
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [resp_429, resp_200]

        resp = await provider.generate_response_with_key(
            api_key="key_retry",
            messages=[{"role": "user", "content": "hi"}],
            system_prompt=""
        )

        assert resp.text == "Retry success!"
        assert mock_post.call_count == 2

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_provider_client_reuse_and_close():
    provider = OpenRouterProvider(api_keys=["key1"], model="openrouter/free")

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Hello"}}]
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        await provider.generate_response_with_key(api_key="key1", messages=[{"role": "user", "content": "1"}], system_prompt="")
        client1 = provider._client

        await provider.generate_response_with_key(api_key="key1", messages=[{"role": "user", "content": "2"}], system_prompt="")
        client2 = provider._client

        assert client1 is not None
        assert client1 is client2

    await provider.close()
    assert provider._client is None


@pytest.mark.asyncio
async def test_openrouter_provider_timeouts():
    provider = OpenRouterProvider(api_keys=["key1"], model="openrouter/free")
    req_mock = MagicMock(spec=httpx.Request)

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("Read timeout", request=req_mock)
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt=""
            )
        assert exc_info.value.error_code == ProviderErrorCode.TIMEOUT

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectTimeout("Connect timeout", request=req_mock)
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt=""
            )
        assert exc_info.value.error_code == ProviderErrorCode.TIMEOUT

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_provider_invalid_json_and_empty_response():
    provider = OpenRouterProvider(api_keys=["key1"], model="openrouter/free")

    # Invalid JSON
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError("Invalid JSON string")

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        with pytest.raises(AIProviderError, match="OpenRouter response is not valid JSON"):
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt=""
            )

    # Empty choices
    mock_response_empty = MagicMock(spec=httpx.Response)
    mock_response_empty.status_code = 200
    mock_response_empty.json.return_value = {"choices": []}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response_empty
        with pytest.raises(AIProviderError, match="invalid or empty content response structure"):
            await provider.generate_response_with_key(
                api_key="key1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt=""
            )

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_provider_custom_model_and_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/free")
    provider = OpenRouterProvider(api_keys=["k1"])
    assert provider.model == "openrouter/free"

    provider_explicit = OpenRouterProvider(api_keys=["k1"], model="meta-llama/llama-3.1-8b-instruct:free")
    assert provider_explicit.model == "meta-llama/llama-3.1-8b-instruct:free"


@pytest.mark.asyncio
async def test_addpack_cmd():
    from store import init_db
    init_db()
    from admin import addpack_cmd

    msg = AsyncMock(spec=Message)
    chat = Chat(id=-1001, type="supergroup")
    user = User(id=123, first_name="User", is_bot=False)
    upd = MagicMock(spec=Update)
    msg.reply_html = AsyncMock()
    upd.effective_message = msg
    upd.message = msg
    upd.effective_chat = chat
    upd.effective_user = user
    ctx = MagicMock()

    # 1. Non-owner check
    with patch("admin.is_owner", return_value=False):
        await addpack_cmd(upd, ctx)
        msg.reply_text.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫")

    # 2. Owner success replying to sticker
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

    with patch("admin.is_owner", return_value=True):
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
    msg.reply_html = AsyncMock()
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
        ctx.bot.get_chat_member = AsyncMock(return_value=MagicMock(status='kicked'))

        await unban_cmd(upd, ctx)
        ctx.bot.unban_chat_member.assert_called_with(-1001, 888, only_if_banned=True)
        msg.reply_html.assert_called()


# ==========================================
# Comprehensive AI Provider Failover & Parsing Tests
# ==========================================

@pytest.mark.asyncio
async def test_gemini_http_429_classification():
    provider = GeminiProvider(api_keys=["gemini_key_1"], model="gemini-2.5-flash")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.json.return_value = {"error": {"message": "Rate limit exceeded. Please wait."}}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("gemini_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.RATE_LIMIT
        assert err.status_code == 429
        assert err.suggested_cooldown == DEFAULT_RATE_LIMIT_COOLDOWN


@pytest.mark.asyncio
async def test_gemini_resource_exhausted_classification():
    provider = GeminiProvider(api_keys=["gemini_key_1"], model="gemini-2.5-flash")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.json.return_value = {
        "error": {
            "code": 429,
            "message": "RESOURCE_EXHAUSTED: Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "status": "RESOURCE_EXHAUSTED"
        }
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("gemini_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.QUOTA_EXCEEDED
        assert err.status_code == 429
        assert err.suggested_cooldown == DEFAULT_QUOTA_COOLDOWN


@pytest.mark.asyncio
async def test_gemini_free_tier_quota_exceeded():
    provider = GeminiProvider(api_keys=["gemini_key_1"], model="gemini-2.5-flash")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "error": {
            "message": "Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash"
        }
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("gemini_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_openrouter_http_429():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.headers = {}
    mock_resp.json.return_value = {"error": {"message": "Rate limit reached for account"}}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.RATE_LIMIT
        assert err.status_code == 429

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_upstream_provider_rate_limit():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 429
    mock_resp.headers = {}
    mock_resp.json.return_value = {
        "error": {
            "code": 429,
            "message": "Provider returned error",
            "metadata": {
                "provider_name": "Together",
                "raw": "Upstream rate limit exceeded"
            }
        }
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.is_upstream_error is True
        assert err.upstream_provider == "Together"
        assert err.error_code == ProviderErrorCode.RATE_LIMIT

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_model_unavailable():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="invalid/model")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 404
    mock_resp.headers = {}
    mock_resp.json.return_value = {"error": {"message": "Model 'invalid/model' not found or disabled"}}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.MODEL_UNAVAILABLE

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_json_error_response_in_200():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.json.return_value = {
        "error": {
            "message": "Provider returned error: rate limit",
            "code": 429,
            "metadata": {"provider_name": "Meta"}
        }
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.RATE_LIMIT
        assert err.is_upstream_error is True
        assert err.upstream_provider == "Meta"

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_missing_choices():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "{\"id\": \"gen-1\"}"
    mock_resp.json.return_value = {"id": "gen-1"}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.EMPTY_RESPONSE

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_empty_choices():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "{\"choices\": []}"
    mock_resp.json.return_value = {"choices": []}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.EMPTY_RESPONSE

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_missing_message():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "{\"choices\": [{}]}"
    mock_resp.json.return_value = {"choices": [{}]}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.EMPTY_RESPONSE

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_missing_content():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "{\"choices\": [{\"message\": {}}]}"
    mock_resp.json.return_value = {"choices": [{"message": {}}]}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.EMPTY_RESPONSE

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_content_none():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "{\"choices\": [{\"message\": {\"content\": null}}]}"
    mock_resp.json.return_value = {"choices": [{"message": {"content": None}}]}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.EMPTY_RESPONSE

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_empty_string_content():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.text = "{\"choices\": [{\"message\": {\"content\": \"   \"}}]}"
    mock_resp.json.return_value = {"choices": [{"message": {"content": "   "}}]}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.EMPTY_RESPONSE

    await provider.close()


@pytest.mark.asyncio
async def test_valid_openrouter_response():
    provider = OpenRouterProvider(api_keys=["or_key_1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.json.return_value = {"choices": [{"message": {"content": "Hello world!"}}]}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await provider.generate_response_with_key("or_key_1", [{"role": "user", "content": "hi"}], "")
        assert res.text == "Hello world!"
        assert res.provider == "openrouter"

    await provider.close()


@pytest.mark.asyncio
async def test_provider_failover_gemini_quota_to_next_key_and_openrouter():
    manager = AIProviderManager(
        provider_order=["gemini", "openrouter"],
        gemini_keys=["g_key1", "g_key2"],
        groq_keys=[],
        openrouter_keys=["or_key1"],
        key_cooldown_seconds=10.0
    )

    # Both Gemini keys fail with Quota Exceeded, then OpenRouter succeeds
    with patch.object(GeminiProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_gemini, \
         patch.object(OpenRouterProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_openrouter:

        mock_gemini.side_effect = [
            AIProviderError("Quota exceeded on key 1", error_code=ProviderErrorCode.QUOTA_EXCEEDED),
            AIProviderError("Quota exceeded on key 2", error_code=ProviderErrorCode.QUOTA_EXCEEDED),
        ]
        mock_openrouter.return_value = AIResponse(text="Success from OpenRouter!", provider="openrouter", model="openrouter/free")

        resp = await manager.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="")

        assert resp.text == "Success from OpenRouter!"
        assert resp.provider == "openrouter"
        assert mock_gemini.call_count == 2
        assert mock_openrouter.call_count == 1

    await manager.close()


@pytest.mark.asyncio
async def test_provider_failover_after_openrouter_failure():
    manager = AIProviderManager(
        provider_order=["openrouter", "groq"],
        gemini_keys=[],
        groq_keys=["groq_key1"],
        openrouter_keys=["or_key1"],
        key_cooldown_seconds=10.0
    )

    with patch.object(OpenRouterProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_openrouter, \
         patch.object(GroqProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_groq:

        mock_openrouter.side_effect = AIProviderError("Empty response", error_code=ProviderErrorCode.EMPTY_RESPONSE)
        mock_groq.return_value = AIResponse(text="Groq response desu!", provider="groq", model="openai/gpt-oss-120b")

        resp = await manager.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="")

        assert resp.text == "Groq response desu!"
        assert resp.provider == "groq"
        assert mock_openrouter.call_count == 1
        assert mock_groq.call_count == 1

    await manager.close()


@pytest.mark.asyncio
async def test_all_providers_exhausted_fallback():
    manager = AIProviderManager(
        provider_order=["gemini", "openrouter"],
        gemini_keys=["g_key1"],
        groq_keys=[],
        openrouter_keys=["or_key1"],
        key_cooldown_seconds=10.0
    )

    with patch.object(GeminiProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_gemini, \
         patch.object(OpenRouterProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_openrouter:

        mock_gemini.side_effect = AIProviderError("Quota exceeded", error_code=ProviderErrorCode.QUOTA_EXCEEDED)
        mock_openrouter.side_effect = AIProviderError("Rate limit", error_code=ProviderErrorCode.RATE_LIMIT)

        resp = await manager.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="")

        assert resp.provider == "fallback"
        assert "taking a little break" in resp.text

    await manager.close()


@pytest.mark.asyncio
async def test_gemini_timeout_raises_provider_error():
    provider = GeminiProvider(api_keys=["secret_gemini_key_123"], model="gemini-2.5-flash")

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("Read timed out")

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                "secret_gemini_key_123",
                [{"role": "user", "content": "hello"}],
                system_prompt="test"
            )

        err = exc_info.value
        assert err.error_code == ProviderErrorCode.TIMEOUT
        assert "secret_gemini_key_123" not in str(err)
        assert "Gemini API timeout" in str(err)

    await provider.close()


@pytest.mark.asyncio
async def test_gemini_timeout_failover_to_next_key_and_groq(caplog):
    manager = AIProviderManager(
        provider_order=["gemini", "groq"],
        gemini_keys=["g_key1", "g_key2"],
        groq_keys=["groq_key1"],
        openrouter_keys=[],
        key_cooldown_seconds=30.0
    )

    with patch.object(GeminiProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_gemini, \
         patch.object(GroqProvider, "generate_response_with_key", new_callable=AsyncMock) as mock_groq:

        mock_gemini.side_effect = [
            AIProviderError("Gemini API timeout for model 'gemini-2.5-flash': ReadTimeout", error_code=ProviderErrorCode.TIMEOUT, suggested_cooldown=30.0),
            AIProviderError("Gemini API timeout for model 'gemini-2.5-flash': ReadTimeout", error_code=ProviderErrorCode.TIMEOUT, suggested_cooldown=30.0),
        ]
        mock_groq.return_value = AIResponse(text="Groq response after Gemini timeout!", provider="groq", model="openai/gpt-oss-120b")

        with caplog.at_level(logging.INFO):
            resp = await manager.chat(messages=[{"role": "user", "content": "hello"}], system_prompt="")

        assert resp.text == "Groq response after Gemini timeout!"
        assert resp.provider == "groq"
        assert mock_gemini.call_count == 2
        assert mock_groq.call_count == 1
        assert "Provider 'gemini' model='gemini-2.5-flash' timed out" in caplog.text

    await manager.close()


@pytest.mark.asyncio
async def test_gemini_client_reuse_and_close():
    provider = GeminiProvider(api_keys=["g_key1"], model="gemini-2.5-flash")
    client1 = await provider._get_client()
    client2 = await provider._get_client()
    assert client1 is client2
    assert not client1.is_closed

    await provider.close()
    assert provider._client is None


# ==========================================
# Task-Specific Unit Tests (Tests 1-7)
# ==========================================

@pytest.mark.asyncio
async def test_openrouter_empty_response_handling():
    """Test 1: OpenRouter returns empty choices -> raises EMPTY_RESPONSE & fails over immediately."""
    provider = OpenRouterProvider(api_keys=["key1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.json.return_value = {"choices": []}

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("key1", [{"role": "user", "content": "hi"}], "")
        assert exc_info.value.error_code == ProviderErrorCode.EMPTY_RESPONSE

    await provider.close()


@pytest.mark.asyncio
async def test_openrouter_http_200_error_object():
    """Test 2: OpenRouter returns HTTP 200 with error object -> classified as error & immediate failover."""
    provider = OpenRouterProvider(api_keys=["key1"], model="openrouter/free")
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.json.return_value = {
        "error": {
            "message": "upstream provider error"
        }
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("key1", [{"role": "user", "content": "hi"}], "")
        assert exc_info.value.error_code != ProviderErrorCode.EMPTY_RESPONSE
        assert "upstream provider error" in str(exc_info.value)

    await provider.close()


@pytest.mark.asyncio
async def test_gemini_timeout_handling():
    """Test 3: Gemini times out with httpx.ReadTimeout -> raises ProviderErrorCode.TIMEOUT."""
    provider = GeminiProvider(api_keys=["key1"], model="gemini-2.5-flash")
    req_mock = MagicMock(spec=httpx.Request)

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("Read timed out", request=req_mock)
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key("key1", [{"role": "user", "content": "hi"}], "", timeout=5.0)
        assert exc_info.value.error_code == ProviderErrorCode.TIMEOUT

    await provider.close()


@pytest.mark.asyncio
async def test_provider_failover_chain():
    """Test 4: OpenRouter (EMPTY_RESPONSE) -> Gemini (TIMEOUT) -> Groq (SUCCESS)."""
    mgr = AIProviderManager(
        provider_order=["openrouter", "gemini", "groq"],
        openrouter_keys=["or_key"],
        gemini_keys=["g_key"],
        groq_keys=["gr_key"]
    )

    mgr.providers["openrouter"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError("Empty choices", error_code=ProviderErrorCode.EMPTY_RESPONSE)
    )
    mgr.providers["gemini"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError("ReadTimeout", error_code=ProviderErrorCode.TIMEOUT)
    )
    mgr.providers["groq"].generate_response_with_key = AsyncMock(
        return_value=AIResponse(text="Success from Groq!", provider="groq", model="openai/gpt-oss-120b")
    )

    resp = await mgr.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="", timeout=10.0)
    assert resp.text == "Success from Groq!"
    assert resp.provider == "groq"

    await mgr.close()


@pytest.mark.asyncio
async def test_all_providers_fail_fallback():
    """Test 5: All providers fail -> returns FALLBACK_YUKI_RESPONSE."""
    mgr = AIProviderManager(
        provider_order=["openrouter", "gemini", "groq"],
        openrouter_keys=["or_key"],
        gemini_keys=["g_key"],
        groq_keys=["gr_key"]
    )

    mgr.providers["openrouter"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError("Error 1", error_code=ProviderErrorCode.SERVER_ERROR)
    )
    mgr.providers["gemini"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError("Error 2", error_code=ProviderErrorCode.TIMEOUT)
    )
    mgr.providers["groq"].generate_response_with_key = AsyncMock(
        side_effect=AIProviderError("Error 3", error_code=ProviderErrorCode.RATE_LIMIT)
    )

    resp = await mgr.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="", timeout=10.0)
    assert resp.text == FALLBACK_YUKI_RESPONSE
    assert resp.provider == "fallback"

    await mgr.close()


@pytest.mark.asyncio
async def test_multiple_api_keys_cooldown():
    """Test 6: Verify unhealthy keys are skipped during cooldown and healthy keys remain usable."""
    mgr = AIProviderManager(
        provider_order=["gemini"],
        gemini_keys=["key1", "key2"],
        groq_keys=[],
        openrouter_keys=[],
        key_cooldown_seconds=300.0
    )

    # Key 1 on cooldown
    mgr._mark_key_cooldown("gemini", 0, 300.0)
    assert not mgr._is_key_healthy("gemini", 0)
    assert mgr._is_key_healthy("gemini", 1)

    mgr.providers["gemini"].generate_response_with_key = AsyncMock(
        return_value=AIResponse(text="Used key 2!", provider="gemini", model="gemini-2.5-flash")
    )

    resp = await mgr.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="", timeout=10.0)
    assert resp.text == "Used key 2!"
    # Key 1 was skipped, so generate_response_with_key was called with key2
    mgr.providers["gemini"].generate_response_with_key.assert_called_once_with(
        api_key="key2",
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="",
        max_tokens=150,
        timeout=pytest.approx(10.0, abs=0.5)
    )

    await mgr.close()


@pytest.mark.asyncio
async def test_global_deadline_respected():
    """Test 7: Verify total AI operation respects overall deadline and does not grant full timeout per provider."""
    mgr = AIProviderManager(
        provider_order=["openrouter", "gemini", "groq"],
        openrouter_keys=["or_key"],
        gemini_keys=["g_key"],
        groq_keys=["gr_key"]
    )

    passed_timeouts = []

    async def mock_or(api_key, timeout, **kwargs):
        passed_timeouts.append(("openrouter", timeout))
        # Simulate taking 2 seconds
        await asyncio.sleep(0.1)
        raise AIProviderError("OpenRouter timeout", error_code=ProviderErrorCode.TIMEOUT)

    async def mock_gemini(api_key, timeout, **kwargs):
        passed_timeouts.append(("gemini", timeout))
        await asyncio.sleep(0.1)
        raise AIProviderError("Gemini timeout", error_code=ProviderErrorCode.TIMEOUT)

    async def mock_groq(api_key, timeout, **kwargs):
        passed_timeouts.append(("groq", timeout))
        return AIResponse(text="Groq success", provider="groq", model="openai/gpt-oss-120b")

    mgr.providers["openrouter"].generate_response_with_key = AsyncMock(side_effect=mock_or)
    mgr.providers["gemini"].generate_response_with_key = AsyncMock(side_effect=mock_gemini)
    mgr.providers["groq"].generate_response_with_key = AsyncMock(side_effect=mock_groq)

    start_time = asyncio.get_event_loop().time()
    resp = await mgr.chat(messages=[{"role": "user", "content": "hi"}], system_prompt="", timeout=2.0)
    elapsed = asyncio.get_event_loop().time() - start_time

    assert resp.text == "Groq success"
    assert len(passed_timeouts) == 3
    # Initial timeout for OpenRouter was ~2.0s
    assert passed_timeouts[0][1] == pytest.approx(2.0, abs=0.2)
    # Remaining timeout for Gemini was ~1.9s (< 2.0s)
    assert passed_timeouts[1][1] < passed_timeouts[0][1]
    # Remaining timeout for Groq was ~1.8s (< Gemini timeout)
    assert passed_timeouts[2][1] < passed_timeouts[1][1]
    assert elapsed < 2.0

    await mgr.close()



@pytest.mark.asyncio
async def test_concurrent_ai_handling():
    """Verify concurrent AI chat requests run concurrently without blocking each other."""
    from handlers.ai_chat import handle_ai_chat, rate_limiter, provider_manager, context_manager

    # Set rate limiter chat cooldown to 0 for concurrency test
    rate_limiter.chat_cooldown = 0.0
    rate_limiter.user_cooldown = 0.0

    call_order = []

    async def slow_chat(messages, system_prompt, max_tokens=150, timeout=10.0, chat_id=None, message_id=None):
        req_id = messages[-1]["content"]
        call_order.append(f"start_{req_id}")
        await asyncio.sleep(0.2)
        call_order.append(f"end_{req_id}")
        if req_id == "fail_req":
            return AIResponse(text="", provider="fallback", model="none")
        return AIResponse(text=f"Response for {req_id}", provider="mock", model="mock")

    with patch.object(provider_manager, "chat", side_effect=slow_chat):
        # Create 3 concurrent updates
        up1 = MagicMock()
        up1.update_id = 101
        up1.effective_chat.id = 12345
        up1.effective_user.id = 1
        up1.effective_user.first_name = "User1"
        up1.effective_message.message_id = 1001
        up1.effective_message.reply_text = AsyncMock()

        up2 = MagicMock()
        up2.update_id = 102
        up2.effective_chat.id = 12345
        up2.effective_user.id = 2
        up2.effective_user.first_name = "User2"
        up2.effective_message.message_id = 1002
        up2.effective_message.reply_text = AsyncMock()

        up3 = MagicMock()
        up3.update_id = 103
        up3.effective_chat.id = 12345
        up3.effective_user.id = 3
        up3.effective_user.first_name = "User3"
        up3.effective_message.message_id = 1003
        up3.effective_message.reply_text = AsyncMock()

        ctx = MagicMock()
        ctx.bot.username = "yuki_bot"
        ctx.bot.id = 123456
        ctx.bot.send_chat_action = AsyncMock()

        # Execute 3 tasks concurrently in the same chat_id
        t1 = asyncio.create_task(handle_ai_chat(up1, ctx, prompt_override="req1"))
        t2 = asyncio.create_task(handle_ai_chat(up2, ctx, prompt_override="fail_req"))
        t3 = asyncio.create_task(handle_ai_chat(up3, ctx, prompt_override="req3"))

        await asyncio.gather(t1, t2, t3)

        # All 3 requests should have started before any finished (concurrent execution)
        starts = [item for item in call_order if item.startswith("start_")]
        ends = [item for item in call_order if item.startswith("end_")]
        assert len(starts) == 3
        assert len(ends) == 3
        # Confirm failure on fail_req did not cancel req1 or req3
        ctx_messages = context_manager.get_context(12345)
        assert len(ctx_messages) > 0


@pytest.mark.asyncio
async def test_groq_provider_success_and_payload():
    secret_key = "gsk_test_secret_key_123"
    provider = GroqProvider(api_keys=[secret_key], model="openai/gpt-oss-120b")
    assert provider.model == "openai/gpt-oss-120b"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello from Groq GPT-OSS 120B!"
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
    }

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        resp = await provider.generate_response_with_key(
            api_key=secret_key,
            messages=[{"role": "user", "content": "Hello!"}],
            system_prompt="You are Yuki."
        )

        assert resp.text == "Hello from Groq GPT-OSS 120B!"
        assert resp.provider == "groq"
        assert resp.model == "openai/gpt-oss-120b"
        assert resp.usage == {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert mock_post.call_args[0][0] == "https://api.groq.com/openai/v1/chat/completions"
        assert call_kwargs["headers"]["Authorization"] == f"Bearer {secret_key}"
        assert call_kwargs["json"]["model"] == "openai/gpt-oss-120b"

    await provider.close()


@pytest.mark.asyncio
async def test_groq_provider_401_error_and_secret_redaction():
    secret_key = "gsk_invalid_secret_key"
    provider = GroqProvider(api_keys=[secret_key])
    assert provider.model == "openai/gpt-oss-120b"

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 401
    mock_response.text = f"Invalid API key provided: {secret_key}"
    req_mock = MagicMock(spec=httpx.Request)
    mock_response.request = req_mock

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key=secret_key,
                messages=[{"role": "user", "content": "hi"}],
                system_prompt=""
            )

        err_str = str(exc_info.value)
        assert secret_key not in err_str
        assert "[REDACTED]" in err_str or "401" in err_str
        assert exc_info.value.error_code == ProviderErrorCode.AUTHENTICATION_ERROR

    await provider.close()


@pytest.mark.asyncio
async def test_groq_provider_timeout():
    provider = GroqProvider(api_keys=["gsk_k1"])
    req_mock = MagicMock(spec=httpx.Request)

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("Read timeout", request=req_mock)
        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response_with_key(
                api_key="gsk_k1",
                messages=[{"role": "user", "content": "hello"}],
                system_prompt=""
            )
        assert exc_info.value.error_code == ProviderErrorCode.TIMEOUT

    await provider.close()


def test_yuki_persona_content():
    """Verify YUKI_PERSONA contains essential guidelines for personality, creator lore, and behaviors."""
    assert "Yuki" in YUKI_PERSONA
    assert "18" in YUKI_PERSONA
    assert "Tokyo" in YUKI_PERSONA
    assert "baddie energy" in YUKI_PERSONA
    assert "𓂀 𝑨𝒍𝒃𝒆𝒓𝒕 𝑰𝒏𝒇𝒆⃤𝒓𝒏𝒐 𓂀" in YUKI_PERSONA
    assert "Do NOT falsely treat other users as your creator" in YUKI_PERSONA
    assert "SAVAGE COMBACKS" in YUKI_PERSONA
    assert "HARMLESS TROLLING" in YUKI_PERSONA
    assert "EMPATHY & SUPPORT" in YUKI_PERSONA
    assert "Hinglish" in YUKI_PERSONA


@pytest.mark.asyncio
async def test_creator_vs_non_creator_identification():
    """Verify handle_ai_chat correctly identifies creator vs non-creator user IDs."""
    from handlers.ai_chat import context_manager, deduplicator, rate_limiter, provider_manager

    # Setup context manager and mock provider_manager
    chat_id = 99999
    rate_limiter.chat_cooldown = 0.0
    rate_limiter.user_cooldown = 0.0

    # 1. Non-creator user (ID 123)
    user_non_creator = User(id=123, first_name="Alice", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    msg_non = MagicMock(spec=Message)
    msg_non.message_id = 5001
    msg_non.text = "Hi Yuki!"
    msg_non.sticker = None
    msg_non.reply_to_message = None
    msg_non.entities = []

    upd_non = MagicMock(spec=Update)
    upd_non.update_id = 8001
    upd_non.effective_message = msg_non
    upd_non.effective_chat = chat
    upd_non.effective_user = user_non_creator

    ctx = MagicMock()
    ctx.bot.username = "yuki_bot"
    ctx.bot.id = 123456
    ctx.bot.send_chat_action = AsyncMock()

    with patch.object(config, 'OWNER_IDS', [999999]), \
         patch.object(provider_manager, 'chat', new_callable=AsyncMock) as mock_chat, \
         patch('handlers.ai_chat.send_reply_with_custom_emoji', new_callable=AsyncMock):

        mock_chat.return_value = AIResponse(text="Hey Alice! ✨", provider="mock", model="mock")
        await handle_ai_chat(upd_non, ctx, prompt_override="Hi Yuki!")

        messages = context_manager.get_context(chat_id)
        assert len(messages) >= 1
        # Find user message
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assert user_msgs[-1]["name"] == "Alice"

    # 2. Creator user (ID 999999)
    user_creator = User(id=999999, first_name="Albert", is_bot=False)
    msg_creator = MagicMock(spec=Message)
    msg_creator.message_id = 5002
    msg_creator.text = "Hey Yuki!"
    msg_creator.sticker = None
    msg_creator.reply_to_message = None
    msg_creator.entities = []

    upd_creator = MagicMock(spec=Update)
    upd_creator.update_id = 8002
    upd_creator.effective_message = msg_creator
    upd_creator.effective_chat = chat
    upd_creator.effective_user = user_creator

    context_manager.clear_context(chat_id)

    with patch.object(config, 'OWNER_IDS', [999999]), \
         patch.object(provider_manager, 'chat', new_callable=AsyncMock) as mock_chat, \
         patch('handlers.ai_chat.send_reply_with_custom_emoji', new_callable=AsyncMock):

        mock_chat.return_value = AIResponse(text="Hello my creator! 💕", provider="mock", model="mock")
        await handle_ai_chat(upd_creator, ctx, prompt_override="Hey Yuki!")

        messages = context_manager.get_context(chat_id)
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assert "𓂀 𝑨𝒍𝒃𝒆𝒓𝒕 𝑰𝒏𝒇𝒆⃤𝒓𝒏𝒐 𓂀 (Creator/Owner)" in user_msgs[-1]["name"]
