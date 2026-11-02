import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, Chat, User, Message, MessageEntity
from telegram.ext import ContextTypes
import store
import admin
import mongomock


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_cleanup.db")
    monkeypatch.setattr(store, "DB_PATH", db_file)
    monkeypatch.setattr(store, "_mongo_client", None)
    monkeypatch.setattr(store, "_mongo_db", None)
    store.init_db()
    return store


@pytest.fixture
def mongo_store(monkeypatch):
    mock_client = mongomock.MongoClient()
    mock_db = mock_client['test_cleanup_db']
    monkeypatch.setattr(store, "_mongo_client", mock_client)
    monkeypatch.setattr(store, "_mongo_db", mock_db)
    monkeypatch.setattr(store, "is_mongo", lambda: True)
    monkeypatch.setattr(store, "get_mongo_db", lambda: mock_db)
    store.init_db()
    return store


# ----------------------------------------------------------------------------
# 1. join_request_decisions tests
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_join_request_decision_deleted_on_success_callback(sqlite_store):
    chat_id = -10099901
    user_id = 111101
    admin_user = User(id=88801, first_name="Admin", username="admin", is_bot=False)

    msg = AsyncMock()
    msg.text = "🌸 Yuki noticed a new visitor!"
    query = AsyncMock()
    query.data = f"cjr:a:{chat_id}:{user_id}"
    query.from_user = admin_user
    query.message = msg

    upd = MagicMock(spec=Update)
    upd.callback_query = query
    upd.effective_user = admin_user

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()
    admin_member = MagicMock(status='administrator')
    ctx.bot.get_chat_member.return_value = admin_member

    await admin.chat_join_request_callback_handler(upd, ctx)

    ctx.bot.approve_chat_join_request.assert_called_once_with(chat_id=chat_id, user_id=user_id)
    # Ensure decision record is deleted immediately after success
    assert store.get_join_request_decision(chat_id, user_id) is None


@pytest.mark.asyncio
async def test_join_request_decision_deleted_on_success_autojoin(sqlite_store):
    chat_id = -10099902
    user_id = 111102
    chat = Chat(id=chat_id, type="supergroup", title="Lounge")
    user = User(id=user_id, first_name="Visitor", username="vis", is_bot=False)

    store.set_setting(chat_id, "autojoin", "on")

    req = MagicMock(chat=chat, from_user=user, user=user)
    upd = MagicMock(spec=Update)
    upd.chat_join_request = req

    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()

    with patch("logger_helper.send_logger_notification", new_callable=AsyncMock):
        await admin.chat_join_request_handler(upd, ctx)

    ctx.bot.approve_chat_join_request.assert_called_once_with(chat_id=chat_id, user_id=user_id)
    # Ensure decision is deleted immediately after auto-join success
    assert store.get_join_request_decision(chat_id, user_id) is None


# ----------------------------------------------------------------------------
# 2. reports tests
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reports_deleted_on_success_preserved_on_failure_and_deleted_on_retry(sqlite_store):
    chat_id = -10099903
    reporter_id = 88802
    target_id = 77702
    msg_id = 501
    reason = "Spamming"

    # Step A: Insert report
    r_id = store.add_report(chat_id, reporter_id, target_id, msg_id, reason)
    assert r_id is not None
    pending = store.get_pending_reports()
    assert any(r['id'] == r_id for r in pending)

    # Step B: Failure scenario
    ctx = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    ctx.bot = AsyncMock()
    ctx.bot.get_chat_administrators.side_effect = Exception("Telegram network error")
    ctx.bot.send_message.side_effect = Exception("Telegram network error")

    # Delivery fails:
    success = await admin.deliver_report_notification(ctx, {'id': r_id, 'chat_id': chat_id, 'reporter_id': reporter_id, 'target_id': target_id, 'reason': reason})
    assert success is False
    # Report must still be preserved in DB
    pending = store.get_pending_reports()
    assert any(r['id'] == r_id for r in pending)

    # Step C: Success / Retry scenario
    ctx.bot.get_chat_administrators.side_effect = None
    admin_member = MagicMock(user=User(id=99999, first_name="GroupAdmin", is_bot=False))
    ctx.bot.get_chat_administrators.return_value = [admin_member]
    ctx.bot.send_message.side_effect = None

    retried_count = await admin.retry_pending_reports(ctx)
    assert retried_count >= 1
    # Report must now be deleted after successful delivery
    pending_after = store.get_pending_reports()
    assert not any(r['id'] == r_id for r in pending_after)


# ----------------------------------------------------------------------------
# 3. user_messages tests (SQLite & MongoDB)
# ----------------------------------------------------------------------------

def test_user_messages_cleanup_stale_and_quota_sqlite(sqlite_store):
    chat_id = -10099904
    user_id = 111104
    now = int(time.time())

    # Test quota limit (quota_limit=5)
    for mid in range(1, 10):
        sqlite_store.record_user_message(chat_id, user_id, mid, quota_limit=5)

    mids = sqlite_store.get_user_messages(chat_id, user_id)
    assert len(mids) == 5
    assert mids == [9, 8, 7, 6, 5]

    # Test clean_stale_user_messages: set some with created_at 3 days ago
    old_cutoff = now - (3 * 86400)
    with store.closing(store.conn()) as c:
        c.execute('UPDATE user_messages SET created_at=? WHERE message_id IN (5, 6)', (old_cutoff,))
        c.commit()

    deleted_count = sqlite_store.clean_stale_user_messages(max_age_seconds=2 * 86400)
    assert deleted_count == 2
    remaining = sqlite_store.get_user_messages(chat_id, user_id)
    assert remaining == [9, 8, 7]


def test_user_messages_cleanup_stale_and_quota_mongo(mongo_store):
    chat_id = -10099905
    user_id = 111105
    now = int(time.time())

    # Test quota limit (quota_limit=5)
    for mid in range(1, 10):
        mongo_store.record_user_message(chat_id, user_id, mid, quota_limit=5)

    mids = mongo_store.get_user_messages(chat_id, user_id)
    assert len(mids) == 5
    assert mids == [9, 8, 7, 6, 5]

    # Test clean_stale_user_messages
    db = mongo_store.get_mongo_db()
    old_cutoff = now - (3 * 86400)
    db['user_messages'].update_many({'message_id': {'$in': [5, 6]}}, {'$set': {'created_at': old_cutoff}})

    deleted_count = mongo_store.clean_stale_user_messages(max_age_seconds=2 * 86400)
    assert deleted_count == 2
    remaining = mongo_store.get_user_messages(chat_id, user_id)
    assert remaining == [9, 8, 7]


# ----------------------------------------------------------------------------
# 4. action_events tests
# ----------------------------------------------------------------------------

def test_action_events_cleanup_expired_sqlite(sqlite_store):
    chat_id = -10099906
    reporter_id = 88806
    target_id = 77706
    now = int(time.time())

    # Insert events: one recent, one older than 300s
    with store.closing(store.conn()) as c:
        c.execute(
            'INSERT INTO action_events(scope, actor_id, chat_id, target_id, event, created_at) VALUES (?,?,?,?,?,?)',
            (f'report:{chat_id}:{target_id}', reporter_id, chat_id, target_id, 'report', now - 400)
        )
        c.execute(
            'INSERT INTO action_events(scope, actor_id, chat_id, target_id, event, created_at) VALUES (?,?,?,?,?,?)',
            (f'report:{chat_id}:{target_id}', reporter_id, chat_id, target_id, 'report', now - 10)
        )
        c.commit()

    deleted = sqlite_store.delete_expired_action_events(max_age_seconds=300)
    assert deleted == 1

    # Recent event remains for rate limiting
    assert sqlite_store.allow_report_event(chat_id, reporter_id, target_id, window_seconds=60, max_events=2) is True


def test_action_events_cleanup_expired_mongo(mongo_store):
    chat_id = -10099907
    reporter_id = 88807
    target_id = 77707
    now = int(time.time())

    db = mongo_store.get_mongo_db()
    db['action_events'].insert_one({
        'id': 1, 'scope': f'report:{chat_id}:{target_id}', 'actor_id': reporter_id,
        'chat_id': chat_id, 'target_id': target_id, 'event': 'report', 'created_at': now - 400
    })
    db['action_events'].insert_one({
        'id': 2, 'scope': f'report:{chat_id}:{target_id}', 'actor_id': reporter_id,
        'chat_id': chat_id, 'target_id': target_id, 'event': 'report', 'created_at': now - 10
    })

    deleted = mongo_store.delete_expired_action_events(max_age_seconds=300)
    assert deleted == 1

    # Recent event remains
    assert mongo_store.allow_report_event(chat_id, reporter_id, target_id, window_seconds=60, max_events=2) is True


# ----------------------------------------------------------------------------
# 5. temp_actions tests
# ----------------------------------------------------------------------------

def test_temp_actions_cleanup_expired_sqlite(sqlite_store):
    chat_id = -10099908
    user1 = 111108
    user2 = 222208
    now = int(time.time())

    sqlite_store.add_temp_action(chat_id, user1, 'tmute', now - 50)  # Expired
    sqlite_store.add_temp_action(chat_id, user2, 'tmute', now + 500) # Active / in the future

    cleaned = sqlite_store.cleanup_expired_temp_actions(now_ts=now)
    assert cleaned == 1

    # Active remains
    remaining = sqlite_store.get_expired_temp_actions(now_ts=now + 600)
    assert len(remaining) == 1
    assert remaining[0][1] == user2


def test_temp_actions_cleanup_expired_mongo(mongo_store):
    chat_id = -10099909
    user1 = 111109
    user2 = 222209
    now = int(time.time())

    mongo_store.add_temp_action(chat_id, user1, 'tban', now - 50)  # Expired
    mongo_store.add_temp_action(chat_id, user2, 'tban', now + 500) # Active / in the future

    cleaned = mongo_store.cleanup_expired_temp_actions(now_ts=now)
    assert cleaned == 1

    remaining = mongo_store.get_expired_temp_actions(now_ts=now + 600)
    assert len(remaining) == 1
    assert remaining[0][1] == user2


# ----------------------------------------------------------------------------
# 6. audit_logs tests
# ----------------------------------------------------------------------------

def test_audit_logs_configurable_retention_sqlite(sqlite_store):
    chat_id = -10099910
    actor_id = 999910
    now = int(time.time())

    # Add 2 logs
    sqlite_store.log_admin_action(chat_id, actor_id, 'ban', 101, 'old log')
    sqlite_store.log_admin_action(chat_id, actor_id, 'ban', 102, 'recent log')

    # Make the first log 10 days old
    with store.closing(store.conn()) as c:
        c.execute('UPDATE audit_logs SET created_at=? WHERE target_id=?', (now - (10 * 86400), 101))
        c.commit()

    # Retention of 7 days: old log is removed, recent is kept
    sqlite_store.delete_expired_audit_logs(chat_id, retention_seconds=7 * 86400)
    logs = sqlite_store.get_recent_audit_logs(chat_id)
    assert len(logs) == 1
    assert logs[0][2] == 102


def test_audit_logs_configurable_retention_mongo(mongo_store):
    chat_id = -10099911
    actor_id = 999911
    now = int(time.time())

    mongo_store.log_admin_action(chat_id, actor_id, 'ban', 101, 'old log')
    mongo_store.log_admin_action(chat_id, actor_id, 'ban', 102, 'recent log')

    db = mongo_store.get_mongo_db()
    db['audit_logs'].update_one({'target_id': 101}, {'$set': {'created_at': now - (10 * 86400)}})

    mongo_store.delete_expired_audit_logs(chat_id, retention_seconds=7 * 86400)
    logs = mongo_store.get_recent_audit_logs(chat_id)
    assert len(logs) == 1
    assert logs[0][2] == 102
