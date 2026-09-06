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
    assert "Something went wrong. Reference ID:" in mock_msg.reply_text.call_args[0][0]


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
    assert store.get_note(1001, 'rules') == ('Rule 1: Be nice', 'Click - https://example.com')
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
