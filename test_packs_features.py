import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Chat, User
from store import save_sticker_pack, get_all_sticker_packs, delete_sticker_pack, get_sticker_pack
from handlers.ai_chat import get_random_sticker, get_cute_fallback_response, CUTE_FALLBACK_RESPONSES, handle_ai_chat, should_trigger_yuki
from admin import packs_cmd, delpack_cmd


def test_sticker_pack_store_ops():
    save_sticker_pack("pack1", "Title 1", [{"file_id": "stk1", "emoji": "😊"}])
    save_sticker_pack("pack2", "Title 2", [{"file_id": "stk2", "emoji": "🐱"}, {"file_id": "stk3", "emoji": "🐶"}])

    all_packs = get_all_sticker_packs()
    set_names = [p["set_name"] for p in all_packs]
    assert "pack1" in set_names
    assert "pack2" in set_names

    p2 = get_sticker_pack("pack2")
    assert p2 is not None
    assert p2["title"] == "Title 2"
    assert len(p2["stickers"]) == 2

    del_res = delete_sticker_pack("pack1")
    assert del_res is True

    delete_sticker_pack("pack2")


def test_random_sticker_selection():
    save_sticker_pack("pack_multi", "Multi Pack", [
        {"file_id": "s1", "emoji": "🌸"},
        {"file_id": "s2", "emoji": "🌸"},
        {"file_id": "s3", "emoji": "🌸"}
    ])

    selected = set()
    for _ in range(10):
        stk = get_random_sticker(chat_id=999)
        if stk:
            selected.add(stk)

    assert len(selected) > 1
    delete_sticker_pack("pack_multi")


def test_cute_fallback_response():
    resp = get_cute_fallback_response()
    assert resp in CUTE_FALLBACK_RESPONSES
    assert "AI" not in resp
    assert "API" not in resp


def test_trigger_rules():
    upd = MagicMock(spec=Update)
    msg = MagicMock(spec=Message)
    chat = MagicMock(spec=Chat)
    chat.type = "supergroup"
    msg.text = "hello everyone"
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = None
    upd.effective_message = msg
    upd.effective_chat = chat

    triggered, prompt = should_trigger_yuki(upd, bot_username="YukiBot", bot_id=123)
    assert triggered is False

    msg.text = "@YukiBot hello"
    entity = MagicMock()
    entity.type = "mention"
    entity.offset = 0
    entity.length = 8
    msg.entities = [entity]

    triggered, prompt = should_trigger_yuki(upd, bot_username="YukiBot", bot_id=123)
    assert triggered is True
    assert prompt == "hello"

    msg.text = "what do you think?"
    msg.entities = []
    reply_msg = MagicMock(spec=Message)
    reply_user = MagicMock(spec=User)
    reply_user.id = 123
    reply_user.username = "YukiBot"
    reply_msg.from_user = reply_user
    msg.reply_to_message = reply_msg

    triggered, prompt = should_trigger_yuki(upd, bot_username="YukiBot", bot_id=123)
    assert triggered is True
    assert prompt == "what do you think?"


@pytest.mark.asyncio
async def test_packs_cmd_empty():
    for p in get_all_sticker_packs():
        delete_sticker_pack(p["set_name"])

    upd = MagicMock(spec=Update)
    msg = AsyncMock(spec=Message)
    upd.effective_message = msg

    upd.effective_user.id = 99999
    with patch("admin.is_owner", lambda uid, *a, **k: True):
        await packs_cmd(upd, None)
        msg.reply_text.assert_called_with("Aww~ I don't have any sticker packs saved yet! 🥺✨")


@pytest.mark.asyncio
async def test_delpack_permissions():
    upd = MagicMock(spec=Update)
    msg = MagicMock(spec=Message)
    msg.reply_text = AsyncMock()
    upd.effective_message = msg
    upd.message = msg

    upd.effective_user.id = 12345
    with patch("admin.is_owner", lambda uid, *a, **k: False):
        await delpack_cmd(upd, None)
        msg.reply_text.assert_called_with("Gomen ne~ 🌸 /delpack is strictly for my owner desu! (⁠◕⁠‿⁠◕⁠✿⁠)")
