import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Chat, User, StickerSet, Sticker, ReactionTypeEmoji, ReactionTypeCustomEmoji, MessageEntity
from telegram.error import TelegramError
from config import OWNER_IDS

from store import (
    save_emoji_pack,
    get_emoji_pack,
    get_all_emoji_packs,
    get_all_emojis,
    delete_emoji_pack,
    disable_emoji_reaction,
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

    save_emoji_pack("reactions_pack", "Reactions", [{"emoji": "🎉", "custom_emoji_id": ""}])

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

    # Save custom-only emoji item with no unicode emoji
    save_emoji_pack("custom_pack", "Custom", [{"emoji": "", "custom_emoji_id": "bad_id"}])

    async def mock_set_reaction(reaction=None, **kwargs):
        if reaction and type(reaction[0]).__name__ == "ReactionTypeCustomEmoji":
            raise Exception("BadRequest: Reaction_invalid")
        return True

    msg.set_reaction.side_effect = mock_set_reaction

    # Call 1: tries ReactionTypeCustomEmoji, fails, logs error, quarantines, does NOT fall back to ReactionTypeEmoji
    await try_react_to_message(msg, chat_id)
    assert msg.set_reaction.call_count == 1
    rect_arg = msg.set_reaction.call_args[1].get("reaction") or msg.set_reaction.call_args[0][0]
    assert type(rect_arg[0]).__name__ == "ReactionTypeCustomEmoji"

    # Call 2: since bad_id is quarantined and there is no unicode emoji, try_react_to_message cleanly skips without calling set_reaction again
    msg.set_reaction.reset_mock()
    await try_react_to_message(msg, chat_id)
    msg.set_reaction.assert_not_called()


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
        assert "Emoji Packs" in resp
        kb = msg.reply_text.call_args[1]["reply_markup"].inline_keyboard
        assert kb[0][0].text == "📦 Packs"

    # 4. Non-owner callback query check
    query = AsyncMock()
    query.data = "packsem_page:0"
    upd.callback_query = query
    upd.effective_user = user_non_owner
    with patch("admin.is_owner", return_value=False):
        await packsem_callback_handler(upd, ctx)
        query.answer.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫", show_alert=True)


@pytest.mark.asyncio
async def test_invalid_unicode_emoji_skipped():
    chat_id = -100333
    msg = AsyncMock(spec=Message)

    # 😞 is not a valid Telegram reaction emoji and has no custom_emoji_id
    save_emoji_pack("invalid_pack", "Invalid", [{"emoji": "😞", "custom_emoji_id": ""}])

    await try_react_to_message(msg, chat_id)
    # Should skip set_reaction completely
    msg.set_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_cases_a_through_e_reactions():
    from handlers.ai_chat import _quarantined_reactions, _recent_chat_reactions
    _quarantined_reactions.clear()
    _recent_chat_reactions.clear()

    chat_id = -1009999

    # Case A: Valid Unicode reaction
    save_emoji_pack("unicode_pack", "Unicode", [{"emoji": "👍", "custom_emoji_id": ""}])
    msg_a = AsyncMock(spec=Message)
    await try_react_to_message(msg_a, chat_id)
    msg_a.set_reaction.assert_called_once()
    rect = msg_a.set_reaction.call_args[1]["reaction"][0]
    assert isinstance(rect, ReactionTypeEmoji)
    assert rect.emoji == "👍"
    assert "emoji:👍" in _recent_chat_reactions[chat_id]

    delete_emoji_pack("unicode_pack")
    _recent_chat_reactions.clear()

    # Case B: Valid custom emoji
    save_emoji_pack("custom_valid_pack", "Custom Valid", [{"emoji": "", "custom_emoji_id": "54321"}])
    msg_b = AsyncMock(spec=Message)
    await try_react_to_message(msg_b, chat_id)
    msg_b.set_reaction.assert_called_once()
    rect_b = msg_b.set_reaction.call_args[1]["reaction"][0]
    assert isinstance(rect_b, ReactionTypeCustomEmoji)
    assert rect_b.custom_emoji_id == "54321"
    assert "custom:54321" in _recent_chat_reactions[chat_id]

    delete_emoji_pack("custom_valid_pack")
    _recent_chat_reactions.clear()

    # Case C: Invalid custom emoji (Reaction_invalid)
    save_emoji_pack("custom_invalid_pack", "Custom Invalid", [{"emoji": "", "custom_emoji_id": "99999"}])
    msg_c = AsyncMock(spec=Message)
    msg_c.set_reaction.side_effect = Exception("BadRequest: Reaction_invalid")
    await try_react_to_message(msg_c, chat_id)

    assert "custom:99999" in _quarantined_reactions
    assert "custom:99999" not in _recent_chat_reactions.get(chat_id, [])

    # Retrying should skip quarantined emoji
    msg_c.set_reaction.reset_mock()
    await try_react_to_message(msg_c, chat_id)
    msg_c.set_reaction.assert_not_called()

    delete_emoji_pack("custom_invalid_pack")

    # Case D: Temporary API / network failure
    save_emoji_pack("temp_fail_pack", "Temp Fail", [{"emoji": "", "custom_emoji_id": "88888"}])
    msg_d = AsyncMock(spec=Message)
    msg_d.set_reaction.side_effect = Exception("Timed out network error")
    await try_react_to_message(msg_d, chat_id)

    assert "custom:88888" not in _quarantined_reactions
    assert "custom:88888" not in _recent_chat_reactions.get(chat_id, [])

    delete_emoji_pack("temp_fail_pack")

    # Case E: No valid reactions available
    msg_e = AsyncMock(spec=Message)
    await try_react_to_message(msg_e, chat_id)
    msg_e.set_reaction.assert_not_called()


@pytest.mark.asyncio
async def test_combined_item_candidate_separation():
    from handlers.ai_chat import _quarantined_reactions, _recent_chat_reactions
    _quarantined_reactions.clear()
    _recent_chat_reactions.clear()

    chat_id = -1001122
    # Item has BOTH custom_emoji_id and valid unicode emoji
    save_emoji_pack("combined_pack", "Combined", [{"emoji": "🎉", "custom_emoji_id": "998877"}])

    msg = AsyncMock(spec=Message)
    # Custom emoji fails with Reaction_invalid
    async def mock_set_reaction(reaction=None, **kwargs):
        if reaction and type(reaction[0]).__name__ == "ReactionTypeCustomEmoji":
            raise Exception("BadRequest: Reaction_invalid")
        return True

    msg.set_reaction.side_effect = mock_set_reaction

    # Keep trying until custom reaction gets chosen and quarantined
    for _ in range(20):
        await try_react_to_message(msg, chat_id)
        if "custom:998877" in _quarantined_reactions:
            break

    assert "custom:998877" in _quarantined_reactions

    # Now that custom is quarantined, try_react_to_message should still succeed using the Unicode emoji candidate 🎉
    msg.set_reaction.reset_mock()
    msg.set_reaction.side_effect = None
    await try_react_to_message(msg, chat_id)

    msg.set_reaction.assert_called_once()
    rect = msg.set_reaction.call_args[1]["reaction"][0]
    assert isinstance(rect, ReactionTypeEmoji)
    assert rect.emoji == "🎉"


@pytest.mark.asyncio
async def test_reaction_enabled_db_persistence_and_fallback():
    from handlers.ai_chat import _quarantined_reactions, _recent_chat_reactions
    _quarantined_reactions.clear()
    _recent_chat_reactions.clear()

    chat_id = -100777
    pack_name = "persisted_test_pack"

    # 1. Save pack with a custom emoji ID
    save_emoji_pack(pack_name, "Persisted Test", [{"emoji": "", "custom_emoji_id": "777111"}])

    msg = AsyncMock(spec=Message)
    async def mock_set_reaction(reaction=None, **kwargs):
        if reaction and type(reaction[0]).__name__ == "ReactionTypeCustomEmoji":
            raise Exception("BadRequest: Reaction_invalid")
        return True

    msg.set_reaction.side_effect = mock_set_reaction

    # 2. Trigger reaction attempt - Telegram rejects it
    await try_react_to_message(msg, chat_id)
    assert "custom:777111" in _quarantined_reactions

    # 3. Check DB state: reaction_enabled must be False
    pack_data = get_emoji_pack(pack_name)
    assert pack_data is not None
    assert len(pack_data["emojis"]) == 1
    assert pack_data["emojis"][0]["custom_emoji_id"] == "777111"
    assert pack_data["emojis"][0]["reaction_enabled"] == False

    # 4. Clear in-memory quarantine (simulating bot restart)
    _quarantined_reactions.clear()

    # 5. Call try_react_to_message again - disabled custom emoji should NOT be attempted
    msg.set_reaction.reset_mock()
    await try_react_to_message(msg, chat_id)
    msg.set_reaction.assert_not_called()

    # 6. Deleting the pack cleans it up from DB
    assert delete_emoji_pack(pack_name) == True
    assert get_emoji_pack(pack_name) is None


@pytest.mark.asyncio
async def test_mongo_backend_reaction_enabled_support():
    import os
    from mongomock import MongoClient

    # Temporarily switch to mongomock
    import store
    orig_is_mongo = store.is_mongo
    orig_client = store._mongo_client
    orig_db = store._mongo_db

    try:
        os.environ['MONGO_URI'] = 'mongodb://localhost:27017/test_reaction_db'
        store.is_mongo = lambda: True
        store._mongo_client = MongoClient()
        store._mongo_db = store._mongo_client.get_database('test_reaction_db')

        pack_name = "mongo_pack"
        save_emoji_pack(pack_name, "Mongo Pack", [{"emoji": "⭐", "custom_emoji_id": "999000"}])

        # Check save default
        pack = get_emoji_pack(pack_name)
        assert pack["emojis"][0]["reaction_enabled"] == True

        # Check disable
        assert disable_emoji_reaction("999000") == True

        # Check get_emoji_pack retains disabled emoji
        pack_after = get_emoji_pack(pack_name)
        assert len(pack_after["emojis"]) == 1
        assert pack_after["emojis"][0]["reaction_enabled"] == False

        # Delete pack
        assert delete_emoji_pack(pack_name) == True
        assert get_emoji_pack(pack_name) is None

    finally:
        os.environ.pop('MONGO_URI', None)
        import config
        config.MONGO_URI = ''
        store.is_mongo = orig_is_mongo
        store._mongo_client = orig_client
        store._mongo_db = orig_db






@pytest.mark.asyncio
async def test_emoji_cmd_and_reaction_integration():
    from admin import emoji_cmd
    from store import get_emoji_pack, delete_emoji_pack, get_all_emojis

    user_owner = User(id=OWNER_IDS[0] if OWNER_IDS else 111111111, first_name="Owner", is_bot=False)
    user_normal = User(id=999888, first_name="NormalUser", is_bot=False)
    chat = Chat(id=-1001, type="supergroup")

    msg_normal = AsyncMock(spec=Message)
    upd_normal = MagicMock(spec=Update)
    upd_normal.effective_user = user_normal
    upd_normal.effective_chat = chat
    upd_normal.effective_message = msg_normal
    upd_normal.message = msg_normal
    ctx_normal = MagicMock()

    # 15. Normal users cannot access /emoji
    await emoji_cmd(upd_normal, ctx_normal)
    msg_normal.reply_text.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫")

    # 1. /emoji can configure a reaction emoji (Owner user)
    msg_owner = AsyncMock(spec=Message)
    upd_owner = MagicMock(spec=Update)
    upd_owner.effective_user = user_owner
    upd_owner.effective_chat = chat
    upd_owner.effective_message = msg_owner
    upd_owner.message = msg_owner
    ctx_owner = MagicMock()
    ctx_owner.args = ["123456789", "🌸"]

    with patch("admin.is_owner", return_value=True):
        await emoji_cmd(upd_owner, ctx_owner)
    msg_owner.reply_text.assert_called()

    # Verify emoji was configured in DB
    pack = get_emoji_pack("custom_reactions")
    assert pack is not None
    emojis = pack["emojis"]
    matching = [e for e in emojis if e["custom_emoji_id"] == "123456789"]
    assert len(matching) == 1
    assert matching[0]["reaction_enabled"] is True
    assert matching[0]["emoji"] == "🌸"

    # 2. Configured emojis are actually used by the reaction system
    msg_react = AsyncMock(spec=Message)
    await try_react_to_message(msg_react, chat.id)
    msg_react.set_reaction.assert_called_once()
    reaction_args = msg_react.set_reaction.call_args[1]["reaction"]
    assert len(reaction_args) == 1
    assert getattr(reaction_args[0], "custom_emoji_id", None) == "123456789"

    # Clean up DB
    delete_emoji_pack("custom_reactions")


@pytest.mark.asyncio
async def test_reaction_rejection_does_not_disable_normal_messages():
    # 3. One rejected reaction emoji does not disable other reaction emojis
    # 4. A rejected reaction emoji remains available for normal-message use if otherwise valid
    from store import disable_emoji_reaction, save_emoji_pack, get_emoji_pack, delete_emoji_pack

    save_emoji_pack("test_pack", "Test Pack", [
        {"emoji": "", "custom_emoji_id": "11111"}
    ])

    msg_react = AsyncMock(spec=Message)

    async def mock_set_reaction(reaction):
        cid = getattr(reaction[0], "custom_emoji_id", "")
        if cid == "11111":
            raise TelegramError("Reaction_invalid: Custom emoji invalid")

    msg_react.set_reaction.side_effect = mock_set_reaction

    # Reaction attempt fails for 11111
    await try_react_to_message(msg_react, -1001)

    # Verify 11111 is disabled for reactions in store
    pack = get_emoji_pack("test_pack")
    e1 = [e for e in pack["emojis"] if e["custom_emoji_id"] == "11111"][0]
    assert e1["reaction_enabled"] is False

    # But 11111 is STILL usable in normal bot messages!
    from helpers import create_custom_emoji_entities
    entities = create_custom_emoji_entities("Hello 🌸", [{"emoji": "🌸", "custom_emoji_id": "11111"}])
    assert len(entities) == 1
    assert entities[0].custom_emoji_id == "11111"

    delete_emoji_pack("test_pack")


@pytest.mark.asyncio
async def test_normal_message_custom_emoji_utf16_and_fallback():
    # 5. Yuki can send custom emojis inside normal messages with UTF-16 entity offset calculations and graceful fallback
    from helpers import send_reply_with_custom_emoji

    msg = AsyncMock(spec=Message)
    text = "Konichiwa! 🌸 Yuki desu~ ✨"
    custom_emojis = [
        {"emoji": "🌸", "custom_emoji_id": "999888777"},
        {"emoji": "✨", "custom_emoji_id": "666555444"}
    ]

    # Success sending with custom emoji entities
    await send_reply_with_custom_emoji(msg, text, custom_emojis=custom_emojis)
    msg.reply_text.assert_called_once()
    call_args = msg.reply_text.call_args
    assert call_args[0][0] == text
    entities = call_args[1]["entities"]
    assert len(entities) == 2
    assert entities[0].type == MessageEntity.CUSTOM_EMOJI
    assert entities[0].custom_emoji_id == "999888777"
    assert entities[0].offset == 11
    assert entities[0].length == 2

    # Fallback testing when Telegram rejects custom emoji entity
    msg_fail = AsyncMock(spec=Message)
    msg_fail.reply_text.side_effect = [TelegramError("Entity custom_emoji_id invalid"), AsyncMock()]
    await send_reply_with_custom_emoji(msg_fail, text, custom_emojis=custom_emojis)
    assert msg_fail.reply_text.call_count == 2
    # Second call (fallback) had no entities
    assert "entities" not in msg_fail.reply_text.call_args_list[1][1]


@pytest.mark.asyncio
async def test_packsem_navigation_and_security():
    from admin import packsem_cmd, packsem_callback_handler
    from store import save_emoji_pack, delete_emoji_pack

    owner_id = OWNER_IDS[0] if OWNER_IDS else 111111111

    save_emoji_pack("anime_pack", "Anime Pack", [
        {"emoji": "🌸", "custom_emoji_id": "1001"},
        {"emoji": "💖", "custom_emoji_id": "1002"}
    ])
    save_emoji_pack("cute_pack", "Cute Pack", [
        {"emoji": "🐱", "custom_emoji_id": "2001"}
    ])

    user_owner = User(id=owner_id, first_name="Owner", is_bot=False)
    user_normal = User(id=888999, first_name="Hacker", is_bot=False)
    chat = Chat(id=-1001, type="supergroup")

    # 15. Normal user blocked from /packsem
    msg_norm = AsyncMock(spec=Message)
    upd_norm = MagicMock(spec=Update)
    upd_norm.effective_user = user_normal
    upd_norm.effective_chat = chat
    upd_norm.effective_message = msg_norm
    upd_norm.message = msg_norm
    ctx_norm = MagicMock()

    await packsem_cmd(upd_norm, ctx_norm)
    msg_norm.reply_text.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫")

    # 16. Callback queries cannot be manipulated by non-owner
    query_norm = AsyncMock()
    query_norm.data = "packsem_mode:packs:0"
    upd_norm.callback_query = query_norm
    await packsem_callback_handler(upd_norm, ctx_norm)
    query_norm.answer.assert_called_with("Ehehe~ that's an owner-only command! 🥺💫", show_alert=True)

    # 6. /packsem opens main menu for owner
    msg_owner = AsyncMock(spec=Message)
    upd_owner = MagicMock(spec=Update)
    upd_owner.effective_user = user_owner
    upd_owner.effective_chat = chat
    upd_owner.effective_message = msg_owner
    upd_owner.message = msg_owner
    ctx_owner = MagicMock()

    with patch("admin.is_owner", return_value=True):
        await packsem_cmd(upd_owner, ctx_owner)
    msg_owner.reply_text.assert_called_once()
    kb = msg_owner.reply_text.call_args[1]["reply_markup"].inline_keyboard
    assert len(kb) == 2
    assert kb[0][0].text == "📦 Packs"
    assert kb[1][0].text == "😀 Individual"

    # 7. /packsem -> Packs mode shows pack names as inline buttons
    query_owner = AsyncMock()
    query_owner.data = "packsem_mode:packs:0"
    upd_owner.callback_query = query_owner
    with patch("admin.is_owner", return_value=True):
        await packsem_callback_handler(upd_owner, ctx_owner)
    query_owner.edit_message_text.assert_called_once()
    p_text = query_owner.edit_message_text.call_args[0][0]
    assert "Select a Pack" in p_text
    p_kb = query_owner.edit_message_text.call_args[1]["reply_markup"].inline_keyboard
    pack_btns = [b[0].text for b in p_kb if b[0].text.startswith("📦")]
    assert "📦 Anime Pack" in pack_btns
    assert "📦 Cute Pack" in pack_btns

    # 8. Selecting a pack shows full pack details
    query_owner.reset_mock()
    query_owner.data = "packsem_view_pack:anime_pack:0"
    with patch("admin.is_owner", return_value=True):
        await packsem_callback_handler(upd_owner, ctx_owner)
    query_owner.edit_message_text.assert_called_once()
    detail_text = query_owner.edit_message_text.call_args[0][0]
    assert "Anime Pack" in detail_text
    assert "anime_pack" in detail_text
    assert "1001" in detail_text

    # 9. /packsem -> Individual mode shows pack names as inline buttons
    query_owner.reset_mock()
    query_owner.data = "packsem_mode:indiv:0"
    with patch("admin.is_owner", return_value=True):
        await packsem_callback_handler(upd_owner, ctx_owner)
    query_owner.edit_message_text.assert_called_once()
    indiv_text = query_owner.edit_message_text.call_args[0][0]
    assert "Select a Pack" in indiv_text
    indiv_kb = query_owner.edit_message_text.call_args[1]["reply_markup"].inline_keyboard
    indiv_pack_btns = [b[0].text for b in indiv_kb if b[0].text.startswith("😀")]
    assert "😀 Anime Pack" in indiv_pack_btns

    # 10, 11, 12. Selecting a pack in Individual mode shows individual emoji details
    query_owner.reset_mock()
    query_owner.data = "packsem_view_indiv:anime_pack:0"
    with patch("admin.is_owner", return_value=True):
        await packsem_callback_handler(upd_owner, ctx_owner)
    query_owner.edit_message_text.assert_called_once()
    e_detail_text = query_owner.edit_message_text.call_args[0][0]
    assert "Custom Emoji" in e_detail_text
    assert "ID: 1001" in e_detail_text or "1001" in e_detail_text
    assert "Anime Pack" in e_detail_text
    assert "<b>Pack ID:</b> <code>anime_pack</code>" in e_detail_text
    assert "<b>Reaction:</b> Enabled" in e_detail_text

    # 14. Back buttons work correctly
    query_owner.reset_mock()
    query_owner.data = "packsem_main"
    with patch("admin.is_owner", return_value=True):
        await packsem_callback_handler(upd_owner, ctx_owner)
    query_owner.edit_message_text.assert_called_once()
    main_text = query_owner.edit_message_text.call_args[0][0]
    assert "Emoji Packs" in main_text

    delete_emoji_pack("anime_pack")
    delete_emoji_pack("cute_pack")
