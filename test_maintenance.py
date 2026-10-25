import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, User, Chat, Message, CallbackQuery, ChatMemberUpdated, ChatMember
from telegram.ext import ApplicationHandlerStop

import config
import store
import admin
import bot as bot_module


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_maintenance.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()
    store.set_maintenance_mode(False)


def test_maintenance_mode_persistence(tmp_path, monkeypatch):
    assert store.is_maintenance_mode() is False
    store.set_maintenance_mode(True)
    assert store.is_maintenance_mode() is True

    # Simulate re-initialization
    store.init_db()
    assert store.is_maintenance_mode() is True

    store.set_maintenance_mode(False)
    assert store.is_maintenance_mode() is False


def test_maintenance_cmd_permissions_and_toggling(monkeypatch):
    owner_id = 11111
    sudo_id = 22222
    normal_id = 33333

    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(config, "SUDO_USERS", [sudo_id])
    monkeypatch.setattr(admin, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(admin, "SUDO_USERS", [sudo_id])

    context = MagicMock()

    # 1. Normal user attempts /maintenance -> REJECTED
    normal_user = User(id=normal_id, first_name="NormalUser", is_bot=False)
    msg_normal = AsyncMock(spec=Message)
    up_normal = MagicMock(spec=Update)
    up_normal.effective_user = normal_user
    up_normal.effective_message = msg_normal

    asyncio.run(admin.maintenance_cmd(up_normal, context))
    msg_normal.reply_text.assert_called_once()
    assert "Only my owner and sudo users" in msg_normal.reply_text.call_args[0][0]
    assert store.is_maintenance_mode() is False

    # 2. Sudo user views status -> ALLOWED
    sudo_user = User(id=sudo_id, first_name="SudoUser", is_bot=False)
    msg_sudo = AsyncMock(spec=Message)
    up_sudo = MagicMock(spec=Update)
    up_sudo.effective_user = sudo_user
    up_sudo.effective_message = msg_sudo
    context.args = []

    asyncio.run(admin.maintenance_cmd(up_sudo, context))
    msg_sudo.reply_text.assert_called_once()
    assert "OFF" in msg_sudo.reply_text.call_args[0][0]

    # 3. Sudo user tries to toggle maintenance on -> REJECTED (Owner only)
    msg_sudo.reply_text.reset_mock()
    context.args = ["on"]
    asyncio.run(admin.maintenance_cmd(up_sudo, context))
    msg_sudo.reply_text.assert_called_once()
    assert "Only my owner can turn maintenance mode on or off" in msg_sudo.reply_text.call_args[0][0]
    assert store.is_maintenance_mode() is False

    # 4. Owner user turns maintenance on -> ALLOWED
    owner_user = User(id=owner_id, first_name="OwnerUser", is_bot=False)
    msg_owner = AsyncMock(spec=Message)
    up_owner = MagicMock(spec=Update)
    up_owner.effective_user = owner_user
    up_owner.effective_message = msg_owner
    context.args = ["on"]

    asyncio.run(admin.maintenance_cmd(up_owner, context))
    msg_owner.reply_text.assert_called_once()
    assert "ON" in msg_owner.reply_text.call_args[0][0]
    assert store.is_maintenance_mode() is True

    # 5. Owner user turns maintenance off -> ALLOWED
    msg_owner.reply_text.reset_mock()
    context.args = ["off"]

    asyncio.run(admin.maintenance_cmd(up_owner, context))
    msg_owner.reply_text.assert_called_once()
    assert "OFF" in msg_owner.reply_text.call_args[0][0]
    assert store.is_maintenance_mode() is False


def test_maintenance_middleware_pass_through_when_off(monkeypatch):
    store.set_maintenance_mode(False)
    update = MagicMock(spec=Update)
    update.effective_user = User(id=99999, first_name="NormalUser", is_bot=False)
    context = MagicMock()

    # Middleware should return None without raising ApplicationHandlerStop
    res = asyncio.run(bot_module.maintenance_middleware_handler(update, context))
    assert res is None


def test_maintenance_middleware_pass_through_owner_and_sudo_when_on(monkeypatch):
    owner_id = 11111
    sudo_id = 22222

    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(config, "SUDO_USERS", [sudo_id])
    monkeypatch.setattr(bot_module, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(bot_module, "SUDO_USERS", [sudo_id])

    store.set_maintenance_mode(True)
    context = MagicMock()

    # Owner update
    up_owner = MagicMock(spec=Update)
    up_owner.effective_user = User(id=owner_id, first_name="Owner", is_bot=False)
    up_owner.my_chat_member = None
    up_owner.chat_member = None

    res_owner = asyncio.run(bot_module.maintenance_middleware_handler(up_owner, context))
    assert res_owner is None

    # Sudo update
    up_sudo = MagicMock(spec=Update)
    up_sudo.effective_user = User(id=sudo_id, first_name="Sudo", is_bot=False)
    up_sudo.my_chat_member = None
    up_sudo.chat_member = None

    res_sudo = asyncio.run(bot_module.maintenance_middleware_handler(up_sudo, context))
    assert res_sudo is None


def test_maintenance_middleware_pass_through_operational_updates(monkeypatch):
    store.set_maintenance_mode(True)
    context = MagicMock()

    # my_chat_member update (e.g. bot added/removed from chat)
    up_my_chat = MagicMock(spec=Update)
    up_my_chat.effective_user = User(id=99999, first_name="NormalUser", is_bot=False)
    up_my_chat.my_chat_member = MagicMock(spec=ChatMemberUpdated)
    up_my_chat.chat_member = None

    res_my_chat = asyncio.run(bot_module.maintenance_middleware_handler(up_my_chat, context))
    assert res_my_chat is None

    # chat_member update
    up_chat_member = MagicMock(spec=Update)
    up_chat_member.effective_user = User(id=99999, first_name="NormalUser", is_bot=False)
    up_chat_member.my_chat_member = None
    up_chat_member.chat_member = MagicMock(spec=ChatMemberUpdated)

    res_chat_member = asyncio.run(bot_module.maintenance_middleware_handler(up_chat_member, context))
    assert res_chat_member is None


def test_maintenance_middleware_blocks_unauthorized_message(monkeypatch):
    owner_id = 11111
    normal_id = 99999

    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(config, "SUDO_USERS", [])
    monkeypatch.setattr(bot_module, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(bot_module, "SUDO_USERS", [])

    store.set_maintenance_mode(True)
    context = MagicMock()

    msg = AsyncMock(spec=Message)
    up = MagicMock(spec=Update)
    up.effective_user = User(id=normal_id, first_name="NormalUser", is_bot=False)
    up.effective_message = msg
    up.callback_query = None
    up.my_chat_member = None
    up.chat_member = None

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot_module.maintenance_middleware_handler(up, context))

    msg.reply_text.assert_called_once()
    assert "undergoing scheduled maintenance" in msg.reply_text.call_args[0][0]


def test_maintenance_middleware_blocks_unauthorized_callback_query(monkeypatch):
    owner_id = 11111
    normal_id = 99999

    monkeypatch.setattr(config, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(config, "SUDO_USERS", [])
    monkeypatch.setattr(bot_module, "OWNER_IDS", [owner_id])
    monkeypatch.setattr(bot_module, "SUDO_USERS", [])

    store.set_maintenance_mode(True)
    context = MagicMock()

    cb_query = AsyncMock(spec=CallbackQuery)
    up = MagicMock(spec=Update)
    up.effective_user = User(id=normal_id, first_name="NormalUser", is_bot=False)
    up.effective_message = None
    up.callback_query = cb_query
    up.my_chat_member = None
    up.chat_member = None

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot_module.maintenance_middleware_handler(up, context))

    cb_query.answer.assert_called_once()
    assert "undergoing scheduled maintenance" in cb_query.answer.call_args[0][0]
    assert cb_query.answer.call_args[1].get("show_alert") is True
