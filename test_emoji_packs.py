import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Chat, User, StickerSet, Sticker, ReactionTypeEmoji, ReactionTypeCustomEmoji

from store import (
    save_emoji_pack,
    get_emoji_pack,
    get_all_emoji_packs,
    get_all_emojis,
    delete_emoji_pack,
    init_db
)
from admin import addem_cmd, delem_cmd, packem_cmd, parse_emoji_pack_link
from handlers.ai_chat import (
    set_active_chatter,
    get_active_chatter,
    is_active_chatter,
    try_react_to_message,
    process_active_interaction_and_reaction,
    _active_chatters,
    _recent_chat_reactions
)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    # Clear emoji packs
    for p in get_all_emoji_packs():
        delete_emoji_pack(p['set_name'])
    _active_chatters.clear()
    _recent_chat_reactions.clear()


def test_parse_emoji_pack_link():
    assert parse_emoji_pack_link("https://t.me/addemoji/ExamplePack") == "ExamplePack"
    assert parse_emoji_pack_link("t.me/addemoji/Test_Pack") == "Test_Pack"
    assert parse_emoji_pack_link("addemoji/SimplePack") == "SimplePack"
    assert parse_emoji_pack_link("MyRawPack123") == "MyRawPack123"


def test_emoji_pack_store_ops():
    # Save pack
    save_emoji_pack(
        set_name="test_pack_1",
        title="Test Pack 1",
        emojis_data=[{"emoji": "🌸", "custom_emoji_id": ""}, {"emoji": "✨", "custom_emoji_id": "123"}],
        link="https://t.me/addemoji/test_pack_1"
    )

    p1 = get_emoji_pack("test_pack_1")
    assert p1 is not None
    assert p1["title"] == "Test Pack 1"
    assert p1["link"] == "https://t.me/addemoji/test_pack_1"
    assert p1["count"] == 2
    assert len(p1["emojis"]) == 2

    all_packs = get_all_emoji_packs()
    assert any(p["set_name"] == "test_pack_1" for p in all_packs)

    all_emojis = get_all_emojis()
    assert len(all_emojis) == 2

    # Delete pack
    del_res = delete_emoji_pack("test_pack_1")
    assert del_res is True
    assert get_emoji_pack("test_pack_1") is None


@pytest.mark.asyncio
async def test_addem_cmd_permissions_and_validation():
    # 1. Non-owner permission check
    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    user = MagicMock(spec=User)
    user.id = 999999  # Non-owner
    upd.effective_user = user
    upd.effective_message = msg
    upd.message = msg

    ctx = MagicMock()
    ctx.args = ["https://t.me/addemoji/SomePack"]

    await addem_cmd(upd, ctx)
    msg.reply_text.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫")

    # 2. Owner missing args
    with patch("admin.is_owner", return_value=True):
        ctx.args = []
        await addem_cmd(upd, ctx)
        msg.reply_text.assert_called_with("🌸 Usage: <code>/addem &lt;pack link&gt;</code>", parse_mode="HTML")


@pytest.mark.asyncio
async def test_addem_cmd_valid_duplicate_invalid():
    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    chat = MagicMock(spec=Chat)
    chat.id = -1001234
    user = MagicMock(spec=User)
    user.id = 111111111  # Owner
    upd.effective_user = user
    upd.effective_chat = chat
    upd.effective_message = msg
    upd.message = msg

    ctx = MagicMock()
    bot = AsyncMock()
    ctx.bot = bot
    ctx.args = ["https://t.me/addemoji/CuteAnimeEmojis"]

    # Mock Telegram StickerSet
    item1 = MagicMock()
    item1.emoji = "🌸"
    item1.custom_emoji_id = ""
    item2 = MagicMock()
    item2.emoji = "🎀"
    item2.custom_emoji_id = "55555"

    sticker_set = MagicMock(spec=StickerSet)
    sticker_set.title = "Cute Anime Emojis"
    sticker_set.stickers = [item1, item2]

    bot.get_sticker_set.return_value = sticker_set

    with patch("admin.is_owner", return_value=True):
        # Successful addition
        await addem_cmd(upd, ctx)
        assert get_emoji_pack("CuteAnimeEmojis") is not None
        assert "✨ Yay! Emoji pack added successfully desu~!" in msg.reply_text.call_args[0][0]

        # Duplicate addition
        await addem_cmd(upd, ctx)
        assert "already saved!" in msg.reply_text.call_args[0][0]

        # Invalid link / Telegram API failure
        ctx.args = ["https://t.me/addemoji/NonExistentPack"]
        bot.get_sticker_set.side_effect = Exception("Pack not found")
        await addem_cmd(upd, ctx)
        assert "couldn't find or load that emoji pack" in msg.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_delem_cmd_features():
    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    chat = MagicMock(spec=Chat)
    chat.id = -1001234
    user = MagicMock(spec=User)
    user.id = 111111111  # Owner
    upd.effective_user = user
    upd.effective_chat = chat
    upd.effective_message = msg
    upd.message = msg

    ctx = MagicMock()

    save_emoji_pack("pack_to_del", "Delete Pack", [{"emoji": "🔥", "custom_emoji_id": ""}])

    with patch("admin.is_owner", return_value=True):
        # Invalid pack ID
        ctx.args = ["non_existent_pack"]
        await delem_cmd(upd, ctx)
        msg.reply_text.assert_called_with("Uwaa~ I couldn't find that emoji pack anywhere! 🥺🔍")

        # Existing pack ID
        ctx.args = ["pack_to_del"]
        await delem_cmd(upd, ctx)
        assert "deleted successfully" in msg.reply_text.call_args[0][0]
        assert get_emoji_pack("pack_to_del") is None


@pytest.mark.asyncio
async def test_packem_cmd_features():
    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    user = MagicMock(spec=User)
    user.id = 999999  # Non-owner
    upd.effective_user = user
    upd.effective_message = msg
    upd.message = msg
    ctx = MagicMock()

    # 1. Non-owner permission check
    ctx.args = ["unknown_pack"]
    await packem_cmd(upd, ctx)
    msg.reply_text.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫")

    # 2. Owner invalid pack ID
    with patch("admin.is_owner", return_value=True):
        ctx.args = ["unknown_pack"]
        await packem_cmd(upd, ctx)
        msg.reply_text.assert_called_with("Uwaa~ I couldn't find that emoji pack anywhere! 🥺🔍")

        # 3. Owner valid pack ID
        save_emoji_pack("pack_show", "Showcase Pack", [{"emoji": "💖", "custom_emoji_id": ""}], link="https://t.me/addemoji/pack_show")
        ctx.args = ["pack_show"]
        await packem_cmd(upd, ctx)
        resp = msg.reply_text.call_args[0][0]
        assert "Showcase Pack" in resp
        assert "pack_show" in resp
        assert "💖" in resp


@pytest.mark.asyncio
async def test_active_interaction_tracking_and_targeting():
    chat_id = -100999
    user_a = 100
    user_b = 200

    # 1. Active chatter tracking functions
    set_active_chatter(chat_id, user_a)
    assert get_active_chatter(chat_id) == user_a
    assert is_active_chatter(chat_id, user_a) is True
    assert is_active_chatter(chat_id, user_b) is False

    # 2. Switching active chatter
    set_active_chatter(chat_id, user_b)
    assert get_active_chatter(chat_id) == user_b
    assert is_active_chatter(chat_id, user_a) is False

    # 3. Timeout check
    with patch("time.time", return_value=time.time() + 400):
        assert get_active_chatter(chat_id) is None
        assert is_active_chatter(chat_id, user_b) is False


@pytest.mark.asyncio
async def test_reaction_behavior_and_graceful_fallback():
    chat_id = -100888
    msg = AsyncMock(spec=Message)

    # 1. No emoji packs configured -> gracefully skip without error
    await try_react_to_message(msg, chat_id)
    msg.set_reaction.assert_not_called()

    # 2. Add emoji pack -> reacts to message
    save_emoji_pack("test_emojis", "Emojis", [{"emoji": "🎉", "custom_emoji_id": ""}])
    await try_react_to_message(msg, chat_id)
    assert msg.set_reaction.called


@pytest.mark.asyncio
async def test_process_active_interaction_targeted_reactions():
    chat_id = -100777
    user_a = MagicMock(spec=User)
    user_a.id = 101
    user_a.is_bot = False

    user_b = MagicMock(spec=User)
    user_b.id = 102
    user_b.is_bot = False

    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = "supergroup"

    save_emoji_pack("reactions_pack", "Reactions", [{"emoji": "🌟", "custom_emoji_id": ""}])

    # User A addresses Yuki -> Yuki responds and reacts
    upd1 = MagicMock(spec=Update)
    msg1 = AsyncMock(spec=Message)
    msg1.text = "Yuki hi!"
    msg1.entities = []
    msg1.reply_to_message = None

    upd1.effective_message = msg1
    upd1.effective_chat = chat
    upd1.effective_user = user_a

    ctx = MagicMock()
    bot = AsyncMock()
    bot.username = "yuki_bot"
    bot.id = 123456
    ctx.bot = bot

    with patch("handlers.ai_chat.should_trigger_yuki", return_value=(True, "hi!")):
        with patch("handlers.ai_chat.handle_ai_chat", new_callable=AsyncMock) as mock_ai:
            await process_active_interaction_and_reaction(upd1, ctx)
            await asyncio.sleep(0.01)
            assert get_active_chatter(chat_id) == 101
            msg1.set_reaction.assert_called_once()
            mock_ai.assert_called_once()

    # Follow up message from User A (active chatter) -> reacts!
    upd2 = MagicMock(spec=Update)
    msg2 = AsyncMock(spec=Message)
    msg2.text = "How are you?"
    upd2.effective_message = msg2
    upd2.effective_chat = chat
    upd2.effective_user = user_a

    with patch("handlers.ai_chat.should_trigger_yuki", return_value=(False, "")):
        await process_active_interaction_and_reaction(upd2, ctx)
        await asyncio.sleep(0.01)
        msg2.set_reaction.assert_called_once()

    # Unrelated User B sends message -> NO reaction!
    upd3 = MagicMock(spec=Update)
    msg3 = AsyncMock(spec=Message)
    msg3.text = "Hello everyone"
    upd3.effective_message = msg3
    upd3.effective_chat = chat
    upd3.effective_user = user_b

    with patch("handlers.ai_chat.should_trigger_yuki", return_value=(False, "")):
        await process_active_interaction_and_reaction(upd3, ctx)
        await asyncio.sleep(0.01)
        msg3.set_reaction.assert_not_called()
        # Active chatter is still User A
        assert get_active_chatter(chat_id) == 101

    # User B now interacts with Yuki -> Active chatter switches to User B!
    upd4 = MagicMock(spec=Update)
    msg4 = AsyncMock(spec=Message)
    msg4.text = "Yuki notice me!"
    upd4.effective_message = msg4
    upd4.effective_chat = chat
    upd4.effective_user = user_b

    with patch("handlers.ai_chat.should_trigger_yuki", return_value=(True, "notice me!")):
        with patch("handlers.ai_chat.handle_ai_chat", new_callable=AsyncMock):
            await process_active_interaction_and_reaction(upd4, ctx)
            await asyncio.sleep(0.01)
            assert get_active_chatter(chat_id) == 102
            msg4.set_reaction.assert_called_once()



@pytest.mark.asyncio
async def test_non_owner_all_pack_commands_denied():
    from admin import addpack_cmd, packs_cmd, pack_cmd, delpack_cmd, addem_cmd, delem_cmd, packem_cmd, packsem_cmd

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    user = User(id=777777, first_name="SneakyNonOwner", is_bot=False)
    upd.effective_user = user
    upd.effective_message = msg
    upd.message = msg
    ctx = MagicMock()
    ctx.args = ["test"]

    with patch("admin.is_owner", return_value=False):
        for cmd in [addpack_cmd, packs_cmd, pack_cmd, delpack_cmd, addem_cmd, delem_cmd, packem_cmd, packsem_cmd]:
            msg.reply_text.reset_mock()
            await cmd(upd, ctx)
            msg.reply_text.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫")


@pytest.mark.asyncio
async def test_active_chatter_sticker_reaction():
    chat_id = -100555
    user_a = MagicMock(spec=User)
    user_a.id = 301
    user_a.is_bot = False

    chat = MagicMock(spec=Chat)
    chat.id = chat_id
    chat.type = "supergroup"

    save_emoji_pack("sticker_reaction_pack", "Sticker React", [{"emoji": "🎉", "custom_emoji_id": ""}])
    set_active_chatter(chat_id, 301)

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    sticker_obj = MagicMock()
    sticker_obj.emoji = "😊"
    msg.sticker = sticker_obj
    msg.text = None
    msg.caption = None
    upd.effective_message = msg
    upd.effective_chat = chat
    upd.effective_user = user_a

    ctx = MagicMock()

    with patch("handlers.ai_chat.should_trigger_yuki", return_value=(False, "")):
        await process_active_interaction_and_reaction(upd, ctx)
        await asyncio.sleep(0.01)
        msg.set_reaction.assert_called_once()


@pytest.mark.asyncio
async def test_custom_emoji_fallback_and_api_error_handling():
    chat_id = -100444
    msg = AsyncMock(spec=Message)

    save_emoji_pack("custom_pack", "Custom", [{"emoji": "💖", "custom_emoji_id": "bad_id"}])

    async def mock_set_reaction(reaction=None, **kwargs):
        if reaction and type(reaction[0]).__name__ == "ReactionTypeCustomEmoji":
            raise Exception("Custom emoji not allowed")
        return True

    msg.set_reaction.side_effect = mock_set_reaction

    await try_react_to_message(msg, chat_id)
    assert msg.set_reaction.call_count == 2


@pytest.mark.asyncio
async def test_packsem_cmd_features():
    from admin import packsem_cmd, packsem_callback_handler

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    user_non_owner = User(id=888888, first_name="NonOwner", is_bot=False)
    upd.effective_user = user_non_owner
    upd.effective_message = msg
    upd.message = msg
    ctx = MagicMock()

    # 1. Non-owner permission check
    await packsem_cmd(upd, ctx)
    msg.reply_text.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫")

    user_owner = User(id=111111111, first_name="Owner", is_bot=False)
    upd.effective_user = user_owner

    with patch("admin.is_owner", return_value=True):
        # 2. Owner when no emoji packs saved
        msg.reply_text.reset_mock()
        await packsem_cmd(upd, ctx)
        msg.reply_text.assert_called_with("Aww~ I don't have any emoji packs saved yet! 🥺✨")

        # 3. Owner when emoji packs exist
        save_emoji_pack("pack_sem_1", "Emoji Pack One", [{"emoji": "🌸", "custom_emoji_id": ""}])
        save_emoji_pack("pack_sem_2", "Emoji Pack Two", [{"emoji": "✨", "custom_emoji_id": "12345"}])
        msg.reply_text.reset_mock()
        await packsem_cmd(upd, ctx)
        resp = msg.reply_text.call_args[0][0]
        assert "Stored Emoji Packs" in resp
        assert "pack_sem_1" in resp
        assert "pack_sem_2" in resp

    # 4. Non-owner callback query check
    query = AsyncMock()
    query.data = "packsem_page:0"
    upd.callback_query = query
    upd.effective_user = user_non_owner
    with patch("admin.is_owner", return_value=False):
        await packsem_callback_handler(upd, ctx)
        query.answer.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫", show_alert=True)
