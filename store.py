import random
import sqlite3
import time
from contextlib import closing

DB_PATH = 'bot.db'

CONTENT_QUOTAS = {
    'notes': 100,
    'filters': 200,
    'blacklists': 200,
    'reports_retained': 500,
    'buttons_per_scope': 10,
}

MAX_LENGTHS = {
    'fed_id': 64,
    'fed_name': 64,
    'note_name': 64,
    'note_content': 4000,
    'filter_keyword': 64,
    'filter_reply': 4000,
    'blacklist_trigger': 128,
    'report_reason': 300,
    'button_label': 32,
    'button_url': 500,
    'setting_value': 4000,
}

def clamp_text(value, limit):
    if value is None:
        return ''
    value = str(value).strip()
    return value[:limit]


def conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS groups (chat_id INTEGER PRIMARY KEY, title TEXT, username TEXT, added_at INTEGER, last_seen_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, added_at INTEGER, last_seen_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS group_members (chat_id INTEGER, user_id INTEGER, last_seen_at INTEGER, PRIMARY KEY(chat_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS settings (chat_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(chat_id, key))')
        cur.execute('CREATE TABLE IF NOT EXISTS warns (chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0, reasons TEXT DEFAULT "", PRIMARY KEY(chat_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS notes (chat_id INTEGER, name TEXT, content TEXT, buttons TEXT DEFAULT "", PRIMARY KEY(chat_id, name))')
        cur.execute('CREATE TABLE IF NOT EXISTS filters (chat_id INTEGER, keyword TEXT, reply TEXT, PRIMARY KEY(chat_id, keyword))')
        cur.execute('CREATE TABLE IF NOT EXISTS quizzes (quiz_id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, answer TEXT, added_by INTEGER, created_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, actor_id INTEGER, action TEXT, target_id INTEGER, details TEXT, created_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS action_events (id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, actor_id INTEGER NOT NULL, chat_id INTEGER, target_id INTEGER, event TEXT NOT NULL, created_at INTEGER NOT NULL)')
        cur.execute('CREATE TABLE IF NOT EXISTS blacklists (chat_id INTEGER, trigger TEXT, action TEXT DEFAULT "delete", PRIMARY KEY(chat_id, trigger))')
        cur.execute('CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, reporter_id INTEGER, target_id INTEGER, message_id INTEGER, reason TEXT, created_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS federations (fed_id TEXT PRIMARY KEY, fed_name TEXT, owner_id INTEGER, created_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS federation_chats (fed_id TEXT, chat_id INTEGER UNIQUE, added_by INTEGER, created_at INTEGER, PRIMARY KEY(fed_id, chat_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS fed_admins (fed_id TEXT, user_id INTEGER, PRIMARY KEY(fed_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS fed_bans (fed_id TEXT, user_id INTEGER, reason TEXT, banned_by INTEGER, created_at INTEGER, PRIMARY KEY(fed_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS temp_actions (chat_id INTEGER, user_id INTEGER, action TEXT, until_ts INTEGER, PRIMARY KEY(chat_id, user_id, action))')
        cur.execute('CREATE TABLE IF NOT EXISTS welcome_buttons (chat_id INTEGER, label TEXT, url TEXT, sort_order INTEGER DEFAULT 0, PRIMARY KEY(chat_id, label, url))')
        cur.execute('CREATE TABLE IF NOT EXISTS rules_buttons (chat_id INTEGER, label TEXT, url TEXT, sort_order INTEGER DEFAULT 0, PRIMARY KEY(chat_id, label, url))')
        c.commit()


def _now():
    return int(time.time())


def upsert_group(chat_id, title, username, touch_seen=True):
    now = _now()
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO groups(chat_id,title,username,added_at,last_seen_at) VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username, last_seen_at=CASE WHEN ? THEN excluded.last_seen_at ELSE groups.last_seen_at END', (chat_id, title, username, now, now, 1 if touch_seen else 0))
        c.commit()


def upsert_user(user_id, full_name, username, touch_seen=True):
    now = _now()
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO users(user_id,full_name,username,added_at,last_seen_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username, last_seen_at=CASE WHEN ? THEN excluded.last_seen_at ELSE users.last_seen_at END', (user_id, full_name, username, now, now, 1 if touch_seen else 0))
        c.commit()


def touch_member(chat_id, user_id, now_ts=None):
    now_ts = now_ts or _now()
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO group_members(chat_id,user_id,last_seen_at) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET last_seen_at=excluded.last_seen_at', (chat_id, user_id, now_ts))
        c.commit()


def get_active_groups():
    with closing(conn()) as c:
        return c.execute('SELECT chat_id, COALESCE(title, chat_id) FROM groups').fetchall()


def get_active_group_count():
    with closing(conn()) as c:
        return c.execute('SELECT COUNT(*) FROM groups').fetchone()[0]


def get_user_count():
    with closing(conn()) as c:
        return c.execute('SELECT COUNT(*) FROM users').fetchone()[0]


def set_setting(chat_id, key, value):
    with closing(conn()) as c:
        c.execute('INSERT INTO settings(chat_id,key,value) VALUES(?,?,?) ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value', (chat_id, key, str(value)))
        c.commit()


def get_setting(chat_id, key, default=None):
    with closing(conn()) as c:
        row = c.execute('SELECT value FROM settings WHERE chat_id=? AND key=?', (chat_id, key)).fetchone()
        return row[0] if row else default


def add_warn(chat_id, user_id, reason=''):
    with closing(conn()) as c:
        cur = c.cursor()
        row = cur.execute('SELECT count, reasons FROM warns WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
        count = (row[0] + 1) if row else 1
        reasons = (row[1] if row else '')
        if reason:
            reasons = (reasons + '\n' if reasons else '') + reason[:300]
        cur.execute('INSERT INTO warns(chat_id,user_id,count,reasons) VALUES(?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count, reasons=excluded.reasons', (chat_id, user_id, count, reasons))
        c.commit()
        return count


def get_warns(chat_id, user_id):
    with closing(conn()) as c:
        row = c.execute('SELECT count FROM warns WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
        return row[0] if row else 0


def get_warn_reasons(chat_id, user_id):
    with closing(conn()) as c:
        row = c.execute('SELECT reasons FROM warns WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
        return row[0] if row and row[0] else ''


def list_warned_users(chat_id, limit=50):
    with closing(conn()) as c:
        return c.execute('SELECT w.user_id, w.count, u.full_name FROM warns w LEFT JOIN users u ON u.user_id=w.user_id WHERE w.chat_id=? AND w.count>0 ORDER BY w.count DESC, w.user_id ASC LIMIT ?', (chat_id, limit)).fetchall()


def reset_warns(chat_id, user_id):
    with closing(conn()) as c:
        c.execute('DELETE FROM warns WHERE chat_id=? AND user_id=?', (chat_id, user_id))
        c.commit()


def save_note(chat_id, name, content, buttons=''):
    with closing(conn()) as c:
        c.execute('INSERT INTO notes(chat_id,name,content,buttons) VALUES(?,?,?,?) ON CONFLICT(chat_id,name) DO UPDATE SET content=excluded.content, buttons=excluded.buttons', (chat_id, name.lower(), content, buttons))
        c.commit()


def get_note(chat_id, name):
    with closing(conn()) as c:
        return c.execute('SELECT content, buttons FROM notes WHERE chat_id=? AND name=?', (chat_id, name.lower())).fetchone()


def list_notes(chat_id):
    with closing(conn()) as c:
        return c.execute('SELECT name FROM notes WHERE chat_id=? ORDER BY name', (chat_id,)).fetchall()


def delete_note(chat_id, name):
    with closing(conn()) as c:
        c.execute('DELETE FROM notes WHERE chat_id=? AND name=?', (chat_id, name.lower()))
        c.commit()


def save_filter(chat_id, keyword, reply):
    with closing(conn()) as c:
        c.execute('INSERT INTO filters(chat_id,keyword,reply) VALUES(?,?,?) ON CONFLICT(chat_id,keyword) DO UPDATE SET reply=excluded.reply', (chat_id, keyword.lower(), reply))
        c.commit()


def get_filters(chat_id):
    with closing(conn()) as c:
        return c.execute('SELECT keyword, reply FROM filters WHERE chat_id=? ORDER BY keyword', (chat_id,)).fetchall()


def delete_filter(chat_id, keyword):
    with closing(conn()) as c:
        c.execute('DELETE FROM filters WHERE chat_id=? AND keyword=?', (chat_id, keyword.lower()))
        c.commit()


def log_admin_action(chat_id, actor_id, action, target_id=None, details=None):
    with closing(conn()) as c:
        c.execute('INSERT INTO audit_logs(chat_id,actor_id,action,target_id,details,created_at) VALUES(?,?,?,?,?,?)', (chat_id, actor_id, action, target_id, details, _now()))
        c.commit()


def get_recent_audit_logs(chat_id, limit=10):
    with closing(conn()) as c:
        return c.execute('SELECT actor_id, action, target_id, details, datetime(created_at, "unixepoch") FROM audit_logs WHERE chat_id=? ORDER BY id DESC LIMIT ?', (chat_id, limit)).fetchall()


def add_quiz(question, answer, added_by):
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO quizzes(question,answer,added_by,created_at) VALUES(?,?,?,?)', (question, answer, added_by, _now()))
        c.commit()
        return cur.lastrowid


def list_quizzes():
    with closing(conn()) as c:
        return c.execute('SELECT quiz_id, question, answer, datetime(created_at, "unixepoch") FROM quizzes ORDER BY quiz_id DESC').fetchall()


def delete_quiz(quiz_id):
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM quizzes WHERE quiz_id=?', (quiz_id,))
        c.commit()
        return cur.rowcount > 0


def get_quiz_count():
    with closing(conn()) as c:
        return c.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0]


def get_random_quiz():
    with closing(conn()) as c:
        rows = c.execute('SELECT quiz_id, question, answer FROM quizzes').fetchall()
        return random.choice(rows) if rows else None


def add_blacklist(chat_id, trigger, action='delete'):
    with closing(conn()) as c:
        c.execute('INSERT INTO blacklists(chat_id,trigger,action) VALUES(?,?,?) ON CONFLICT(chat_id,trigger) DO UPDATE SET action=excluded.action', (chat_id, trigger.lower(), action))
        c.commit()


def remove_blacklist(chat_id, trigger):
    with closing(conn()) as c:
        c.execute('DELETE FROM blacklists WHERE chat_id=? AND trigger=?', (chat_id, trigger.lower()))
        c.commit()


def list_blacklists(chat_id):
    with closing(conn()) as c:
        return c.execute('SELECT trigger, action FROM blacklists WHERE chat_id=? ORDER BY trigger', (chat_id,)).fetchall()


def add_report(chat_id, reporter_id, target_id, message_id, reason=''):
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO reports(chat_id,reporter_id,target_id,message_id,reason,created_at) VALUES(?,?,?,?,?,?)', (chat_id, reporter_id, target_id, message_id, reason, _now()))
        c.commit()
        return cur.lastrowid


def create_federation(fed_id, fed_name, owner_id):
    with closing(conn()) as c:
        c.execute('INSERT INTO federations(fed_id,fed_name,owner_id,created_at) VALUES(?,?,?,?)', (fed_id.lower(), fed_name, owner_id, _now()))
        c.execute('INSERT OR IGNORE INTO fed_admins(fed_id,user_id) VALUES(?,?)', (fed_id.lower(), owner_id))
        c.commit()


def get_federation(fed_id):
    with closing(conn()) as c:
        return c.execute('SELECT fed_id, fed_name, owner_id FROM federations WHERE fed_id=?', (fed_id.lower(),)).fetchone()


def list_federations_by_owner(owner_id):
    with closing(conn()) as c:
        return c.execute('SELECT fed_id, fed_name FROM federations WHERE owner_id=? ORDER BY fed_name', (owner_id,)).fetchall()


def set_chat_federation(chat_id, fed_id, added_by):
    with closing(conn()) as c:
        c.execute('DELETE FROM federation_chats WHERE chat_id=?', (chat_id,))
        c.execute('INSERT INTO federation_chats(fed_id,chat_id,added_by,created_at) VALUES(?,?,?,?)', (fed_id.lower(), chat_id, added_by, _now()))
        c.commit()


def get_chat_federation(chat_id):
    with closing(conn()) as c:
        return c.execute('SELECT fed_id FROM federation_chats WHERE chat_id=?', (chat_id,)).fetchone()


def leave_chat_federation(chat_id):
    with closing(conn()) as c:
        c.execute('DELETE FROM federation_chats WHERE chat_id=?', (chat_id,))
        c.commit()


def add_fed_admin(fed_id, user_id):
    with closing(conn()) as c:
        c.execute('INSERT OR IGNORE INTO fed_admins(fed_id,user_id) VALUES(?,?)', (fed_id.lower(), user_id))
        c.commit()


def is_fed_admin(fed_id, user_id):
    with closing(conn()) as c:
        row = c.execute('SELECT 1 FROM fed_admins WHERE fed_id=? AND user_id=?', (fed_id.lower(), user_id)).fetchone()
        return bool(row)


def fed_ban_user(fed_id, user_id, reason, banned_by):
    with closing(conn()) as c:
        c.execute('INSERT INTO fed_bans(fed_id,user_id,reason,banned_by,created_at) VALUES(?,?,?,?,?) ON CONFLICT(fed_id,user_id) DO UPDATE SET reason=excluded.reason, banned_by=excluded.banned_by, created_at=excluded.created_at', (fed_id.lower(), user_id, reason, banned_by, _now()))
        c.commit()


def unfed_ban_user(fed_id, user_id):
    with closing(conn()) as c:
        c.execute('DELETE FROM fed_bans WHERE fed_id=? AND user_id=?', (fed_id.lower(), user_id))
        c.commit()


def get_fed_ban(fed_id, user_id):
    with closing(conn()) as c:
        return c.execute('SELECT reason FROM fed_bans WHERE fed_id=? AND user_id=?', (fed_id.lower(), user_id)).fetchone()


def add_temp_action(chat_id, user_id, action, until_ts):
    with closing(conn()) as c:
        c.execute('INSERT INTO temp_actions(chat_id,user_id,action,until_ts) VALUES(?,?,?,?) ON CONFLICT(chat_id,user_id,action) DO UPDATE SET until_ts=excluded.until_ts', (chat_id, user_id, action, until_ts))
        c.commit()


def clear_temp_action(chat_id, user_id, action):
    with closing(conn()) as c:
        c.execute('DELETE FROM temp_actions WHERE chat_id=? AND user_id=? AND action=?', (chat_id, user_id, action))
        c.commit()


def get_expired_temp_actions(now_ts=None):
    now_ts = now_ts or _now()
    with closing(conn()) as c:
        return c.execute('SELECT chat_id, user_id, action, until_ts FROM temp_actions WHERE until_ts<=?', (now_ts,)).fetchall()


def save_buttons(chat_id, scope, rows):
    table = 'welcome_buttons' if scope == 'welcome' else 'rules_buttons'
    with closing(conn()) as c:
        c.execute(f'DELETE FROM {table} WHERE chat_id=?', (chat_id,))
        for idx, (label, url) in enumerate(rows):
            c.execute(f'INSERT INTO {table}(chat_id,label,url,sort_order) VALUES(?,?,?,?)', (chat_id, label, url, idx))
        c.commit()


def get_buttons(chat_id, scope):
    table = 'welcome_buttons' if scope == 'welcome' else 'rules_buttons'
    with closing(conn()) as c:
        return c.execute(f'SELECT label, url FROM {table} WHERE chat_id=? ORDER BY sort_order', (chat_id,)).fetchall()


def check_chat_quota(chat_id, kind):
    with get_conn() as c:
        if kind == 'notes':
            row = c.execute('SELECT COUNT(*) FROM notes WHERE chat_id=?', (chat_id,)).fetchone()
            return (row[0] if row else 0) < CONTENT_QUOTAS['notes']
        if kind == 'filters':
            row = c.execute('SELECT COUNT(*) FROM filters WHERE chat_id=?', (chat_id,)).fetchone()
            return (row[0] if row else 0) < CONTENT_QUOTAS['filters']
        if kind == 'blacklists':
            row = c.execute('SELECT COUNT(*) FROM blacklists WHERE chat_id=?', (chat_id,)).fetchone()
            return (row[0] if row else 0) < CONTENT_QUOTAS['blacklists']
    return True


def trim_reports(chat_id):
    with get_conn() as c:
        keep = CONTENT_QUOTAS['reports_retained']
        c.execute('DELETE FROM reports WHERE chat_id=? AND id NOT IN (SELECT id FROM reports WHERE chat_id=? ORDER BY created_at DESC, id DESC LIMIT ?)', (chat_id, chat_id, keep))


def allow_report_event(chat_id, reporter_id, target_id, window_seconds=60, max_events=2):
    now = int(time.time())
    cutoff = now - window_seconds
    with get_conn() as c:
        row = c.execute('SELECT COUNT(*) FROM action_events WHERE scope=? AND actor_id=? AND event=? AND created_at>=?', (f'report:{chat_id}:{target_id}', reporter_id, 'report', cutoff)).fetchone()
        count = row[0] if row else 0
        if count >= max_events:
            return False
        c.execute('INSERT INTO action_events(scope, actor_id, chat_id, target_id, event, created_at) VALUES(?,?,?,?,?,?)', (f'report:{chat_id}:{target_id}', reporter_id, chat_id, target_id, 'report', now))
        return True


def report_exists_recent(chat_id, reporter_id, message_id, window_seconds=60):
    now = int(time.time())
    cutoff = now - window_seconds
    with get_conn() as c:
        row = c.execute('SELECT 1 FROM reports WHERE chat_id=? AND reporter_id=? AND message_id=? AND created_at>=? LIMIT 1', (chat_id, reporter_id, message_id, cutoff)).fetchone()
        return bool(row)


def get_group_quota_lines():
    return [
        f"Notes per group: {CONTENT_QUOTAS['notes']}",
        f"Filters per group: {CONTENT_QUOTAS['filters']}",
        f"Blacklists per group: {CONTENT_QUOTAS['blacklists']}",
        f"Reports retained per group: {CONTENT_QUOTAS['reports_retained']}",
        f"Buttons per scope: {CONTENT_QUOTAS['buttons_per_scope']}",
    ]


def delete_user_data(user_id):
    with get_conn() as c:
        c.execute('DELETE FROM users WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM members WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM warns WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM reports WHERE reporter_id=? OR target_id=?', (user_id, user_id))
        c.execute('DELETE FROM audit_logs WHERE actor_id=? OR target_id=?', (user_id, user_id))
        c.execute('DELETE FROM temp_actions WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM fed_bans WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM fed_admins WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM action_events WHERE actor_id=? OR target_id=?', (user_id, user_id))


def set_global_link(key, value):
    value = clamp_text(value, MAX_LENGTHS['button_url'])
    with get_conn() as c:
        c.execute('INSERT INTO settings(chat_id,key,value) VALUES(?,?,?) ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value', (0, key, value))


def get_global_link(key, default=''):
    with get_conn() as c:
        row = c.execute('SELECT value FROM settings WHERE chat_id=0 AND key=?', (key,)).fetchone()
        return row[0] if row and row[0] else default
