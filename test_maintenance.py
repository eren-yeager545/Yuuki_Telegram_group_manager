import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Message, Chat, User, CallbackQuery
from telegram.ext import ContextTypes, ApplicationHandlerStop

import config
import store
from store import init_db, set_maintenance_mode, is_maintenance_mode
from admin import maintenance_cmd
from bot import maintenance_handler, MAINTENANCE_MESSAGE


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    set_maintenance_mode(False)


def test_maintenance_mode_persistence():
    assert is_maintenance_mode() is False

    set_maintenance_mode(True)
    assert is_maintenance_mode() is True

    # Check persistence directly via store get_setting
    val = store.get_setting(0, 'maintenance_mode')
    assert val == 'on'

    set_maintenance_mode(False)
    assert is_maintenance_mode() is False
    val = store.get_setting(0, 'maintenance_mode')
    assert val == 'off'


@pytest.mark.asyncio
async def test_maintenance_cmd_permissions_and_status():
    owner_id = 111111111
    sudo_id = 222222222
    regular_id = 333333333

    chat = Chat(id=1001, type="private")
    owner_user = User(id=owner_id, first_name="Owner", is_bot=False)
    sudo_user = User(id=sudo_id, first_name="Sudo", is_bot=False)
    regular_user = User(id=regular_id, first_name="Regular", is_bot=False)

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    def build_upd(user, args=None):
        upd = MagicMock(spec=Update)
        msg = AsyncMock(spec=Message)
        upd.effective_chat = chat
        upd.effective_user = user
        upd.effective_message = msg
        upd.message = msg
        ctx.args = args or []
        return upd, msg

    # 1. Non-owner (regular user) attempts /maintenance on -> Denied
    upd_reg, msg_reg = build_upd(regular_user, args=['on'])
    with patch("admin.OWNER_IDS", [owner_id]):
        await maintenance_cmd(upd_reg, ctx)
        assert is_maintenance_mode() is False
        msg_reg.reply_text.assert_called_once()
        assert "Only my owner" in msg_reg.reply_text.call_args[0][0]

    # 2. Sudo user attempts /maintenance on -> Denied (only bot owner can enable/disable)
    upd_sudo, msg_sudo = build_upd(sudo_user, args=['on'])
    with patch("admin.OWNER_IDS", [owner_id]), patch("admin.SUDO_USERS", [sudo_id]):
        await maintenance_cmd(upd_sudo, ctx)
        assert is_maintenance_mode() is False
        msg_sudo.reply_text.assert_called_once()
        assert "Only my owner" in msg_sudo.reply_text.call_args[0][0]

    # 3. Owner executes /maintenance on -> Enabled
    upd_owner, msg_owner = build_upd(owner_user, args=['on'])
    with patch("admin.OWNER_IDS", [owner_id]):
        await maintenance_cmd(upd_owner, ctx)
        assert is_maintenance_mode() is True
        msg_owner.reply_html.assert_called_once()
        assert "Maintenance mode is now <b>ON</b>" in msg_owner.reply_html.call_args[0][0]

    # 4. Status check when maintenance is ON
    upd_status, msg_status = build_upd(sudo_user, args=[])
    with patch("admin.OWNER_IDS", [owner_id]), patch("admin.SUDO_USERS", [sudo_id]):
        await maintenance_cmd(upd_status, ctx)
        msg_status.reply_html.assert_called_once()
        assert "currently <b>ON</b>" in msg_status.reply_html.call_args[0][0]

    # 5. Owner executes /maintenance off -> Disabled
    upd_owner_off, msg_owner_off = build_upd(owner_user, args=['off'])
    with patch("admin.OWNER_IDS", [owner_id]):
        await maintenance_cmd(upd_owner_off, ctx)
        assert is_maintenance_mode() is False
        msg_owner_off.reply_html.assert_called_once()
        assert "Maintenance mode is now <b>OFF</b>" in msg_owner_off.reply_html.call_args[0][0]


@pytest.mark.asyncio
async def test_central_maintenance_handler_enforcement():
    owner_id = 111111111
    sudo_id = 222222222
    unauthorized_id = 999999999

    owner_user = User(id=owner_id, first_name="Owner", is_bot=False)
    sudo_user = User(id=sudo_id, first_name="Sudo", is_bot=False)
    unauth_user = User(id=unauthorized_id, first_name="Unauth", is_bot=False)

    priv_chat = Chat(id=unauthorized_id, type="private")
    group_chat = Chat(id=-100123456, type="supergroup")

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()
    ctx.bot.username = "YuukiBot"

    # --- Scenario A: Maintenance is OFF ---
    set_maintenance_mode(False)

    upd_off = MagicMock(spec=Update)
    upd_off.effective_user = unauth_user
    # When maintenance is OFF, handler returns without raising ApplicationHandlerStop
    res = await maintenance_handler(upd_off, ctx)
    assert res is None

    # --- Scenario B: Maintenance is ON ---
    set_maintenance_mode(True)

    with patch("bot.is_owner_or_sudo", side_effect=lambda uid: uid in (owner_id, sudo_id)):

        # 1. Owner & Sudo Access: Allowed
        upd_owner = MagicMock(spec=Update)
        upd_owner.effective_user = owner_user
        res_owner = await maintenance_handler(upd_owner, ctx)
        assert res_owner is None  # Pass-through

        upd_sudo = MagicMock(spec=Update)
        upd_sudo.effective_user = sudo_user
        res_sudo = await maintenance_handler(upd_sudo, ctx)
        assert res_sudo is None  # Pass-through

        # 2. Internal updates (my_chat_member / chat_member / status updates): Allowed
        upd_internal = MagicMock(spec=Update)
        upd_internal.effective_user = unauth_user
        upd_internal.my_chat_member = MagicMock()
        res_internal = await maintenance_handler(upd_internal, ctx)
        assert res_internal is None  # Pass-through

        upd_status_upd = MagicMock(spec=Update)
        upd_status_upd.effective_user = unauth_user
        upd_status_upd.my_chat_member = None
        upd_status_upd.chat_member = None
        status_msg = MagicMock(spec=Message)
        status_msg.status_update = True
        upd_status_upd.effective_message = status_msg
        res_status = await maintenance_handler(upd_status_upd, ctx)
        assert res_status is None  # Pass-through

        # 3. Callback Queries (Inline buttons) by unauthorized user -> Answered & Stopped
        upd_cb = MagicMock(spec=Update)
        upd_cb.effective_user = unauth_user
        upd_cb.my_chat_member = None
        upd_cb.chat_member = None
        upd_cb.effective_message = None
        cb_query = AsyncMock(spec=CallbackQuery)
        upd_cb.callback_query = cb_query

        with pytest.raises(ApplicationHandlerStop):
            await maintenance_handler(upd_cb, ctx)
        cb_query.answer.assert_called_once()
        assert "Maintenance Mode Active" in cb_query.answer.call_args[0][0]

        # 4. Commands / DM / Interactions by unauthorized user -> Friendly message & Stopped
        # Command in private chat
        upd_cmd = MagicMock(spec=Update)
        upd_cmd.effective_user = unauth_user
        upd_cmd.effective_chat = priv_chat
        upd_cmd.my_chat_member = None
        upd_cmd.chat_member = None
        upd_cmd.callback_query = None

        msg_cmd = AsyncMock(spec=Message)
        msg_cmd.text = "/start"
        msg_cmd.caption = None
        msg_cmd.entities = []
        msg_cmd.status_update = False
        upd_cmd.effective_message = msg_cmd
        upd_cmd.message = msg_cmd
        upd_cmd.edited_message = None

        with pytest.raises(ApplicationHandlerStop):
            await maintenance_handler(upd_cmd, ctx)
        msg_cmd.reply_html.assert_called_once()
        assert "Maintenance Mode Active" in msg_cmd.reply_html.call_args[0][0]

        # Group bot mention (@YuukiBot)
        upd_mention = MagicMock(spec=Update)
        upd_mention.effective_user = unauth_user
        upd_mention.effective_chat = group_chat
        upd_mention.my_chat_member = None
        upd_mention.chat_member = None
        upd_mention.callback_query = None

        msg_mention = AsyncMock(spec=Message)
        msg_mention.text = "hello @YuukiBot how are you"
        msg_mention.caption = None
        msg_mention.entities = []
        msg_mention.status_update = False
        upd_mention.effective_message = msg_mention
        upd_mention.message = msg_mention
        upd_mention.edited_message = None

        with pytest.raises(ApplicationHandlerStop):
            await maintenance_handler(upd_mention, ctx)
        msg_mention.reply_html.assert_called_once()

        # Group regular message (non-interaction) -> Stopped without reply to avoid group spam
        upd_group_msg = MagicMock(spec=Update)
        upd_group_msg.effective_user = unauth_user
        upd_group_msg.effective_chat = group_chat
        upd_group_msg.my_chat_member = None
        upd_group_msg.chat_member = None
        upd_group_msg.callback_query = None

        msg_group = AsyncMock(spec=Message)
        msg_group.text = "regular conversation between users"
        msg_group.caption = None
        msg_group.entities = []
        msg_group.status_update = False
        msg_group.reply_to_message = None
        upd_group_msg.effective_message = msg_group
        upd_group_msg.message = msg_group
        upd_group_msg.edited_message = None

        with pytest.raises(ApplicationHandlerStop):
            await maintenance_handler(upd_group_msg, ctx)
        msg_group.reply_html.assert_not_called()
