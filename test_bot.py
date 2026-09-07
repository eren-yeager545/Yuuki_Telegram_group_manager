import asyncio
import pytest
from bot import ensure_event_loop

def test_ensure_event_loop_when_none_set():
    asyncio.set_event_loop(None)
    loop = ensure_event_loop()
    assert loop is not None
    assert not loop.is_closed()
    assert asyncio.get_event_loop() == loop

def test_ensure_event_loop_when_closed():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.close()

    new_loop = ensure_event_loop()
    assert new_loop is not None
    assert not new_loop.is_closed()
    assert new_loop != loop
    assert asyncio.get_event_loop() == new_loop

def test_ensure_event_loop_when_already_active():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    existing_loop = ensure_event_loop()
    assert existing_loop == loop
    assert not existing_loop.is_closed()


def test_post_init_does_not_schedule_expire_temp_actions():
    from unittest.mock import MagicMock
    from bot import post_init

    app = MagicMock()
    app.bot_data = {}
    app.job_queue = MagicMock()

    asyncio.run(post_init(app))

    # Ensure expire_temp_actions is not scheduled in job_queue
    for call in app.job_queue.run_repeating.call_args_list:
        callback = call.args[0]
        assert callback.__name__ != 'expire_temp_actions'



def test_get_conn_and_store_functions(tmp_path, monkeypatch):
    import store
    db_file = str(tmp_path / "test_bot.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    assert store.check_chat_quota(100, "notes") is True
    store.set_global_link("support", "https://example.com/support")
    assert store.get_global_link("support") == "https://example.com/support"

    store.delete_user_data(999)


def test_error_handler_with_message():
    from unittest.mock import AsyncMock, MagicMock
    from telegram import Update, Message
    from bot import error_handler

    mock_msg = AsyncMock(spec=Message)
    update = MagicMock(spec=Update)
    update.effective_message = mock_msg

    context = MagicMock()
    context.error = ValueError("Test exception")

    asyncio.run(error_handler(update, context))

    mock_msg.reply_text.assert_called_once()
    assert "Oopsie!" in mock_msg.reply_text.call_args[0][0]


def test_error_handler_without_message():
    from unittest.mock import MagicMock
    from bot import error_handler

    update = MagicMock()
    update.effective_message = None

    context = MagicMock()
    context.error = ValueError("Test exception")

    # Should log without raising any exception
    asyncio.run(error_handler(update, context))


def test_mongo_store_operations(monkeypatch):
    import mongomock
    import config
    import store

    mock_client = mongomock.MongoClient()
    mock_db = mock_client['yuuki_bot_test']

    monkeypatch.setattr(config, 'MONGO_URI', 'mongodb://localhost:27017')
    monkeypatch.setattr(store, '_mongo_client', mock_client)
    monkeypatch.setattr(store, '_mongo_db', mock_db)

    store.init_db()

    # Test group and user upsert
    store.upsert_group(1001, 'Test Group', 'testgroup')
    assert store.get_active_group_count() == 1
    assert store.get_active_groups() == [(1001, 'Test Group')]

    store.upsert_user(2001, 'Test User', 'testuser')
    assert store.get_user_count() == 1

    store.touch_member(1001, 2001)

    # Test settings
    store.set_setting(1001, 'welcome', 'on')
    assert store.get_setting(1001, 'welcome') == 'on'
    assert store.get_setting(1001, 'nonexistent', 'default') == 'default'

    # Test warns
    assert store.get_warns(1001, 2001) == 0
    cnt = store.add_warn(1001, 2001, 'Spamming')
    assert cnt == 1
    assert store.get_warns(1001, 2001) == 1
    assert store.get_warn_reasons(1001, 2001) == 'Spamming'
    warned = store.list_warned_users(1001)
    assert len(warned) == 1
    assert warned[0][0] == 2001
    assert warned[0][2] == 'Test User'
    store.reset_warns(1001, 2001)
    assert store.get_warns(1001, 2001) == 0

    # Test notes
    assert store.check_chat_quota(1001, 'notes') is True
    store.save_note(1001, 'rules', 'Rule 1: Be nice', 'Click - https://example.com')
    assert store.get_note(1001, 'rules') == ('Rule 1: Be nice', 'Click - https://example.com', 'text')
    assert store.list_notes(1001) == [('rules',)]
    store.delete_note(1001, 'rules')
    assert store.get_note(1001, 'rules') is None

    # Test filters
    assert store.check_chat_quota(1001, 'filters') is True
    store.save_filter(1001, 'hello', 'world', 'text')
    assert store.get_filters(1001) == [('hello', 'world', 'text')]
    store.delete_filter(1001, 'hello', 'text')
    assert store.get_filters(1001) == []

    # Test quizzes
    quiz_id = store.add_quiz('What is 2+2?', '4', 2001)
    assert quiz_id > 0
    assert store.get_quiz_count() == 1
    quizzes = store.list_quizzes()
    assert len(quizzes) == 1
    assert quizzes[0][0] == quiz_id
    assert quizzes[0][1] == 'What is 2+2?'
    random_q = store.get_random_quiz()
    assert random_q is not None
    assert random_q[0] == quiz_id
    assert store.delete_quiz(quiz_id) is True
    assert store.get_quiz_count() == 0

    # Test audit logs
    store.log_admin_action(1001, 2001, 'ban', 3001, 'violating rules')
    logs = store.get_recent_audit_logs(1001)
    assert len(logs) == 1
    assert logs[0][0] == 2001
    assert logs[0][1] == 'ban'

    # Test blacklists
    assert store.check_chat_quota(1001, 'blacklists') is True
    store.add_blacklist(1001, 'badword', 'warn')
    assert store.list_blacklists(1001) == [('badword', 'warn')]
    store.remove_blacklist(1001, 'badword')
    assert store.list_blacklists(1001) == []

    # Test reports and rate limit
    rep_id = store.add_report(1001, 2001, 3001, 555, 'spam')
    assert rep_id > 0
    assert store.report_exists_recent(1001, 2001, 555) is True
    assert store.allow_report_event(1001, 2001, 3001) is True
    assert store.allow_report_event(1001, 2001, 3001) is True
    assert store.allow_report_event(1001, 2001, 3001) is False
    store.trim_reports(1001)

    # Test federations
    store.create_federation('testfed', 'Test Federation', 2001)
    assert store.get_federation('testfed') == ('testfed', 'Test Federation', 2001)
    assert store.list_federations_by_owner(2001) == [('testfed', 'Test Federation')]
    assert store.is_fed_admin('testfed', 2001) is True
    store.add_fed_admin('testfed', 3001)
    assert store.is_fed_admin('testfed', 3001) is True

    store.set_chat_federation(1001, 'testfed', 2001)
    assert store.get_chat_federation(1001) == ('testfed',)
    store.leave_chat_federation(1001)
    assert store.get_chat_federation(1001) is None

    store.fed_ban_user('testfed', 3001, 'spammer', 2001)
    assert store.get_fed_ban('testfed', 3001) == ('spammer',)
    store.unfed_ban_user('testfed', 3001)
    assert store.get_fed_ban('testfed', 3001) is None

    # Test temp actions
    store.add_temp_action(1001, 2001, 'tmute', 1000)
    assert len(store.get_expired_temp_actions(2000)) == 1
    store.clear_temp_action(1001, 2001, 'tmute')
    assert len(store.get_expired_temp_actions(2000)) == 0

    # Test buttons
    store.save_buttons(1001, 'welcome', [('Help', 'https://example.com/help')])
    assert store.get_buttons(1001, 'welcome') == [('Help', 'https://example.com/help')]

    # Test delete user data
    store.delete_user_data(2001)
    assert store.get_user_count() == 0

    # Test global links
    store.set_global_link('support_group_url', 'https://t.me/testsupport')
    assert store.get_global_link('support_group_url') == 'https://t.me/testsupport'


def test_command_logic(monkeypatch, tmp_path):
    import store
    import common
    import admin
    from unittest.mock import AsyncMock, MagicMock
    from telegram import Update, User, Chat, Message, ChatMember

    db_file = str(tmp_path / "test_cmd_bot.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    # Test format_uptime
    assert common.format_uptime(10) == "10s"
    assert common.format_uptime(332) == "5m 32s"
    assert common.format_uptime(8040) == "2h 14m"
    assert common.format_uptime(98420) == "1d 3h 20m 20s"

    # Test parse_duration_to_seconds
    assert admin.parse_duration_to_seconds("10m") == 600
    assert admin.parse_duration_to_seconds("2h") == 7200
    assert admin.parse_duration_to_seconds("1d") == 86400
    assert admin.parse_duration_to_seconds("invalid") is None

    # Test format_user_tag and format_user_link
    assert admin.format_user_tag(100, "Alice", "alice") == "@alice"
    assert admin.format_user_tag(200, "Bob", None) == '<a href="tg://user?id=200">Bob</a>'

    # Test zombies functions
    store.touch_member(-1001, 301, status='left')
    zombies = store.list_zombies(-1001)
    assert len(zombies) == 1
    assert zombies[0][0] == 301
    cleaned = store.clean_zombies(-1001)
    assert cleaned == 1
    assert len(store.list_zombies(-1001)) == 0


def test_logger_formatting_and_events(monkeypatch, tmp_path):
    import store
    import logger_helper
    from unittest.mock import AsyncMock, MagicMock
    from telegram import User, Chat, ChatMember, ChatMemberUpdated

    db_file = str(tmp_path / "test_logger.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    bot = AsyncMock()
    bot.first_name = "Yuuki Bot"
    bot.username = "yuukibot"
    bot.id = 999
    bot.get_chat_member_count.return_value = 150
    bot.export_chat_invite_link.return_value = "https://t.me/+invite123"

    context = MagicMock()
    context.bot = bot

    sent_messages = []
    async def mock_send_message(chat_id, text, **kwargs):
        sent_messages.append((chat_id, text))

    bot.send_message.side_effect = mock_send_message

    # Configure LOG_CHANNEL_ID
    monkeypatch.setattr(logger_helper.config, "LOG_CHANNEL_ID", -100999)

    # 1. Test User Started Event
    user = User(id=1001, first_name="Alice", is_bot=False, username="alice_user")
    asyncio.run(logger_helper.log_user_started_event(user, context))

    assert len(sent_messages) == 1
    target, msg_text = sent_messages[0]
    assert target == -100999
    assert "📢 NEW USER STARTED" in msg_text
    assert '👤 User: "Alice"' in msg_text
    assert '🆔 User ID: "1001"' in msg_text
    assert '🔗 Username: @alice_user' in msg_text
    assert '📊 Total Users: "1"' in msg_text
    assert "🤖 Bot: Yuuki Bot" in msg_text
    assert "━━━━━━━━━━━━━━━━━━" in msg_text

    # Test user with no username
    user_no_un = User(id=1002, first_name="Bob", is_bot=False, username=None)
    asyncio.run(logger_helper.log_user_started_event(user_no_un, context))
    assert len(sent_messages) == 2
    _, msg_text2 = sent_messages[1]
    assert '🔗 Username: "Not Set"' in msg_text2
    assert '📊 Total Users: "2"' in msg_text2

    # 2. Test New Group Added Event
    group = Chat(id=-100123, title="Awesome Group", type="supergroup", username="awesomegroup")
    adder = User(id=1001, first_name="Alice", is_bot=False, username="alice_user")

    asyncio.run(logger_helper.log_group_added_event(group, adder, context))

    assert len(sent_messages) == 3
    _, group_msg = sent_messages[2]
    assert "📢 NEW GROUP ADDED" in group_msg
    assert '👥 Group: "Awesome Group"' in group_msg
    assert '🔗 Group Link: "https://t.me/awesomegroup"' in group_msg
    assert '🆔 Chat ID: "-100123"' in group_msg
    assert '👤 Added By: "Alice"' in group_msg
    assert '🆔 User ID: "1001"' in group_msg
    assert '👥 Total Members: "150"' in group_msg
    assert '📊 Total Groups: "1"' in group_msg

    # Verify group in DB
    group_db = store.get_group_by_id(-100123)
    assert group_db is not None
    assert group_db['title'] == "Awesome Group"
    assert group_db['is_active'] == 1
    assert group_db['added_by_user_id'] == 1001

    # 3. Test Private Group Added Event (No username)
    private_group = Chat(id=-100456, title="Secret Club", type="supergroup", username=None)
    asyncio.run(logger_helper.log_group_added_event(private_group, None, context))
    assert len(sent_messages) == 4
    _, priv_msg = sent_messages[3]
    assert '👥 Group: "Secret Club"' in priv_msg
    assert '🔗 Group Link: "https://t.me/+invite123"' in priv_msg
    assert "👤 Added By: Unknown" in priv_msg
    assert '📊 Total Groups: "2"' in priv_msg

    # 4. Test Bot Banned / Removed Event
    asyncio.run(logger_helper.log_group_removed_event(private_group, adder, context, removal_status="kicked"))
    assert len(sent_messages) == 5
    _, rem_msg = sent_messages[4]
    assert "📢 BOT BANNED / REMOVED" in rem_msg
    assert '👥 Group: "Secret Club"' in rem_msg
    assert '👤 Action By: "Alice"' in rem_msg
    assert '👥 Members Before Removal: "150"' in rem_msg
    assert '📊 Remaining Groups: "1"' in rem_msg

    # Verify DB marked group inactive
    updated_group_db = store.get_group_by_id(-100456)
    assert updated_group_db['is_active'] == 0
    assert updated_group_db['current_bot_status'] == "kicked"
    assert store.get_active_group_count() == 1


def test_logger_error_reliability(monkeypatch):
    import logger_helper
    from unittest.mock import AsyncMock, MagicMock

    bot = AsyncMock()
    bot.send_message.side_effect = Exception("Telegram API network timeout")
    context = MagicMock()
    context.bot = bot

    monkeypatch.setattr(logger_helper.config, "LOG_CHANNEL_ID", -100999)

    # Should log warning internally without crashing caller
    asyncio.run(logger_helper.send_logger_notification(context, "Test message"))


def test_mongo_extended_group_store(monkeypatch):
    import mongomock
    import config
    import store

    mock_client = mongomock.MongoClient()
    mock_db = mock_client['yuuki_bot_group_test']

    monkeypatch.setattr(config, 'MONGO_URI', 'mongodb://localhost:27017')
    monkeypatch.setattr(store, '_mongo_client', mock_client)
    monkeypatch.setattr(store, '_mongo_db', mock_db)

    store.init_db()

    # Add active group
    store.upsert_group(
        chat_id=-100777,
        title="Mongo Group",
        username="mongogroup",
        group_link="https://t.me/mongogroup",
        member_count=50,
        added_by_user_id=888,
        current_bot_status="member",
        is_active=1
    )

    assert store.get_active_group_count() == 1
    g = store.get_group_by_id(-100777)
    assert g['title'] == "Mongo Group"
    assert g['member_count'] == 50
    assert g['is_active'] == 1

    # Mark group inactive
    store.set_group_inactive(-100777, current_bot_status="left", member_count=48)
    assert store.get_active_group_count() == 0
    g_updated = store.get_group_by_id(-100777)
    assert g_updated['is_active'] == 0
    assert g_updated['current_bot_status'] == "left"
    assert g_updated['member_count'] == 48



def test_purge_and_dban_bulk(monkeypatch, tmp_path):
    import store
    import admin
    from unittest.mock import AsyncMock, MagicMock
    from telegram import Update, User, Chat, Message

    db_file = str(tmp_path / "test_purge_dban.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    # Record user messages
    store.record_user_message(-1001, 555, 101)
    store.record_user_message(-1001, 555, 102)
    store.record_user_message(-1001, 555, 103)

    bot = AsyncMock()
    bot.delete_messages = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.ban_chat_member = AsyncMock()
    bot.send_message = AsyncMock()

    context = MagicMock()
    context.bot = bot

    # Test purge
    user = User(id=555, first_name="Target", is_bot=False)
    chat = Chat(id=-1001, type="supergroup")
    chat.set_bot(bot)
    cmd_user = User(id=999, first_name="Admin", is_bot=False)

    msg = MagicMock(spec=Message)
    msg.message_id = 9999
    msg.reply_to_message = None

    update = MagicMock(spec=Update)
    update.effective_chat = chat
    update.effective_user = cmd_user
    update.message = msg

    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=user))
    monkeypatch.setattr(admin, "admin_only", AsyncMock(return_value=True))

    asyncio.run(admin.dban_cmd(update, context))

    assert bot.delete_messages.called or bot.delete_message.called
    assert bot.send_message.called


def test_feedback_and_reply_flow(monkeypatch, tmp_path):
    import store
    import common
    import config
    import bot as bot_module
    from unittest.mock import AsyncMock, MagicMock
    from telegram import Update, User, Chat, Message, CallbackQuery

    db_file = str(tmp_path / "test_feedback.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    bot = AsyncMock()
    bot.send_message = AsyncMock()

    context = MagicMock()
    context.bot = bot
    context.user_data = {}

    user = User(id=12345, first_name="Alice", is_bot=False)
    msg = MagicMock(spec=Message)
    msg.text = "/feedback This bot is awesome!"
    msg.reply_to_message = None

    update = MagicMock(spec=Update)
    update.effective_user = user
    update.effective_message = msg

    monkeypatch.setattr(config, "OWNER_ID", 99999)

    asyncio.run(common.feedback_cmd(update, context))

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert call_args.kwargs['chat_id'] == 99999
    assert "This bot is awesome!" in call_args.kwargs['text']
    assert "tg://user?id=12345" in call_args.kwargs['text']

    # Test reply callback
    cb_update = MagicMock(spec=Update)
    cb_query = MagicMock(spec=CallbackQuery)
    cb_query.data = "reply_fb:12345"
    cb_query.message = AsyncMock()
    cb_update.callback_query = cb_query

    cb_context = MagicMock()
    cb_context.user_data = {}

    asyncio.run(bot_module.homepage_callback(cb_update, cb_context))
    assert cb_context.user_data['pending_feedback_reply'] == 12345

    # Test DM router response from owner
    owner_user = User(id=99999, first_name="Owner", is_bot=False)
    owner_chat = Chat(id=99999, type="private")
    reply_msg = MagicMock(spec=Message)
    reply_msg.text = "Thank you for your feedback!"

    dm_update = MagicMock(spec=Update)
    dm_update.effective_user = owner_user
    dm_update.effective_chat = owner_chat
    dm_update.effective_message = reply_msg

    dm_context = MagicMock()
    dm_context.bot = bot
    dm_context.user_data = {'pending_feedback_reply': 12345}

    asyncio.run(bot_module.message_router(dm_update, dm_context))

    assert bot.send_message.call_count == 2
    dm_call_args = bot.send_message.call_args
    assert dm_call_args.kwargs['chat_id'] == 12345
    assert "Thank you for your feedback!" in dm_call_args.kwargs['text']


def test_broadcast_system(monkeypatch, tmp_path):
    import store
    import broadcast
    import config
    from unittest.mock import AsyncMock, MagicMock
    from telegram import Update, User, Chat, Message, CallbackQuery
    from telegram.error import Forbidden

    db_file = str(tmp_path / "test_broadcast.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    # Populate sample database
    store.upsert_user(1001, "User One", "user1")
    store.upsert_user(1002, "User Two", "user2")
    store.upsert_group(-1001, "Group One", is_active=1)
    store.upsert_group(-1002, "Group Two", is_active=1)

    owner_id = 99999
    monkeypatch.setattr(broadcast, "OWNER_IDS", [owner_id])
    monkeypatch.setattr("helpers.is_owner", lambda uid, owner_ids=None: uid == owner_id)

    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.copy_message = AsyncMock()

    context = MagicMock()
    context.bot = bot

    owner_user = User(id=owner_id, first_name="Owner", is_bot=False)
    normal_user = User(id=11111, first_name="Normal", is_bot=False)

    # 1. Non-owner /broadcast rejection
    no_owner_msg = MagicMock(spec=Message)
    no_owner_msg.reply_text = AsyncMock()
    no_owner_update = MagicMock(spec=Update)
    no_owner_update.effective_user = normal_user
    no_owner_update.message = no_owner_msg

    asyncio.run(broadcast.broadcast_cmd(no_owner_update, context))
    no_owner_msg.reply_text.assert_called_once()
    assert "Only the Bot Owner" in no_owner_msg.reply_text.call_args[0][0]

    # 2. Owner /broadcast initial prompt
    owner_msg = MagicMock(spec=Message)
    owner_msg.reply_text = AsyncMock()
    owner_update = MagicMock(spec=Update)
    owner_update.effective_user = owner_user
    owner_update.message = owner_msg

    asyncio.run(broadcast.broadcast_cmd(owner_update, context))
    owner_msg.reply_text.assert_called_once()
    assert "Choose Broadcast Type" in owner_msg.reply_text.call_args[0][0]
    assert broadcast.BROADCAST_SESSIONS[owner_id]["status"] == "selecting_target"

    # 3. Target Selection Callback ("both")
    cb_query = MagicMock(spec=CallbackQuery)
    cb_query.data = "bcast_target:both"
    cb_query.answer = AsyncMock()
    cb_query.edit_message_text = AsyncMock()

    cb_update = MagicMock(spec=Update)
    cb_update.effective_user = owner_user
    cb_update.callback_query = cb_query

    asyncio.run(broadcast.broadcast_callback_handler(cb_update, context))
    cb_query.edit_message_text.assert_called_once()
    assert "Send the message you want to broadcast" in cb_query.edit_message_text.call_args[0][0]
    assert broadcast.BROADCAST_SESSIONS[owner_id]["status"] == "waiting_content"

    # 4. Handle Content message & Execution
    content_msg = MagicMock(spec=Message)
    content_msg.chat_id = owner_id
    content_msg.message_id = 7777

    content_chat = Chat(id=owner_id, type="private")

    content_update = MagicMock(spec=Update)
    content_update.effective_user = owner_user
    content_update.effective_chat = content_chat
    content_update.effective_message = content_msg

    # Mock send_single_recipient behavior: user 1002 forbidden, group -1002 forbidden
    async def mock_copy_message(chat_id, from_chat_id, message_id):
        if chat_id in (1002, -1002):
            raise Forbidden("Bot was blocked by the user")
        return MagicMock()

    bot.copy_message.side_effect = mock_copy_message

    # Handle broadcast content triggers async task
    asyncio.run(broadcast.execute_broadcast(context, owner_id, owner_id, 7777, 'both'))

    # Verify results
    assert owner_id not in broadcast.BROADCAST_SESSIONS

    # Verify group -1002 was marked inactive in DB
    g2 = store.get_group_by_id(-1002)
    assert g2["is_active"] == 0


def test_new_features_pytest(monkeypatch, tmp_path):
    import store
    import bot as bot_module
    import common
    import logger_helper
    from unittest.mock import AsyncMock, MagicMock
    import asyncio

    db_file = str(tmp_path / "test_new_features.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    # 1. Test Filter Word Boundary Matching
    store.save_filter(10001, "help", "Hello! How can I help you?", "text")
    filters_list = store.get_filters(10001)
    import re

    # 'helping' should NOT match 'help'
    lower_text = "i am a helping hand"
    matched = any(bool(re.search(r'\b' + re.escape(kw) + r'\b', lower_text)) for kw, rep, f_type in filters_list)
    assert not matched

    # 'can you help me' SHOULD match 'help'
    lower_text_2 = "can you help me"
    matched_2 = any(bool(re.search(r'\b' + re.escape(kw) + r'\b', lower_text_2)) for kw, rep, f_type in filters_list)
    assert matched_2

    # 2. Test Privacy Policy Callback / Message Handling
    update_cb = MagicMock()
    update_cb.effective_message = None
    update_cb.callback_query = MagicMock()
    update_cb.callback_query.message = AsyncMock()
    context_cb = MagicMock()

    asyncio.run(common.privacy_cmd(update_cb, context_cb))
    update_cb.callback_query.message.reply_text.assert_called_once()

    # 3. Test /mybot command & Logger Status Toggle
    store.set_setting(0, 'logger_status', 'on')
    store.set_global_link('logger_channel_id', '-100123456789')

    update_mybot = MagicMock()
    update_mybot.effective_user.id = 99999
    update_mybot.effective_message = AsyncMock()
    context_mybot = MagicMock()
    context_mybot.bot.first_name = "Yuki"

    monkeypatch.setattr(common, 'OWNER_IDS', [99999])
    asyncio.run(common.mybot_cmd(update_mybot, context_mybot))
    update_mybot.effective_message.reply_text.assert_called_once()

    # Test logger_helper respects logger_status = 'off'
    store.set_setting(0, 'logger_status', 'off')
    ctx_log = MagicMock()
    ctx_log.bot = AsyncMock()
    asyncio.run(logger_helper.send_logger_notification(ctx_log, "Test message"))
    ctx_log.bot.send_message.assert_not_called()


def test_ban_admin_protection_and_guards(monkeypatch, tmp_path):
    import store
    import admin
    import common
    from unittest.mock import AsyncMock, MagicMock
    import asyncio
    from telegram import User, Chat, Message

    db_file = str(tmp_path / "test_ban_guards.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    store.init_db()

    # Setup mocks
    bot = AsyncMock()
    bot.ban_chat_member = AsyncMock()
    bot.unban_chat_member = AsyncMock()
    bot.send_message = AsyncMock()

    context = MagicMock()
    context.bot = bot

    chat = Chat(id=-1001, type="supergroup")
    cmd_user = User(id=111, first_name="NormalUser", is_bot=False)
    admin_user = User(id=222, first_name="AdminUser", is_bot=False)
    target_admin = User(id=333, first_name="TargetAdmin", is_bot=False)

    msg = MagicMock(spec=Message)
    msg.reply_text = AsyncMock()
    msg.reply_html = AsyncMock()

    update = MagicMock()
    update.effective_chat = chat
    update.effective_user = cmd_user
    update.message = msg

    # 1. Non-admin calls /ban
    monkeypatch.setattr(admin, "is_admin", AsyncMock(return_value=False))
    asyncio.run(admin.ban_cmd(update, context))
    assert "/ban command is only for admins desu!" in msg.reply_text.call_args[0][0]

    # 2. Admin tries to ban another admin
    update.effective_user = admin_user
    monkeypatch.setattr(admin, "is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(admin, "resolve_target_user", AsyncMock(return_value=target_admin))
    monkeypatch.setattr(admin, "is_target_admin", AsyncMock(return_value=True))

    msg.reply_text.reset_mock()
    asyncio.run(admin.ban_cmd(update, context))
    assert "Ehehe… I can't ban an admin!" in msg.reply_text.call_args[0][0]

    # 3. Test /kickme command
    msg.reply_text.reset_mock()
    asyncio.run(admin.kickme_cmd(update, context))
    assert "kicking you out gently!" in msg.reply_text.call_args[0][0]
    bot.ban_chat_member.assert_called_with(-1001, 222)
    bot.unban_chat_member.assert_called_with(-1001, 222)

    # 4. Test /setwelcome and /setgoodbye
    context.args = ["Welcome", "{user}", "to", "{group}!"]
    asyncio.run(admin.setwelcome_cmd(update, context))
    assert store.get_setting(-1001, "welcome_text") == "Welcome {user} to {group}!"

    context.args = ["Goodbye", "{user}!"]
    asyncio.run(admin.setgoodbye_cmd(update, context))
    assert store.get_setting(-1001, "goodbye_text") == "Goodbye {user}!"


def test_tictactoe_logic_and_gameplay():
    import tictactoe
    from unittest.mock import AsyncMock, MagicMock
    from telegram import Update, User, Chat, Message, CallbackQuery

    # 1. Test check_winner
    assert tictactoe.check_winner([' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ']) is None
    assert tictactoe.check_winner(['X', 'X', 'X', ' ', ' ', ' ', ' ', ' ', ' ']) == 'X'
    assert tictactoe.check_winner(['O', ' ', ' ', 'O', ' ', ' ', 'O', ' ', ' ']) == 'O'
    assert tictactoe.check_winner(['X', 'O', 'X', 'X', 'O', 'O', 'O', 'X', 'X']) == 'draw'

    # 2. Test minimax AI
    board = ['X', 'X', ' ', ' ', 'O', ' ', ' ', ' ', ' ']
    move = tictactoe.get_bot_move(board)
    assert move == 2  # Block X from winning on row 0

    # 3. Test Single Player vs Bot Command (/ttt)
    bot_user = User(id=999, first_name="Yuki", is_bot=True)
    p1 = User(id=101, first_name="Alice", is_bot=False)
    chat = Chat(id=-1001, type="supergroup")

    msg = MagicMock(spec=Message)
    msg.reply_to_message = None
    msg.reply_html = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_message = msg
    update.effective_user = p1
    update.effective_chat = chat

    context = MagicMock()
    context.bot = bot_user

    asyncio.run(tictactoe.ttt_cmd(update, context))

    msg.reply_html.assert_called_once()
    assert len(tictactoe.GAMES) == 1
    game_id = list(tictactoe.GAMES.keys())[0]
    game = tictactoe.GAMES[game_id]
    assert game['p1']['id'] == 101
    assert game['vs_bot'] is True

    # 4. Test Player Move against Bot
    cb_query = MagicMock(spec=CallbackQuery)
    cb_query.data = f"ttt_move:{game_id}:0"
    cb_query.from_user = p1
    cb_query.answer = AsyncMock()
    cb_query.edit_message_text = AsyncMock()

    cb_update = MagicMock(spec=Update)
    cb_update.callback_query = cb_query

    asyncio.run(tictactoe.ttt_callback_handler(cb_update, context))

    # Board should have X at index 0 and O placed by Bot
    assert game['board'][0] == 'X'
    assert 'O' in game['board']

    # Clean games dict
    tictactoe.GAMES.clear()

    # 5. Test Tournament PvP Challenge (/ttt in reply to another user)
    p2 = User(id=202, first_name="Bob", is_bot=False)
    reply_msg = MagicMock(spec=Message)
    reply_msg.from_user = p2

    pvp_msg = MagicMock(spec=Message)
    pvp_msg.reply_to_message = reply_msg
    pvp_msg.reply_html = AsyncMock(return_value=MagicMock(message_id=555))

    pvp_update = MagicMock(spec=Update)
    pvp_update.effective_message = pvp_msg
    pvp_update.effective_user = p1
    pvp_update.effective_chat = chat

    asyncio.run(tictactoe.ttt_cmd(pvp_update, context))

    pvp_msg.reply_html.assert_called_once()
    assert len(tictactoe.INVITATIONS) == 1
    inv_id = list(tictactoe.INVITATIONS.keys())[0]
    inv = tictactoe.INVITATIONS[inv_id]
    assert inv['p1']['id'] == 101
    assert inv['p2']['id'] == 202

    # 6. Test Decline Challenge
    dec_query = MagicMock(spec=CallbackQuery)
    dec_query.data = f"ttt_decline:{inv_id}"
    dec_query.from_user = p2
    dec_query.answer = AsyncMock()
    dec_query.edit_message_text = AsyncMock()

    dec_update = MagicMock(spec=Update)
    dec_update.callback_query = dec_query

    asyncio.run(tictactoe.ttt_callback_handler(dec_update, context))
    assert inv_id not in tictactoe.INVITATIONS
    dec_query.edit_message_text.assert_called_once()
    assert "Declined" in dec_query.edit_message_text.call_args[0][0]

    # 7. Test Accept Challenge & Play
    asyncio.run(tictactoe.ttt_cmd(pvp_update, context))
    inv_id_2 = list(tictactoe.INVITATIONS.keys())[0]

    acc_query = MagicMock(spec=CallbackQuery)
    acc_query.data = f"ttt_accept:{inv_id_2}"
    acc_query.from_user = p2
    acc_query.answer = AsyncMock()
    acc_query.edit_message_text = AsyncMock()

    acc_update = MagicMock(spec=Update)
    acc_update.callback_query = acc_query

    asyncio.run(tictactoe.ttt_callback_handler(acc_update, context))
    assert inv_id_2 not in tictactoe.INVITATIONS
    assert inv_id_2 in tictactoe.GAMES

    pvp_game = tictactoe.GAMES[inv_id_2]
    assert pvp_game['vs_bot'] is False

    # Move by P1 (Alice)
    m1_query = MagicMock(spec=CallbackQuery)
    m1_query.data = f"ttt_move:{inv_id_2}:0"
    m1_query.from_user = p1
    m1_query.answer = AsyncMock()
    m1_query.edit_message_text = AsyncMock()
    asyncio.run(tictactoe.ttt_callback_handler(MagicMock(callback_query=m1_query), context))
    assert pvp_game['board'][0] == 'X'
    assert pvp_game['turn'] == 202

    # Move by P2 (Bob)
    m2_query = MagicMock(spec=CallbackQuery)
    m2_query.data = f"ttt_move:{inv_id_2}:1"
    m2_query.from_user = p2
    m2_query.answer = AsyncMock()
    m2_query.edit_message_text = AsyncMock()
    asyncio.run(tictactoe.ttt_callback_handler(MagicMock(callback_query=m2_query), context))
    assert pvp_game['board'][1] == 'O'
    assert pvp_game['turn'] == 101

    # 8. Test Expiry Timeout
    tictactoe.INVITATIONS[inv_id_2] = {
        'p1': {'id': 101, 'name': 'Alice'},
        'p2': {'id': 202, 'name': 'Bob'},
        'chat_id': -1001,
        'msg_id': 777,
        'status': 'pending'
    }
    dummy_job = type('Job', (), {'data': {'game_id': inv_id_2}})()
    dummy_ctx = type('Ctx', (), {'job': dummy_job, 'bot': AsyncMock()})()
    asyncio.run(tictactoe.expire_invitation(dummy_ctx))
    assert inv_id_2 not in tictactoe.INVITATIONS
    dummy_ctx.bot.edit_message_text.assert_called_once()
    assert "Expired" in dummy_ctx.bot.edit_message_text.call_args.kwargs['text'] or "Expired" in str(dummy_ctx.bot.edit_message_text.call_args)
