import datetime
import os
import random
import re
import sqlite3
import time
from contextlib import closing, contextmanager
import config

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

VALID_FILTER_TYPES = {'text', 'sticker', 'voice', 'link'}

_mongo_client = None
_mongo_db = None


def get_mongo_db():
    global _mongo_client, _mongo_db
    if _mongo_db is not None:
        return _mongo_db
    mongo_uri = getattr(config, 'MONGO_URI', None) or os.getenv('MONGO_URI') or os.getenv('MONGO_URL') or os.getenv('MONGODB_URI')
    if mongo_uri:
        from pymongo import MongoClient
        _mongo_client = MongoClient(mongo_uri)
        db_name = getattr(config, 'MONGO_DB_NAME', 'yuuki_bot')
        _mongo_db = _mongo_client[db_name]
        return _mongo_db
    return None


def is_mongo():
    return get_mongo_db() is not None


def clamp_text(value, limit):
    if value is None:
        return ''
    value = str(value).strip()
    return value[:limit]


def conn():
    return sqlite3.connect(DB_PATH)


@contextmanager
def get_conn():
    connection = conn()
    try:
        yield connection
    finally:
        connection.close()


def _get_next_id(db, collection_name):
    counter = db['counters'].find_one_and_update(
        {'_id': collection_name},
        {'$inc': {'seq': 1}},
        upsert=True,
        return_document=True
    )
    if counter is None: # fallback
        doc = db[collection_name].find_one(sort=[('id', -1)]) or db[collection_name].find_one(sort=[('quiz_id', -1)])
        last_id = doc.get('id', doc.get('quiz_id', 0)) if doc else 0
        return last_id + 1
    return counter['seq']


def init_db():
    if is_mongo():
        db = get_mongo_db()
        db['groups'].create_index('chat_id', unique=True)
        db['users'].create_index('user_id', unique=True)
        db['group_members'].create_index([('chat_id', 1), ('user_id', 1)], unique=True)
        db['settings'].create_index([('chat_id', 1), ('key', 1)], unique=True)
        db['warns'].create_index([('chat_id', 1), ('user_id', 1)], unique=True)
        db['notes'].create_index([('chat_id', 1), ('name', 1)], unique=True)
        db['filters'].create_index([('chat_id', 1), ('filter_type', 1), ('keyword', 1)], unique=True)
        db['quizzes'].create_index('quiz_id', unique=True)
        db['audit_logs'].create_index('id', unique=True)
        db['action_events'].create_index('id', unique=True)
        db['blacklists'].create_index([('chat_id', 1), ('trigger', 1)], unique=True)
        db['reports'].create_index('id', unique=True)
        db['federations'].create_index('fed_id', unique=True)
        db['federation_chats'].create_index('chat_id', unique=True)
        db['fed_admins'].create_index([('fed_id', 1), ('user_id', 1)], unique=True)
        db['fed_bans'].create_index([('fed_id', 1), ('user_id', 1)], unique=True)
        db['temp_actions'].create_index([('chat_id', 1), ('user_id', 1), ('action', 1)], unique=True)
        db['welcome_buttons'].create_index([('chat_id', 1), ('label', 1), ('url', 1)], unique=True)
        db['rules_buttons'].create_index([('chat_id', 1), ('label', 1), ('url', 1)], unique=True)
        db['user_messages'].create_index([('chat_id', 1), ('user_id', 1), ('message_id', 1)], unique=True)
        return

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            username TEXT,
            group_link TEXT,
            member_count INTEGER,
            added_by_user_id INTEGER,
            added_at INTEGER,
            current_bot_status TEXT DEFAULT "member",
            last_seen_at INTEGER,
            last_updated INTEGER,
            is_active INTEGER DEFAULT 1
        )''')
        cur.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, added_at INTEGER, last_seen_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS group_members (chat_id INTEGER, user_id INTEGER, last_seen_at INTEGER, status TEXT DEFAULT "member", PRIMARY KEY(chat_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS settings (chat_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(chat_id, key))')
        cur.execute('CREATE TABLE IF NOT EXISTS warns (chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0, reasons TEXT DEFAULT "", PRIMARY KEY(chat_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS notes (chat_id INTEGER, name TEXT, content TEXT, buttons TEXT DEFAULT "", PRIMARY KEY(chat_id, name))')
        cur.execute('CREATE TABLE IF NOT EXISTS filters (chat_id INTEGER, keyword TEXT, reply TEXT, filter_type TEXT DEFAULT "text", PRIMARY KEY(chat_id, filter_type, keyword))')
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
        cur.execute('CREATE TABLE IF NOT EXISTS user_messages (chat_id INTEGER, user_id INTEGER, message_id INTEGER, created_at INTEGER, PRIMARY KEY(chat_id, user_id, message_id))')

        # Schema migrations for existing SQLite databases
        cur.execute("PRAGMA table_info(filters)")
        cols = [row[1] for row in cur.fetchall()]
        if 'filter_type' not in cols:
            cur.execute("ALTER TABLE filters ADD COLUMN filter_type TEXT DEFAULT 'text'")

        cur.execute("PRAGMA table_info(group_members)")
        cols = [row[1] for row in cur.fetchall()]
        if 'status' not in cols:
            cur.execute("ALTER TABLE group_members ADD COLUMN status TEXT DEFAULT 'member'")

        cur.execute("PRAGMA table_info(groups)")
        cols = [row[1] for row in cur.fetchall()]
        if 'group_link' not in cols:
            cur.execute("ALTER TABLE groups ADD COLUMN group_link TEXT")
        if 'member_count' not in cols:
            cur.execute("ALTER TABLE groups ADD COLUMN member_count INTEGER")
        if 'added_by_user_id' not in cols:
            cur.execute("ALTER TABLE groups ADD COLUMN added_by_user_id INTEGER")
        if 'current_bot_status' not in cols:
            cur.execute("ALTER TABLE groups ADD COLUMN current_bot_status TEXT DEFAULT 'member'")
        if 'last_updated' not in cols:
            cur.execute("ALTER TABLE groups ADD COLUMN last_updated INTEGER")
        if 'is_active' not in cols:
            cur.execute("ALTER TABLE groups ADD COLUMN is_active INTEGER DEFAULT 1")

        c.commit()


def _now():
    return int(time.time())


def _format_time(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def upsert_group(chat_id, title=None, username=None, group_link=None, member_count=None, added_by_user_id=None, current_bot_status='member', touch_seen=True, is_active=1):
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        existing = db['groups'].find_one({'chat_id': chat_id})
        update_data = {
            'is_active': is_active,
            'current_bot_status': current_bot_status,
            'last_updated': now
        }
        if title is not None:
            update_data['title'] = title
        if username is not None:
            update_data['username'] = username
        if group_link is not None:
            update_data['group_link'] = group_link
        if member_count is not None:
            update_data['member_count'] = member_count
        if added_by_user_id is not None:
            update_data['added_by_user_id'] = added_by_user_id
        if touch_seen:
            update_data['last_seen_at'] = now

        if existing:
            db['groups'].update_one({'chat_id': chat_id}, {'$set': update_data})
        else:
            doc = {
                'chat_id': chat_id,
                'title': title if title is not None else str(chat_id),
                'username': username,
                'group_link': group_link,
                'member_count': member_count,
                'added_by_user_id': added_by_user_id,
                'added_at': now,
                'last_seen_at': now,
                'last_updated': now,
                'current_bot_status': current_bot_status,
                'is_active': is_active
            }
            db['groups'].insert_one(doc)
        return

    with closing(conn()) as c:
        cur = c.cursor()
        existing = cur.execute('SELECT title, username, group_link, member_count, added_by_user_id FROM groups WHERE chat_id=?', (chat_id,)).fetchone()
        if existing:
            new_title = title if title is not None else existing[0]
            new_username = username if username is not None else existing[1]
            new_link = group_link if group_link is not None else existing[2]
            new_member_cnt = member_count if member_count is not None else existing[3]
            new_added_by = added_by_user_id if added_by_user_id is not None else existing[4]
            cur.execute('''UPDATE groups SET
                title=?, username=?, group_link=?, member_count=?, added_by_user_id=?,
                current_bot_status=?, last_updated=?, is_active=?,
                last_seen_at=CASE WHEN ? THEN ? ELSE last_seen_at END
                WHERE chat_id=?''',
                (new_title, new_username, new_link, new_member_cnt, new_added_by,
                 current_bot_status, now, is_active, 1 if touch_seen else 0, now, chat_id))
        else:
            cur.execute('''INSERT INTO groups(
                chat_id, title, username, group_link, member_count, added_by_user_id,
                added_at, last_seen_at, last_updated, current_bot_status, is_active
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (chat_id, title if title is not None else str(chat_id), username, group_link, member_count, added_by_user_id,
             now, now, now, current_bot_status, is_active))
        c.commit()


def set_group_inactive(chat_id, current_bot_status='kicked', member_count=None):
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        update_data = {'is_active': 0, 'current_bot_status': current_bot_status, 'last_updated': now}
        if member_count is not None:
            update_data['member_count'] = member_count
        db['groups'].update_one({'chat_id': chat_id}, {'$set': update_data})
        return

    with closing(conn()) as c:
        cur = c.cursor()
        if member_count is not None:
            cur.execute('UPDATE groups SET is_active=0, current_bot_status=?, last_updated=?, member_count=? WHERE chat_id=?', (current_bot_status, now, member_count, chat_id))
        else:
            cur.execute('UPDATE groups SET is_active=0, current_bot_status=?, last_updated=? WHERE chat_id=?', (current_bot_status, now, chat_id))
        c.commit()


def get_group_by_id(chat_id):
    if is_mongo():
        db = get_mongo_db()
        doc = db['groups'].find_one({'chat_id': chat_id})
        if not doc:
            return None
        return {
            'chat_id': doc.get('chat_id'),
            'title': doc.get('title'),
            'username': doc.get('username'),
            'group_link': doc.get('group_link'),
            'member_count': doc.get('member_count'),
            'added_by_user_id': doc.get('added_by_user_id'),
            'added_at': doc.get('added_at'),
            'current_bot_status': doc.get('current_bot_status', 'member'),
            'last_seen_at': doc.get('last_seen_at'),
            'last_updated': doc.get('last_updated'),
            'is_active': doc.get('is_active', 1)
        }

    with closing(conn()) as c:
        cur = c.cursor()
        row = cur.execute('SELECT chat_id, title, username, group_link, member_count, added_by_user_id, added_at, current_bot_status, last_seen_at, last_updated, is_active FROM groups WHERE chat_id=?', (chat_id,)).fetchone()
        if not row:
            return None
        return {
            'chat_id': row[0],
            'title': row[1],
            'username': row[2],
            'group_link': row[3],
            'member_count': row[4],
            'added_by_user_id': row[5],
            'added_at': row[6],
            'current_bot_status': row[7],
            'last_seen_at': row[8],
            'last_updated': row[9],
            'is_active': row[10]
        }


def upsert_user(user_id, full_name, username, touch_seen=True):
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        existing = db['users'].find_one({'user_id': user_id})
        if existing:
            update_data = {'full_name': full_name, 'username': username}
            if touch_seen:
                update_data['last_seen_at'] = now
            db['users'].update_one({'user_id': user_id}, {'$set': update_data})
        else:
            db['users'].insert_one({
                'user_id': user_id,
                'full_name': full_name,
                'username': username,
                'added_at': now,
                'last_seen_at': now
            })
        return

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO users(user_id,full_name,username,added_at,last_seen_at) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username, last_seen_at=CASE WHEN ? THEN excluded.last_seen_at ELSE users.last_seen_at END', (user_id, full_name, username, now, now, 1 if touch_seen else 0))
        c.commit()


def get_user_by_username(username):
    if not username:
        return None
    username = username.lstrip('@').strip().lower()
    if is_mongo():
        db = get_mongo_db()
        doc = db['users'].find_one({'username': {'$regex': f'^{re.escape(username)}$', '$options': 'i'}})
        return (doc['user_id'], doc.get('full_name'), doc.get('username')) if doc else None

    with closing(conn()) as c:
        row = c.execute('SELECT user_id, full_name, username FROM users WHERE LOWER(username)=? LIMIT 1', (username,)).fetchone()
        return row if row else None


def get_user_by_id(user_id):
    if is_mongo():
        db = get_mongo_db()
        doc = db['users'].find_one({'user_id': user_id})
        return (doc['user_id'], doc.get('full_name'), doc.get('username')) if doc else None

    with closing(conn()) as c:
        row = c.execute('SELECT user_id, full_name, username FROM users WHERE user_id=? LIMIT 1', (user_id,)).fetchone()
        return row if row else None


def touch_member(chat_id, user_id, now_ts=None, status='member'):
    now_ts = now_ts or _now()
    if is_mongo():
        db = get_mongo_db()
        db['group_members'].update_one(
            {'chat_id': chat_id, 'user_id': user_id},
            {'$set': {'last_seen_at': now_ts, 'status': status}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO group_members(chat_id,user_id,last_seen_at,status) VALUES(?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET last_seen_at=excluded.last_seen_at, status=excluded.status', (chat_id, user_id, now_ts, status))
        c.commit()


def update_member_status(chat_id, user_id, status):
    touch_member(chat_id, user_id, status=status)


def list_zombies(chat_id):
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['group_members'].find({'chat_id': chat_id, 'status': {'$in': ['left', 'kicked', 'banned']}}))
        res = []
        for d in docs:
            uid = d['user_id']
            u_doc = db['users'].find_one({'user_id': uid})
            full_name = u_doc.get('full_name') if u_doc else f"User {uid}"
            username = u_doc.get('username') if u_doc else None
            res.append((uid, full_name, username, d.get('status', 'left')))
        return res

    with closing(conn()) as c:
        rows = c.execute('SELECT m.user_id, u.full_name, u.username, m.status FROM group_members m LEFT JOIN users u ON m.user_id=u.user_id WHERE m.chat_id=? AND m.status IN ("left", "kicked", "banned")', (chat_id,)).fetchall()
        return [(r[0], r[1] or f"User {r[0]}", r[2], r[3]) for r in rows]


def clean_zombies(chat_id):
    if is_mongo():
        db = get_mongo_db()
        res = db['group_members'].delete_many({'chat_id': chat_id, 'status': {'$in': ['left', 'kicked', 'banned']}})
        return res.deleted_count

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM group_members WHERE chat_id=? AND status IN ("left", "kicked", "banned")', (chat_id,))
        c.commit()
        return cur.rowcount


def get_active_groups():
    if is_mongo():
        db = get_mongo_db()
        docs = db['groups'].find({'is_active': {'$ne': 0}}, {'chat_id': 1, 'title': 1})
        return [(doc['chat_id'], doc.get('title') or doc['chat_id']) for doc in docs]

    with closing(conn()) as c:
        return c.execute('SELECT chat_id, COALESCE(title, chat_id) FROM groups WHERE is_active IS NULL OR is_active = 1').fetchall()


def get_active_group_count():
    if is_mongo():
        db = get_mongo_db()
        return db['groups'].count_documents({'is_active': {'$ne': 0}})

    with closing(conn()) as c:
        return c.execute('SELECT COUNT(*) FROM groups WHERE is_active IS NULL OR is_active = 1').fetchone()[0]


def get_user_count():
    if is_mongo():
        db = get_mongo_db()
        return db['users'].count_documents({})

    with closing(conn()) as c:
        return c.execute('SELECT COUNT(*) FROM users').fetchone()[0]


def set_setting(chat_id, key, value):
    if is_mongo():
        db = get_mongo_db()
        db['settings'].update_one(
            {'chat_id': chat_id, 'key': key},
            {'$set': {'value': str(value)}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO settings(chat_id,key,value) VALUES(?,?,?) ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value', (chat_id, key, str(value)))
        c.commit()


def get_setting(chat_id, key, default=None):
    if is_mongo():
        db = get_mongo_db()
        doc = db['settings'].find_one({'chat_id': chat_id, 'key': key})
        return doc['value'] if doc else default

    with closing(conn()) as c:
        row = c.execute('SELECT value FROM settings WHERE chat_id=? AND key=?', (chat_id, key)).fetchone()
        return row[0] if row else default


def add_warn(chat_id, user_id, reason=''):
    if is_mongo():
        db = get_mongo_db()
        doc = db['warns'].find_one({'chat_id': chat_id, 'user_id': user_id})
        count = (doc['count'] + 1) if doc else 1
        reasons = doc.get('reasons', '') if doc else ''
        if reason:
            reasons = (reasons + '\n' if reasons else '') + reason[:300]
        db['warns'].update_one(
            {'chat_id': chat_id, 'user_id': user_id},
            {'$set': {'count': count, 'reasons': reasons}},
            upsert=True
        )
        return count

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
    if is_mongo():
        db = get_mongo_db()
        doc = db['warns'].find_one({'chat_id': chat_id, 'user_id': user_id})
        return doc['count'] if doc else 0

    with closing(conn()) as c:
        row = c.execute('SELECT count FROM warns WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
        return row[0] if row else 0


def get_warn_reasons(chat_id, user_id):
    if is_mongo():
        db = get_mongo_db()
        doc = db['warns'].find_one({'chat_id': chat_id, 'user_id': user_id})
        return doc['reasons'] if doc and doc.get('reasons') else ''

    with closing(conn()) as c:
        row = c.execute('SELECT reasons FROM warns WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
        return row[0] if row and row[0] else ''


def list_warned_users(chat_id, limit=50):
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['warns'].find({'chat_id': chat_id, 'count': {'$gt': 0}}).sort([('count', -1), ('user_id', 1)]).limit(limit))
        res = []
        for d in docs:
            uid = d['user_id']
            cnt = d['count']
            reasons = d.get('reasons', '')
            user_doc = db['users'].find_one({'user_id': uid})
            name = user_doc.get('full_name') if user_doc else None
            username = user_doc.get('username') if user_doc else None
            res.append((uid, cnt, name, username, reasons))
        return res

    with closing(conn()) as c:
        rows = c.execute('SELECT w.user_id, w.count, u.full_name, u.username, w.reasons FROM warns w LEFT JOIN users u ON u.user_id=w.user_id WHERE w.chat_id=? AND w.count>0 ORDER BY w.count DESC, w.user_id ASC LIMIT ?', (chat_id, limit)).fetchall()
        return rows


def reset_warns(chat_id, user_id):
    if is_mongo():
        db = get_mongo_db()
        db['warns'].delete_one({'chat_id': chat_id, 'user_id': user_id})
        return

    with closing(conn()) as c:
        c.execute('DELETE FROM warns WHERE chat_id=? AND user_id=?', (chat_id, user_id))
        c.commit()


def save_note(chat_id, name, content, buttons=''):
    if is_mongo():
        db = get_mongo_db()
        db['notes'].update_one(
            {'chat_id': chat_id, 'name': name.lower()},
            {'$set': {'content': content, 'buttons': buttons}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO notes(chat_id,name,content,buttons) VALUES(?,?,?,?) ON CONFLICT(chat_id,name) DO UPDATE SET content=excluded.content, buttons=excluded.buttons', (chat_id, name.lower(), content, buttons))
        c.commit()


def get_note(chat_id, name):
    if is_mongo():
        db = get_mongo_db()
        doc = db['notes'].find_one({'chat_id': chat_id, 'name': name.lower()})
        return (doc['content'], doc.get('buttons', '')) if doc else None

    with closing(conn()) as c:
        return c.execute('SELECT content, buttons FROM notes WHERE chat_id=? AND name=?', (chat_id, name.lower())).fetchone()


def list_notes(chat_id):
    if is_mongo():
        db = get_mongo_db()
        docs = db['notes'].find({'chat_id': chat_id}).sort('name', 1)
        return [(d['name'],) for d in docs]

    with closing(conn()) as c:
        return c.execute('SELECT name FROM notes WHERE chat_id=? ORDER BY name', (chat_id,)).fetchall()


def delete_note(chat_id, name):
    if is_mongo():
        db = get_mongo_db()
        db['notes'].delete_one({'chat_id': chat_id, 'name': name.lower()})
        return

    with closing(conn()) as c:
        c.execute('DELETE FROM notes WHERE chat_id=? AND name=?', (chat_id, name.lower()))
        c.commit()


def save_filter(chat_id, keyword, reply, filter_type='text'):
    filter_type = (filter_type or 'text').lower().strip()
    if filter_type not in VALID_FILTER_TYPES:
        filter_type = 'text'
    keyword_clean = keyword.lower().strip()
    if is_mongo():
        db = get_mongo_db()
        db['filters'].update_one(
            {'chat_id': chat_id, 'filter_type': filter_type, 'keyword': keyword_clean},
            {'$set': {'reply': reply}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO filters(chat_id,keyword,reply,filter_type) VALUES(?,?,?,?) ON CONFLICT(chat_id,filter_type,keyword) DO UPDATE SET reply=excluded.reply', (chat_id, keyword_clean, reply, filter_type))
        c.commit()


def get_filters(chat_id, filter_type=None):
    if is_mongo():
        db = get_mongo_db()
        query = {'chat_id': chat_id}
        if filter_type:
            query['filter_type'] = filter_type.lower()
        docs = db['filters'].find(query).sort([('filter_type', 1), ('keyword', 1)])
        return [(d['keyword'], d['reply'], d.get('filter_type', 'text')) for d in docs]

    with closing(conn()) as c:
        if filter_type:
            rows = c.execute('SELECT keyword, reply, COALESCE(filter_type, "text") FROM filters WHERE chat_id=? AND LOWER(filter_type)=? ORDER BY keyword', (chat_id, filter_type.lower())).fetchall()
        else:
            rows = c.execute('SELECT keyword, reply, COALESCE(filter_type, "text") FROM filters WHERE chat_id=? ORDER BY filter_type, keyword', (chat_id,)).fetchall()
        return rows


def delete_filter(chat_id, keyword, filter_type=None):
    keyword_clean = keyword.lower().strip()
    if is_mongo():
        db = get_mongo_db()
        query = {'chat_id': chat_id, 'keyword': keyword_clean}
        if filter_type:
            query['filter_type'] = filter_type.lower()
        res = db['filters'].delete_many(query)
        return res.deleted_count > 0

    with closing(conn()) as c:
        cur = c.cursor()
        if filter_type:
            cur.execute('DELETE FROM filters WHERE chat_id=? AND keyword=? AND LOWER(filter_type)=?', (chat_id, keyword_clean, filter_type.lower()))
        else:
            cur.execute('DELETE FROM filters WHERE chat_id=? AND keyword=?', (chat_id, keyword_clean))
        c.commit()
        return cur.rowcount > 0


def delete_expired_audit_logs(chat_id=None):
    cutoff = _now() - 86400  # 24 hours ago
    if is_mongo():
        db = get_mongo_db()
        query = {'created_at': {'$lt': cutoff}}
        if chat_id is not None:
            query['chat_id'] = chat_id
        db['audit_logs'].delete_many(query)
        return

    with closing(conn()) as c:
        if chat_id is not None:
            c.execute('DELETE FROM audit_logs WHERE chat_id=? AND created_at<?', (chat_id, cutoff))
        else:
            c.execute('DELETE FROM audit_logs WHERE created_at<?', (cutoff,))
        c.commit()


def log_admin_action(chat_id, actor_id, action, target_id=None, details=None):
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        next_id = _get_next_id(db, 'audit_logs')
        db['audit_logs'].insert_one({
            'id': next_id,
            'chat_id': chat_id,
            'actor_id': actor_id,
            'action': action,
            'target_id': target_id,
            'details': details,
            'created_at': now
        })
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO audit_logs(chat_id,actor_id,action,target_id,details,created_at) VALUES(?,?,?,?,?,?)', (chat_id, actor_id, action, target_id, details, now))
        c.commit()


def get_recent_audit_logs(chat_id, limit=20):
    delete_expired_audit_logs(chat_id)
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['audit_logs'].find({'chat_id': chat_id}).sort('id', -1).limit(limit))
        return [(d['actor_id'], d['action'], d.get('target_id'), d.get('details'), d['created_at']) for d in docs]

    with closing(conn()) as c:
        return c.execute('SELECT actor_id, action, target_id, details, created_at FROM audit_logs WHERE chat_id=? ORDER BY id DESC LIMIT ?', (chat_id, limit)).fetchall()


def add_quiz(question, answer, added_by):
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        quiz_id = _get_next_id(db, 'quizzes')
        db['quizzes'].insert_one({
            'quiz_id': quiz_id,
            'question': question,
            'answer': answer,
            'added_by': added_by,
            'created_at': now
        })
        return quiz_id

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO quizzes(question,answer,added_by,created_at) VALUES(?,?,?,?)', (question, answer, added_by, now))
        c.commit()
        return cur.lastrowid


def list_quizzes():
    if is_mongo():
        db = get_mongo_db()
        docs = db['quizzes'].find({}).sort('quiz_id', -1)
        return [(d['quiz_id'], d['question'], d['answer'], _format_time(d['created_at'])) for d in docs]

    with closing(conn()) as c:
        return c.execute('SELECT quiz_id, question, answer, datetime(created_at, "unixepoch") FROM quizzes ORDER BY quiz_id DESC').fetchall()


def delete_quiz(quiz_id):
    if is_mongo():
        db = get_mongo_db()
        res = db['quizzes'].delete_one({'quiz_id': quiz_id})
        return res.deleted_count > 0

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM quizzes WHERE quiz_id=?', (quiz_id,))
        c.commit()
        return cur.rowcount > 0


def get_quiz_count():
    if is_mongo():
        db = get_mongo_db()
        return db['quizzes'].count_documents({})

    with closing(conn()) as c:
        return c.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0]


def get_random_quiz():
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['quizzes'].find({}))
        if not docs:
            return None
        d = random.choice(docs)
        return (d['quiz_id'], d['question'], d['answer'])

    with closing(conn()) as c:
        rows = c.execute('SELECT quiz_id, question, answer FROM quizzes').fetchall()
        return random.choice(rows) if rows else None


def add_blacklist(chat_id, trigger, action='delete'):
    if is_mongo():
        db = get_mongo_db()
        db['blacklists'].update_one(
            {'chat_id': chat_id, 'trigger': trigger.lower()},
            {'$set': {'action': action}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO blacklists(chat_id,trigger,action) VALUES(?,?,?) ON CONFLICT(chat_id,trigger) DO UPDATE SET action=excluded.action', (chat_id, trigger.lower(), action))
        c.commit()


def remove_blacklist(chat_id, trigger):
    if is_mongo():
        db = get_mongo_db()
        db['blacklists'].delete_one({'chat_id': chat_id, 'trigger': trigger.lower()})
        return

    with closing(conn()) as c:
        c.execute('DELETE FROM blacklists WHERE chat_id=? AND trigger=?', (chat_id, trigger.lower()))
        c.commit()


def list_blacklists(chat_id):
    if is_mongo():
        db = get_mongo_db()
        docs = db['blacklists'].find({'chat_id': chat_id}).sort('trigger', 1)
        return [(d['trigger'], d['action']) for d in docs]

    with closing(conn()) as c:
        return c.execute('SELECT trigger, action FROM blacklists WHERE chat_id=? ORDER BY trigger', (chat_id,)).fetchall()


def add_report(chat_id, reporter_id, target_id, message_id, reason=''):
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        report_id = _get_next_id(db, 'reports')
        db['reports'].insert_one({
            'id': report_id,
            'chat_id': chat_id,
            'reporter_id': reporter_id,
            'target_id': target_id,
            'message_id': message_id,
            'reason': reason,
            'created_at': now
        })
        return report_id

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO reports(chat_id,reporter_id,target_id,message_id,reason,created_at) VALUES(?,?,?,?,?,?)', (chat_id, reporter_id, target_id, message_id, reason, now))
        c.commit()
        return cur.lastrowid


def create_federation(fed_id, fed_name, owner_id):
    now = _now()
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        db['federations'].insert_one({
            'fed_id': fid,
            'fed_name': fed_name,
            'owner_id': owner_id,
            'created_at': now
        })
        db['fed_admins'].update_one(
            {'fed_id': fid, 'user_id': owner_id},
            {'$set': {'fed_id': fid, 'user_id': owner_id}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO federations(fed_id,fed_name,owner_id,created_at) VALUES(?,?,?,?)', (fid, fed_name, owner_id, now))
        c.execute('INSERT OR IGNORE INTO fed_admins(fed_id,user_id) VALUES(?,?)', (fid, owner_id))
        c.commit()


def get_federation(fed_id):
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        doc = db['federations'].find_one({'fed_id': fid})
        return (doc['fed_id'], doc['fed_name'], doc['owner_id']) if doc else None

    with closing(conn()) as c:
        return c.execute('SELECT fed_id, fed_name, owner_id FROM federations WHERE fed_id=?', (fid,)).fetchone()


def list_federations_by_owner(owner_id):
    if is_mongo():
        db = get_mongo_db()
        docs = db['federations'].find({'owner_id': owner_id}).sort('fed_name', 1)
        return [(d['fed_id'], d['fed_name']) for d in docs]

    with closing(conn()) as c:
        return c.execute('SELECT fed_id, fed_name FROM federations WHERE owner_id=? ORDER BY fed_name', (owner_id,)).fetchall()


def set_chat_federation(chat_id, fed_id, added_by):
    now = _now()
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        db['federation_chats'].delete_one({'chat_id': chat_id})
        db['federation_chats'].insert_one({
            'fed_id': fid,
            'chat_id': chat_id,
            'added_by': added_by,
            'created_at': now
        })
        return

    with closing(conn()) as c:
        c.execute('DELETE FROM federation_chats WHERE chat_id=?', (chat_id,))
        c.execute('INSERT INTO federation_chats(fed_id,chat_id,added_by,created_at) VALUES(?,?,?,?)', (fid, chat_id, added_by, now))
        c.commit()


def get_chat_federation(chat_id):
    if is_mongo():
        db = get_mongo_db()
        doc = db['federation_chats'].find_one({'chat_id': chat_id})
        return (doc['fed_id'],) if doc else None

    with closing(conn()) as c:
        return c.execute('SELECT fed_id FROM federation_chats WHERE chat_id=?', (chat_id,)).fetchone()


def leave_chat_federation(chat_id):
    if is_mongo():
        db = get_mongo_db()
        db['federation_chats'].delete_one({'chat_id': chat_id})
        return

    with closing(conn()) as c:
        c.execute('DELETE FROM federation_chats WHERE chat_id=?', (chat_id,))
        c.commit()


def add_fed_admin(fed_id, user_id):
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        db['fed_admins'].update_one(
            {'fed_id': fid, 'user_id': user_id},
            {'$set': {'fed_id': fid, 'user_id': user_id}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT OR IGNORE INTO fed_admins(fed_id,user_id) VALUES(?,?)', (fid, user_id))
        c.commit()


def is_fed_admin(fed_id, user_id):
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        doc = db['fed_admins'].find_one({'fed_id': fid, 'user_id': user_id})
        return bool(doc)

    with closing(conn()) as c:
        row = c.execute('SELECT 1 FROM fed_admins WHERE fed_id=? AND user_id=?', (fid, user_id)).fetchone()
        return bool(row)


def fed_ban_user(fed_id, user_id, reason, banned_by):
    now = _now()
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        db['fed_bans'].update_one(
            {'fed_id': fid, 'user_id': user_id},
            {'$set': {'reason': reason, 'banned_by': banned_by, 'created_at': now}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO fed_bans(fed_id,user_id,reason,banned_by,created_at) VALUES(?,?,?,?,?) ON CONFLICT(fed_id,user_id) DO UPDATE SET reason=excluded.reason, banned_by=excluded.banned_by, created_at=excluded.created_at', (fid, user_id, reason, banned_by, now))
        c.commit()


def unfed_ban_user(fed_id, user_id):
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        db['fed_bans'].delete_one({'fed_id': fid, 'user_id': user_id})
        return

    with closing(conn()) as c:
        c.execute('DELETE FROM fed_bans WHERE fed_id=? AND user_id=?', (fid, user_id))
        c.commit()


def get_fed_ban(fed_id, user_id):
    fid = fed_id.lower()
    if is_mongo():
        db = get_mongo_db()
        doc = db['fed_bans'].find_one({'fed_id': fid, 'user_id': user_id})
        return (doc['reason'],) if doc else None

    with closing(conn()) as c:
        return c.execute('SELECT reason FROM fed_bans WHERE fed_id=? AND user_id=?', (fid, user_id)).fetchone()


def add_temp_action(chat_id, user_id, action, until_ts):
    if is_mongo():
        db = get_mongo_db()
        db['temp_actions'].update_one(
            {'chat_id': chat_id, 'user_id': user_id, 'action': action},
            {'$set': {'until_ts': until_ts}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO temp_actions(chat_id,user_id,action,until_ts) VALUES(?,?,?,?) ON CONFLICT(chat_id,user_id,action) DO UPDATE SET until_ts=excluded.until_ts', (chat_id, user_id, action, until_ts))
        c.commit()


def clear_temp_action(chat_id, user_id, action):
    if is_mongo():
        db = get_mongo_db()
        db['temp_actions'].delete_one({'chat_id': chat_id, 'user_id': user_id, 'action': action})
        return

    with closing(conn()) as c:
        c.execute('DELETE FROM temp_actions WHERE chat_id=? AND user_id=? AND action=?', (chat_id, user_id, action))
        c.commit()


def get_expired_temp_actions(now_ts=None):
    now_ts = now_ts or _now()
    if is_mongo():
        db = get_mongo_db()
        docs = db['temp_actions'].find({'until_ts': {'$lte': now_ts}})
        return [(d['chat_id'], d['user_id'], d['action'], d['until_ts']) for d in docs]

    with closing(conn()) as c:
        return c.execute('SELECT chat_id, user_id, action, until_ts FROM temp_actions WHERE until_ts<=?', (now_ts,)).fetchall()


def save_buttons(chat_id, scope, rows):
    table = 'welcome_buttons' if scope == 'welcome' else 'rules_buttons'
    if is_mongo():
        db = get_mongo_db()
        db[table].delete_many({'chat_id': chat_id})
        if rows:
            docs = [{'chat_id': chat_id, 'label': label, 'url': url, 'sort_order': idx} for idx, (label, url) in enumerate(rows)]
            db[table].insert_many(docs)
        return

    with closing(conn()) as c:
        c.execute(f'DELETE FROM {table} WHERE chat_id=?', (chat_id,))
        for idx, (label, url) in enumerate(rows):
            c.execute(f'INSERT INTO {table}(chat_id,label,url,sort_order) VALUES(?,?,?,?)', (chat_id, label, url, idx))
        c.commit()


def get_buttons(chat_id, scope):
    table = 'welcome_buttons' if scope == 'welcome' else 'rules_buttons'
    if is_mongo():
        db = get_mongo_db()
        docs = db[table].find({'chat_id': chat_id}).sort('sort_order', 1)
        return [(d['label'], d['url']) for d in docs]

    with closing(conn()) as c:
        return c.execute(f'SELECT label, url FROM {table} WHERE chat_id=? ORDER BY sort_order', (chat_id,)).fetchall()


def check_chat_quota(chat_id, kind):
    if is_mongo():
        db = get_mongo_db()
        if kind == 'notes':
            cnt = db['notes'].count_documents({'chat_id': chat_id})
            return cnt < CONTENT_QUOTAS['notes']
        if kind == 'filters':
            cnt = db['filters'].count_documents({'chat_id': chat_id})
            return cnt < CONTENT_QUOTAS['filters']
        if kind == 'blacklists':
            cnt = db['blacklists'].count_documents({'chat_id': chat_id})
            return cnt < CONTENT_QUOTAS['blacklists']
        return True

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
    if is_mongo():
        db = get_mongo_db()
        keep = CONTENT_QUOTAS['reports_retained']
        docs = list(db['reports'].find({'chat_id': chat_id}).sort([('created_at', -1), ('id', -1)]).skip(keep))
        if docs:
            ids_to_del = [d['_id'] for d in docs]
            db['reports'].delete_many({'_id': {'$in': ids_to_del}})
        return

    with get_conn() as c:
        keep = CONTENT_QUOTAS['reports_retained']
        c.execute('DELETE FROM reports WHERE chat_id=? AND id NOT IN (SELECT id FROM reports WHERE chat_id=? ORDER BY created_at DESC, id DESC LIMIT ?)', (chat_id, chat_id, keep))
        c.commit()


def allow_report_event(chat_id, reporter_id, target_id, window_seconds=60, max_events=2):
    now = int(time.time())
    cutoff = now - window_seconds
    scope = f'report:{chat_id}:{target_id}'
    if is_mongo():
        db = get_mongo_db()
        cnt = db['action_events'].count_documents({
            'scope': scope,
            'actor_id': reporter_id,
            'event': 'report',
            'created_at': {'$gte': cutoff}
        })
        if cnt >= max_events:
            return False
        next_id = _get_next_id(db, 'action_events')
        db['action_events'].insert_one({
            'id': next_id,
            'scope': scope,
            'actor_id': reporter_id,
            'chat_id': chat_id,
            'target_id': target_id,
            'event': 'report',
            'created_at': now
        })
        return True

    with get_conn() as c:
        row = c.execute('SELECT COUNT(*) FROM action_events WHERE scope=? AND actor_id=? AND event=? AND created_at>=?', (scope, reporter_id, 'report', cutoff)).fetchone()
        count = row[0] if row else 0
        if count >= max_events:
            return False
        c.execute('INSERT INTO action_events(scope, actor_id, chat_id, target_id, event, created_at) VALUES(?,?,?,?,?,?)', (scope, reporter_id, chat_id, target_id, 'report', now))
        c.commit()
        return True


def report_exists_recent(chat_id, reporter_id, message_id, window_seconds=60):
    now = int(time.time())
    cutoff = now - window_seconds
    if is_mongo():
        db = get_mongo_db()
        doc = db['reports'].find_one({
            'chat_id': chat_id,
            'reporter_id': reporter_id,
            'message_id': message_id,
            'created_at': {'$gte': cutoff}
        })
        return bool(doc)

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
    if is_mongo():
        db = get_mongo_db()
        db['users'].delete_many({'user_id': user_id})
        db['group_members'].delete_many({'user_id': user_id})
        db['warns'].delete_many({'user_id': user_id})
        db['reports'].delete_many({'$or': [{'reporter_id': user_id}, {'target_id': user_id}]})
        db['audit_logs'].delete_many({'$or': [{'actor_id': user_id}, {'target_id': user_id}]})
        db['temp_actions'].delete_many({'user_id': user_id})
        db['fed_bans'].delete_many({'user_id': user_id})
        db['fed_admins'].delete_many({'user_id': user_id})
        db['action_events'].delete_many({'$or': [{'actor_id': user_id}, {'target_id': user_id}]})
        return

    with get_conn() as c:
        c.execute('DELETE FROM users WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM group_members WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM warns WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM reports WHERE reporter_id=? OR target_id=?', (user_id, user_id))
        c.execute('DELETE FROM audit_logs WHERE actor_id=? OR target_id=?', (user_id, user_id))
        c.execute('DELETE FROM temp_actions WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM fed_bans WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM fed_admins WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM action_events WHERE actor_id=? OR target_id=?', (user_id, user_id))
        c.commit()


def set_global_link(key, value):
    value = clamp_text(value, MAX_LENGTHS['button_url'])
    set_setting(0, key, value)


def get_global_link(key, default=''):
    val = get_setting(0, key, default)
    return val if val else default


def record_user_message(chat_id, user_id, message_id):
    now = int(time.time())
    if is_mongo():
        db = get_mongo_db()
        db['user_messages'].replace_one(
            {'chat_id': chat_id, 'user_id': user_id, 'message_id': message_id},
            {'chat_id': chat_id, 'user_id': user_id, 'message_id': message_id, 'created_at': now},
            upsert=True
        )
    else:
        with closing(conn()) as c:
            c.execute('INSERT OR REPLACE INTO user_messages(chat_id, user_id, message_id, created_at) VALUES(?,?,?,?)',
                      (chat_id, user_id, message_id, now))
            c.commit()


def get_user_messages(chat_id, user_id):
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['user_messages'].find({'chat_id': chat_id, 'user_id': user_id}).sort('message_id', -1))
        return [d['message_id'] for d in docs]
    else:
        with closing(conn()) as c:
            rows = c.execute('SELECT message_id FROM user_messages WHERE chat_id=? AND user_id=? ORDER BY message_id DESC',
                             (chat_id, user_id)).fetchall()
            return [r[0] for r in rows]


def clear_user_messages(chat_id, user_id):
    msg_ids = get_user_messages(chat_id, user_id)
    if is_mongo():
        db = get_mongo_db()
        db['user_messages'].delete_many({'chat_id': chat_id, 'user_id': user_id})
    else:
        with closing(conn()) as c:
            c.execute('DELETE FROM user_messages WHERE chat_id=? AND user_id=?', (chat_id, user_id))
            c.commit()
    return msg_ids
