import pytest
import re
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, User, Chat, MessageEntity
import config
from handlers.meters import (
    love_cmd, look_cmd, brain_cmd, stupid_cmd, crush_cmd,
    is_likely_female_name, render_progress_bar, resolve_single_target,
    get_random_couple
)
import store

OWNER_TEST_ID = 999999999


@pytest.fixture(autouse=True)
def setup_test_config(monkeypatch):
    monkeypatch.setattr(config, 'OWNER_IDS', [OWNER_TEST_ID])
    monkeypatch.setattr(config, 'OWNER_ID', OWNER_TEST_ID)


def make_update(user_id=12345, first_name="Alice", username="alice", chat_id=-100123, chat_type="group", text="/love", reply_user=None):
    update = MagicMock(spec=Update)
    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = chat_type

    user = MagicMock(spec=User)
    user.id = user_id
    user.first_name = first_name
    user.full_name = first_name
    user.username = username
    user.is_bot = False

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.from_user = user
    msg.chat = chat
    msg.entities = []
    msg.reply_html = AsyncMock()
    msg.reply_text = AsyncMock()

    if reply_user:
        reply_msg = MagicMock(spec=Message)
        reply_msg.from_user = reply_user
        msg.reply_to_message = reply_msg
    else:
        msg.reply_to_message = None

    update.effective_chat = chat
    update.effective_user = user
    update.effective_message = msg
    update.message = msg
    return update


def make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = MagicMock()
    ctx.bot.id = 888888
    return ctx


class TestMeterHelpers:
    def test_progress_bar_rendering(self):
        assert render_progress_bar(0) == "▱" * 10
        assert render_progress_bar(100) == "▰" * 10
        assert render_progress_bar(50) == ("▰" * 5) + ("▱" * 5)
        # Clamping
        assert render_progress_bar(-10) == "▱" * 10
        assert render_progress_bar(120) == "▰" * 10

    def test_is_likely_female_name(self):
        # Known common names
        assert is_likely_female_name("Sakura") is True
        assert is_likely_female_name("Alice") is True
        assert is_likely_female_name("Hinata Hyuga") is True
        assert is_likely_female_name("Princess Peach") is True
        assert is_likely_female_name("Lady Maria") is True
        assert is_likely_female_name("Elena") is True
        assert is_likely_female_name("Yuki-chan") is True

        # Non-female names / general words
        assert is_likely_female_name("John") is False
        assert is_likely_female_name("Bob") is False
        assert is_likely_female_name("Goku") is False
        assert is_likely_female_name("") is False
        assert is_likely_female_name(None) is False


class TestTargetResolution:
    @pytest.mark.asyncio
    async def test_resolve_via_reply(self):
        reply_u = MagicMock(spec=User)
        reply_u.id = 55555
        reply_u.first_name = "TargetUser"
        reply_u.username = "targetuser"
        reply_u.is_bot = False

        update = make_update(reply_user=reply_u)
        ctx = make_context()
        target = await resolve_single_target(update, ctx)
        assert target is not None
        assert target.id == 55555
        assert target.first_name == "TargetUser"

    @pytest.mark.asyncio
    async def test_resolve_via_username_arg(self):
        update = make_update()
        ctx = make_context(args=["@testgirl"])
        with patch.object(store, "get_user_by_username", return_value=(77777, "TestGirl", "testgirl")):
            target = await resolve_single_target(update, ctx)
            assert target is not None
            assert target.id == 77777
            assert target.first_name == "TestGirl"

    @pytest.mark.asyncio
    async def test_resolve_via_user_id_arg(self):
        update = make_update()
        ctx = make_context(args=["12345678"])
        with patch.object(store, "get_user_by_id", return_value=(12345678, "ResolvedName", "resolvedun")):
            target = await resolve_single_target(update, ctx)
            assert target is not None
            assert target.id == 12345678
            assert target.first_name == "ResolvedName"

    @pytest.mark.asyncio
    async def test_random_couple_selection(self):
        update = make_update(chat_type="group")
        ctx = make_context()

        # Mock store.get_group_members returning 3 users including a bot and the bot itself
        mock_members = [
            (101, "User One", "user1", False),
            (102, "User Two", "user2", False),
            (888888, "Yuuki Bot", "yuukibot", True),  # bot_id
            (103, "Another Bot", "spambot", True),     # is_bot
        ]
        with patch.object(store, "get_group_members", return_value=mock_members):
            u1, u2 = await get_random_couple(update, ctx)
            assert u1 is not None and u2 is not None
            assert u1.id != u2.id
            assert u1.id in (101, 102, 12345)
            assert u2.id in (101, 102, 12345)
            assert u1.is_bot is False
            assert u2.is_bot is False


class TestOwnerSpecialConfigurations:
    @pytest.mark.asyncio
    async def test_look_owner_infinity(self):
        # Target is owner
        owner_target = MagicMock(spec=User)
        owner_target.id = OWNER_TEST_ID
        owner_target.first_name = "Boss"
        owner_target.username = "boss"
        owner_target.is_bot = False

        update = make_update(reply_user=owner_target)
        ctx = make_context()
        await look_cmd(update, ctx)

        assert update.effective_message.reply_html.called
        call_text = update.effective_message.reply_html.call_args[0][0]
        assert "INFINITY" in call_text
        assert "♾️" in call_text
        assert "Some legends are simply beyond measurement" in call_text

    @pytest.mark.asyncio
    async def test_brain_owner_above_80(self):
        owner_target = MagicMock(spec=User)
        owner_target.id = OWNER_TEST_ID
        owner_target.first_name = "Boss"
        owner_target.username = "boss"
        owner_target.is_bot = False

        update = make_update(reply_user=owner_target)
        ctx = make_context()
        await brain_cmd(update, ctx)

        assert update.effective_message.reply_html.called
        call_text = update.effective_message.reply_html.call_args[0][0]
        # Check percentage
        m = re.search(r'(\d+)%', call_text)
        assert m is not None
        score = int(m.group(1))
        assert score > 80
        assert "supreme mind" in call_text or "visionary" in call_text

    @pytest.mark.asyncio
    async def test_stupid_owner_never_insult(self):
        owner_target = MagicMock(spec=User)
        owner_target.id = OWNER_TEST_ID
        owner_target.first_name = "Boss"
        owner_target.username = "boss"
        owner_target.is_bot = False

        update = make_update(reply_user=owner_target)
        ctx = make_context()
        await stupid_cmd(update, ctx)

        assert update.effective_message.reply_html.called
        call_text = update.effective_message.reply_html.call_args[0][0]
        assert "An intelligent person can never be stupid" in call_text
        assert "Some minds are simply beyond ordinary calculations" in call_text

    @pytest.mark.asyncio
    async def test_crush_owner_girl_name_above_90(self):
        # Sender is owner, target is girl
        girl_target = MagicMock(spec=User)
        girl_target.id = 22222
        girl_target.first_name = "Sakura"
        girl_target.username = "sakura"
        girl_target.is_bot = False

        update = make_update(user_id=OWNER_TEST_ID, first_name="Boss", reply_user=girl_target)
        ctx = make_context()
        await crush_cmd(update, ctx)

        assert update.effective_message.reply_html.called
        call_text = update.effective_message.reply_html.call_args[0][0]
        m = re.search(r'(\d+)%', call_text)
        assert m is not None
        score = int(m.group(1))
        assert score > 90
        assert "romance in full bloom" in call_text or "Kyaaa~" in call_text


class TestCommandsExecution:
    @pytest.mark.asyncio
    async def test_love_single_target(self):
        other_user = MagicMock(spec=User)
        other_user.id = 33333
        other_user.first_name = "John"
        other_user.username = "john"
        other_user.is_bot = False

        update = make_update(reply_user=other_user)
        ctx = make_context()
        await love_cmd(update, ctx)
        assert update.effective_message.reply_html.called
        text = update.effective_message.reply_html.call_args[0][0]
        assert "Love Compatibility Meter" in text
        assert "John" in text

    @pytest.mark.asyncio
    async def test_love_random_couple(self):
        update = make_update(chat_type="group")
        ctx = make_context()
        mock_members = [
            (201, "Romeo", "romeo", False),
            (202, "Juliet", "juliet", False),
        ]
        with patch.object(store, "get_group_members", return_value=mock_members):
            await love_cmd(update, ctx)
            assert update.effective_message.reply_html.called
            text = update.effective_message.reply_html.call_args[0][0]
            assert "Random Group Love Match!" in text
            assert "Compatibility" in text

    @pytest.mark.asyncio
    async def test_look_random_couple(self):
        update = make_update(chat_type="group")
        ctx = make_context()
        mock_members = [
            (201, "Ken", "ken", False),
            (202, "Barbie", "barbie", False),
        ]
        with patch.object(store, "get_group_members", return_value=mock_members):
            await look_cmd(update, ctx)
            assert update.effective_message.reply_html.called
            text = update.effective_message.reply_html.call_args[0][0]
            assert "Random Duo Looks Showcase!" in text

    @pytest.mark.asyncio
    async def test_brain_random_couple(self):
        update = make_update(chat_type="group")
        ctx = make_context()
        mock_members = [
            (201, "Einstein", "einstein", False),
            (202, "Newton", "newton", False),
        ]
        with patch.object(store, "get_group_members", return_value=mock_members):
            await brain_cmd(update, ctx)
            assert update.effective_message.reply_html.called
            text = update.effective_message.reply_html.call_args[0][0]
            assert "Random Brainpower Duo!" in text

    @pytest.mark.asyncio
    async def test_stupid_random_couple(self):
        update = make_update(chat_type="group")
        ctx = make_context()
        mock_members = [
            (201, "Dumb", "dumb", False),
            (202, "Dumber", "dumber", False),
        ]
        with patch.object(store, "get_group_members", return_value=mock_members):
            await stupid_cmd(update, ctx)
            assert update.effective_message.reply_html.called
            text = update.effective_message.reply_html.call_args[0][0]
            assert "Random Goofiness Duo!" in text

    @pytest.mark.asyncio
    async def test_crush_random_couple(self):
        update = make_update(chat_type="group")
        ctx = make_context()
        mock_members = [
            (201, "Taro", "taro", False),
            (202, "Hanako", "hanako", False),
        ]
        with patch.object(store, "get_group_members", return_value=mock_members):
            await crush_cmd(update, ctx)
            assert update.effective_message.reply_html.called
            text = update.effective_message.reply_html.call_args[0][0]
            assert "Secret Group Crush Reveal!" in text
