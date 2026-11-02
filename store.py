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

VALID_FILTER_TYPES = {'text', 'sticker', 'voice', 'link', 'document', 'pdf'}

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
        db['sticker_packs'].create_index('set_name', unique=True)
        db['stickers'].create_index('set_name')
        db['emoji_packs'].create_index('set_name', unique=True)
        db['emojis'].create_index('set_name')
        db['afk'].create_index('user_id', unique=True)
        db['user_profile_snapshots'].create_index([('chat_id', 1), ('user_id', 1)], unique=True)
        db['join_request_decisions'].create_index([('chat_id', 1), ('user_id', 1)], unique=True)
        db['user_personas'].create_index('user_id', unique=True)
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
        cur.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, added_at INTEGER, last_seen_at INTEGER, is_bot INTEGER DEFAULT 0)')
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
        cur.execute('CREATE TABLE IF NOT EXISTS sticker_packs (set_name TEXT PRIMARY KEY, title TEXT, count INTEGER, updated_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS stickers (id INTEGER PRIMARY KEY AUTOINCREMENT, set_name TEXT, file_id TEXT, file_unique_id TEXT, emoji TEXT, sticker_type TEXT)')
        cur.execute('CREATE TABLE IF NOT EXISTS emoji_packs (set_name TEXT PRIMARY KEY, title TEXT, link TEXT, count INTEGER, updated_at INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS emojis (id INTEGER PRIMARY KEY AUTOINCREMENT, set_name TEXT, emoji TEXT, custom_emoji_id TEXT, reaction_enabled INTEGER DEFAULT 1)')
        cur.execute('CREATE TABLE IF NOT EXISTS afk (user_id INTEGER PRIMARY KEY, reason TEXT, afk_since INTEGER)')
        cur.execute('CREATE TABLE IF NOT EXISTS user_profile_snapshots (chat_id INTEGER, user_id INTEGER, first_name TEXT, last_name TEXT, username TEXT, updated_at INTEGER, PRIMARY KEY(chat_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS join_request_decisions (chat_id INTEGER, user_id INTEGER, action TEXT, admin_id INTEGER, created_at INTEGER, PRIMARY KEY(chat_id, user_id))')
        cur.execute('CREATE TABLE IF NOT EXISTS user_personas (user_id INTEGER PRIMARY KEY, persona TEXT NOT NULL DEFAULT "yuki", updated_at INTEGER)')

        # Schema migrations for existing SQLite databases
        cur.execute("PRAGMA table_info(emojis)")
        cols = [row[1] for row in cur.fetchall()]
        if 'reaction_enabled' not in cols:
            cur.execute("ALTER TABLE emojis ADD COLUMN reaction_enabled INTEGER DEFAULT 1")

        cur.execute("PRAGMA table_info(afk)")
        afk_cols = [row[1] for row in cur.fetchall()]
        if afk_cols and 'chat_id' in afk_cols:
            cur.execute('CREATE TABLE IF NOT EXISTS afk_global (user_id INTEGER PRIMARY KEY, reason TEXT, afk_since INTEGER)')
            cur.execute('INSERT OR REPLACE INTO afk_global (user_id, reason, afk_since) SELECT user_id, reason, MAX(afk_since) FROM afk GROUP BY user_id')
            cur.execute('DROP TABLE afk')
            cur.execute('ALTER TABLE afk_global RENAME TO afk')

        cur.execute("PRAGMA table_info(filters)")
        cols = [row[1] for row in cur.fetchall()]
        if 'filter_type' not in cols:
            cur.execute("ALTER TABLE filters ADD COLUMN filter_type TEXT DEFAULT 'text'")

        cur.execute("PRAGMA table_info(notes)")
        cols = [row[1] for row in cur.fetchall()]
        if 'note_type' not in cols:
            cur.execute("ALTER TABLE notes ADD COLUMN note_type TEXT DEFAULT 'text'")

        cur.execute("PRAGMA table_info(users)")
        cols = [row[1] for row in cur.fetchall()]
        if 'is_bot' not in cols:
            cur.execute("ALTER TABLE users ADD COLUMN is_bot INTEGER DEFAULT 0")

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


def upsert_user(user_id, full_name, username, touch_seen=True, is_bot=False):
    now = _now()
    is_bot_val = bool(is_bot)
    if is_mongo():
        db = get_mongo_db()
        existing = db['users'].find_one({'user_id': user_id})
        if existing:
            update_data = {'full_name': full_name, 'username': username, 'is_bot': is_bot_val}
            if touch_seen:
                update_data['last_seen_at'] = now
            db['users'].update_one({'user_id': user_id}, {'$set': update_data})
        else:
            db['users'].insert_one({
                'user_id': user_id,
                'full_name': full_name,
                'username': username,
                'is_bot': is_bot_val,
                'added_at': now,
                'last_seen_at': now
            })
        return

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('INSERT INTO users(user_id,full_name,username,added_at,last_seen_at,is_bot) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username, is_bot=excluded.is_bot, last_seen_at=CASE WHEN ? THEN excluded.last_seen_at ELSE users.last_seen_at END', (user_id, full_name, username, now, now, 1 if is_bot_val else 0, 1 if touch_seen else 0))
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




def get_group_members(chat_id):
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['group_members'].find({'chat_id': chat_id, 'status': 'member'}))
        res = []
        for d in docs:
            uid = d['user_id']
            u_doc = db['users'].find_one({'user_id': uid})
            full_name = u_doc.get('full_name') if u_doc else f"User {uid}"
            username = u_doc.get('username') if u_doc else None
            is_bot = bool(u_doc.get('is_bot', False)) if u_doc else False
            res.append((uid, full_name, username, is_bot))
        return res

    with closing(conn()) as c:
        rows = c.execute(
            'SELECT m.user_id, u.full_name, u.username, COALESCE(u.is_bot, 0) FROM group_members m LEFT JOIN users u ON m.user_id=u.user_id WHERE m.chat_id=? AND (m.status IS NULL OR m.status = "member")',
            (chat_id,)
        ).fetchall()
        return [(r[0], r[1] or f"User {r[0]}", r[2], bool(r[3])) for r in rows]

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



def get_all_user_ids():
    if is_mongo():
        db = get_mongo_db()
        docs = db['users'].find({}, {'user_id': 1})
        return [doc['user_id'] for doc in docs if 'user_id' in doc]

    with closing(conn()) as c:
        rows = c.execute('SELECT user_id FROM users').fetchall()
        return [r[0] for r in rows]


def get_user_count():
    if is_mongo():
        db = get_mongo_db()
        return db['users'].count_documents({})

    with closing(conn()) as c:
        return c.execute('SELECT COUNT(*) FROM users').fetchone()[0]


def set_maintenance_mode(enabled: bool) -> None:
    val = 'on' if enabled else 'off'
    set_setting(0, 'maintenance_mode', val)


def is_maintenance_mode() -> bool:
    val = get_setting(0, 'maintenance_mode', 'off')
    return str(val).lower() in ('on', 'true', '1')


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


def get_safe_users(chat_id: int) -> list:
    raw = get_setting(chat_id, 'safe_users', '')
    if not raw:
        return []
    try:
        import json
        val = json.loads(raw)
        if isinstance(val, list):
            return [int(x) for x in val if str(x).lstrip('-').isdigit()]
    except Exception:
        pass
    return [int(x.strip()) for x in str(raw).split(',') if x.strip().lstrip('-').isdigit()]


def is_safe_user(chat_id: int, user_id: int) -> bool:
    return user_id in get_safe_users(chat_id)


def add_safe_user(chat_id: int, user_id: int) -> bool:
    users = set(get_safe_users(chat_id))
    users.add(user_id)
    import json
    set_setting(chat_id, 'safe_users', json.dumps(sorted(list(users))))
    return True


def remove_safe_user(chat_id: int, user_id: int) -> bool:
    users = set(get_safe_users(chat_id))
    users.discard(user_id)
    import json
    set_setting(chat_id, 'safe_users', json.dumps(sorted(list(users))))
    return True


def get_approved_users(chat_id: int) -> list:
    raw = get_setting(chat_id, 'approved_users', '')
    if not raw:
        return []
    try:
        import json
        val = json.loads(raw)
        if isinstance(val, list):
            return [int(x) for x in val if str(x).lstrip('-').isdigit()]
    except Exception:
        pass
    return [int(x.strip()) for x in str(raw).split(',') if x.strip().lstrip('-').isdigit()]


def is_approved_user(chat_id: int, user_id: int) -> bool:
    return user_id in get_approved_users(chat_id)


def add_approved_user(chat_id: int, user_id: int) -> bool:
    users = set(get_approved_users(chat_id))
    users.add(user_id)
    import json
    set_setting(chat_id, 'approved_users', json.dumps(sorted(list(users))))
    return True


def remove_approved_user(chat_id: int, user_id: int) -> bool:
    users = set(get_approved_users(chat_id))
    users.discard(user_id)
    import json
    set_setting(chat_id, 'approved_users', json.dumps(sorted(list(users))))
    return True


def toggle_safe_user(chat_id: int, user_id: int) -> bool:
    users = set(get_safe_users(chat_id))
    if user_id in users:
        users.remove(user_id)
        is_added = False
    else:
        users.add(user_id)
        is_added = True
    import json
    set_setting(chat_id, 'safe_users', json.dumps(sorted(list(users))))
    return is_added


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


def save_note(chat_id, name, content, buttons='', note_type='text'):
    note_type = (note_type or 'text').lower().strip()
    if is_mongo():
        db = get_mongo_db()
        db['notes'].update_one(
            {'chat_id': chat_id, 'name': name.lower()},
            {'$set': {'content': content, 'buttons': buttons, 'note_type': note_type}},
            upsert=True
        )
        return

    with closing(conn()) as c:
        c.execute('INSERT INTO notes(chat_id,name,content,buttons,note_type) VALUES(?,?,?,?,?) ON CONFLICT(chat_id,name) DO UPDATE SET content=excluded.content, buttons=excluded.buttons, note_type=excluded.note_type', (chat_id, name.lower(), content, buttons, note_type))
        c.commit()


def get_note(chat_id, name):
    if is_mongo():
        db = get_mongo_db()
        doc = db['notes'].find_one({'chat_id': chat_id, 'name': name.lower()})
        return (doc['content'], doc.get('buttons', ''), doc.get('note_type', 'text')) if doc else None

    with closing(conn()) as c:
        row = c.execute('SELECT content, buttons, COALESCE(note_type, "text") FROM notes WHERE chat_id=? AND name=?', (chat_id, name.lower())).fetchone()
        return row if row else None


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


def delete_expired_audit_logs(chat_id=None, retention_seconds=None):
    if retention_seconds is None:
        retention_seconds = getattr(config, 'AUDIT_LOG_RETENTION_SECONDS', 7 * 86400)
    cutoff = _now() - retention_seconds
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


def cleanup_expired_temp_actions(now_ts=None):
    """
    Cleans up expired temp_actions records (where until_ts <= now_ts).
    Never removes records where until_ts > now_ts (active operations).
    Returns count of cleaned records.
    """
    now_ts = now_ts or _now()
    if is_mongo():
        db = get_mongo_db()
        res = db['temp_actions'].delete_many({'until_ts': {'$lte': now_ts}})
        return res.deleted_count
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM temp_actions WHERE until_ts <= ?', (now_ts,))
        c.commit()
        return cur.rowcount


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


def delete_report(report_id):
    """Deletes a report by its ID."""
    if is_mongo():
        db = get_mongo_db()
        db['reports'].delete_one({'id': report_id})
        return
    with closing(conn()) as c:
        c.execute('DELETE FROM reports WHERE id=?', (report_id,))
        c.commit()


def get_pending_reports(limit=50):
    """Retrieves pending reports that have not been successfully delivered/deleted."""
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['reports'].find().sort('created_at', 1).limit(limit))
        return [
            {
                'id': d.get('id'),
                'chat_id': d.get('chat_id'),
                'reporter_id': d.get('reporter_id'),
                'target_id': d.get('target_id'),
                'message_id': d.get('message_id'),
                'reason': d.get('reason', ''),
                'created_at': d.get('created_at')
            }
            for d in docs
        ]
    with closing(conn()) as c:
        rows = c.execute(
            'SELECT id, chat_id, reporter_id, target_id, message_id, reason, created_at FROM reports ORDER BY created_at ASC LIMIT ?',
            (limit,)
        ).fetchall()
        return [
            {
                'id': r[0],
                'chat_id': r[1],
                'reporter_id': r[2],
                'target_id': r[3],
                'message_id': r[4],
                'reason': r[5] or '',
                'created_at': r[6]
            }
            for r in rows
        ]


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


def delete_expired_action_events(max_age_seconds=300):
    """
    Deletes temporary operational action_events that have expired past their sliding window.
    Preserves recent records required for rate-limiting calculations.
    Returns the count of deleted records.
    """
    cutoff = int(time.time()) - max_age_seconds
    if is_mongo():
        db = get_mongo_db()
        res = db['action_events'].delete_many({'created_at': {'$lt': cutoff}})
        return res.deleted_count
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM action_events WHERE created_at < ?', (cutoff,))
        c.commit()
        return cur.rowcount


def allow_report_event(chat_id, reporter_id, target_id, window_seconds=60, max_events=2):
    now = int(time.time())
    cutoff = now - window_seconds
    scope = f'report:{chat_id}:{target_id}'
    delete_expired_action_events(window_seconds * 5)  # Safe cleanup of expired events
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
        db['user_personas'].delete_many({'user_id': user_id})
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
        c.execute('DELETE FROM afk WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM user_personas WHERE user_id=?', (user_id,))
        c.commit()


def set_global_link(key, value):
    value = clamp_text(value, MAX_LENGTHS['button_url'])
    set_setting(0, key, value)


def get_global_link(key, default=''):
    val = get_setting(0, key, default)
    return val if val else default


def record_user_message(chat_id, user_id, message_id, quota_limit=100):
    now = int(time.time())
    if is_mongo():
        db = get_mongo_db()
        db['user_messages'].replace_one(
            {'chat_id': chat_id, 'user_id': user_id, 'message_id': message_id},
            {'chat_id': chat_id, 'user_id': user_id, 'message_id': message_id, 'created_at': now},
            upsert=True
        )
        if quota_limit and quota_limit > 0:
            excess_docs = list(db['user_messages'].find(
                {'chat_id': chat_id, 'user_id': user_id}
            ).sort('message_id', -1).skip(quota_limit))
            if excess_docs:
                excess_ids = [d['_id'] for d in excess_docs]
                db['user_messages'].delete_many({'_id': {'$in': excess_ids}})
    else:
        with closing(conn()) as c:
            c.execute('INSERT OR REPLACE INTO user_messages(chat_id, user_id, message_id, created_at) VALUES(?,?,?,?)',
                      (chat_id, user_id, message_id, now))
            if quota_limit and quota_limit > 0:
                c.execute(
                    'DELETE FROM user_messages WHERE chat_id=? AND user_id=? AND message_id NOT IN '
                    '(SELECT message_id FROM user_messages WHERE chat_id=? AND user_id=? ORDER BY message_id DESC LIMIT ?)',
                    (chat_id, user_id, chat_id, user_id, quota_limit)
                )
            c.commit()


def clean_stale_user_messages(max_age_seconds=172800):
    """
    Safely purges user_messages older than max_age_seconds (default 48 hours).
    Returns count of removed records.
    """
    cutoff = int(time.time()) - max_age_seconds
    if is_mongo():
        db = get_mongo_db()
        res = db['user_messages'].delete_many({'created_at': {'$lt': cutoff}})
        return res.deleted_count
    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute('DELETE FROM user_messages WHERE created_at < ?', (cutoff,))
        c.commit()
        return cur.rowcount


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


def get_user_message_ids_in_range(chat_id, user_id, start_msg_id, end_msg_id):
    min_id = min(start_msg_id, end_msg_id)
    max_id = max(start_msg_id, end_msg_id)
    if is_mongo():
        db = get_mongo_db()
        docs = list(db['user_messages'].find({
            'chat_id': chat_id,
            'user_id': user_id,
            'message_id': {'$gte': min_id, '$lte': max_id}
        }).sort('message_id', 1))
        return [d['message_id'] for d in docs]
    else:
        with closing(conn()) as c:
            rows = c.execute(
                'SELECT message_id FROM user_messages WHERE chat_id=? AND user_id=? AND message_id>=? AND message_id<=? ORDER BY message_id ASC',
                (chat_id, user_id, min_id, max_id)
            ).fetchall()
            return [r[0] for r in rows]


def delete_recorded_message_ids(chat_id, message_ids):
    if not message_ids:
        return
    mids = list(message_ids)
    if is_mongo():
        db = get_mongo_db()
        db['user_messages'].delete_many({'chat_id': chat_id, 'message_id': {'$in': mids}})
    else:
        with closing(conn()) as c:
            c.executemany('DELETE FROM user_messages WHERE chat_id=? AND message_id=?', [(chat_id, mid) for mid in mids])
            c.commit()


# ============================================================================
# STICKER PACK MANAGEMENT
# ============================================================================

def save_sticker_pack(set_name: str, title: str, stickers_data: list):
    """
    Saves a sticker pack and its stickers to storage.
    stickers_data is a list of dicts: [{'file_id': ..., 'file_unique_id': ..., 'emoji': ..., 'type': ...}]
    """
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        db['sticker_packs'].update_one(
            {'set_name': set_name},
            {'$set': {'set_name': set_name, 'title': title, 'count': len(stickers_data), 'updated_at': now}},
            upsert=True
        )
        db['stickers'].delete_many({'set_name': set_name})
        if stickers_data:
            docs = [
                {
                    'set_name': set_name,
                    'file_id': s.get('file_id'),
                    'file_unique_id': s.get('file_unique_id', ''),
                    'emoji': s.get('emoji', ''),
                    'sticker_type': s.get('type', 'regular')
                }
                for s in stickers_data
            ]
            db['stickers'].insert_many(docs)
    else:
        with closing(conn()) as c:
            c.execute(
                'INSERT OR REPLACE INTO sticker_packs(set_name, title, count, updated_at) VALUES(?,?,?,?)',
                (set_name, title, len(stickers_data), now)
            )
            c.execute('DELETE FROM stickers WHERE set_name=?', (set_name,))
            for s in stickers_data:
                c.execute(
                    'INSERT INTO stickers(set_name, file_id, file_unique_id, emoji, sticker_type) VALUES(?,?,?,?,?)',
                    (
                        set_name,
                        s.get('file_id'),
                        s.get('file_unique_id', ''),
                        s.get('emoji', ''),
                        s.get('type', 'regular')
                    )
                )
            c.commit()


def get_sticker_pack(set_name: str):
    """Retrieves metadata and stickers for a specific pack set_name."""
    if is_mongo():
        db = get_mongo_db()
        pack = db['sticker_packs'].find_one({'set_name': set_name})
        if not pack:
            return None
        stickers = list(db['stickers'].find({'set_name': set_name}))
        return {
            'set_name': pack['set_name'],
            'title': pack.get('title', ''),
            'count': pack.get('count', len(stickers)),
            'stickers': stickers
        }
    else:
        with closing(conn()) as c:
            row = c.execute('SELECT set_name, title, count FROM sticker_packs WHERE set_name=?', (set_name,)).fetchone()
            if not row:
                return None
            s_rows = c.execute('SELECT file_id, file_unique_id, emoji, sticker_type FROM stickers WHERE set_name=?', (set_name,)).fetchall()
            stickers = [
                {'file_id': r[0], 'file_unique_id': r[1], 'emoji': r[2], 'sticker_type': r[3]}
                for r in s_rows
            ]
            return {
                'set_name': row[0],
                'title': row[1],
                'count': row[2],
                'stickers': stickers
            }


def get_all_stickers():
    """Retrieves all saved stickers across all packs."""
    if is_mongo():
        db = get_mongo_db()
        return list(db['stickers'].find({}))
    else:
        with closing(conn()) as c:
            s_rows = c.execute('SELECT set_name, file_id, file_unique_id, emoji, sticker_type FROM stickers').fetchall()
            return [
                {'set_name': r[0], 'file_id': r[1], 'file_unique_id': r[2], 'emoji': r[3], 'sticker_type': r[4]}
                for r in s_rows
            ]


def delete_group_data(chat_id):
    if is_mongo():
        db = get_mongo_db()
        db['filters'].delete_many({'chat_id': chat_id})
        db['notes'].delete_many({'chat_id': chat_id})
        db['blacklists'].delete_many({'chat_id': chat_id})
        db['warns'].delete_many({'chat_id': chat_id})
        db['settings'].delete_many({'chat_id': chat_id})
        db['welcome_buttons'].delete_many({'chat_id': chat_id})
        db['rules_buttons'].delete_many({'chat_id': chat_id})
        db['reports'].delete_many({'chat_id': chat_id})
        db['audit_logs'].delete_many({'chat_id': chat_id})
        db['action_events'].delete_many({'chat_id': chat_id})
        db['temp_actions'].delete_many({'chat_id': chat_id})
        db['federation_chats'].delete_many({'chat_id': chat_id})
        db['user_messages'].delete_many({'chat_id': chat_id})
        db['group_members'].delete_many({'chat_id': chat_id})
        db['groups'].delete_many({'chat_id': chat_id})
        return

    with get_conn() as c:
        c.execute('DELETE FROM filters WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM notes WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM blacklists WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM warns WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM settings WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM welcome_buttons WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM rules_buttons WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM reports WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM audit_logs WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM action_events WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM temp_actions WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM federation_chats WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM user_messages WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM group_members WHERE chat_id=?', (chat_id,))
        c.execute('DELETE FROM groups WHERE chat_id=?', (chat_id,))
        c.commit()


def get_all_users():
    if is_mongo():
        db = get_mongo_db()
        docs = db['users'].find({})
        return [{
            'user_id': d['user_id'],
            'full_name': d.get('full_name'),
            'username': d.get('username'),
            'added_at': d.get('added_at'),
            'last_seen_at': d.get('last_seen_at')
        } for d in docs if 'user_id' in d]

    with closing(conn()) as c:
        rows = c.execute('SELECT user_id, full_name, username, added_at, last_seen_at FROM users').fetchall()
        return [{
            'user_id': r[0],
            'full_name': r[1],
            'username': r[2],
            'added_at': r[3],
            'last_seen_at': r[4]
        } for r in rows]


def get_all_active_groups_detailed():
    if is_mongo():
        db = get_mongo_db()
        docs = db['groups'].find({'is_active': {'$ne': 0}})
        return [{
            'chat_id': d['chat_id'],
            'title': d.get('title') or str(d['chat_id']),
            'username': d.get('username'),
            'group_link': d.get('group_link'),
            'member_count': d.get('member_count'),
            'added_by_user_id': d.get('added_by_user_id'),
            'added_at': d.get('added_at')
        } for d in docs if 'chat_id' in d]

    with closing(conn()) as c:
        rows = c.execute('SELECT chat_id, title, username, group_link, member_count, added_by_user_id, added_at FROM groups WHERE is_active IS NULL OR is_active = 1').fetchall()
        return [{
            'chat_id': r[0],
            'title': r[1] or str(r[0]),
            'username': r[2],
            'group_link': r[3],
            'member_count': r[4],
            'added_by_user_id': r[5],
            'added_at': r[6]
        } for r in rows]


# --- AFK Data Layer ---

def set_afk(arg1: int, arg2=None, arg3=None, arg4=None) -> None:
    if isinstance(arg2, int):
        user_id = arg2
        reason = arg3 if arg3 is not None else ""
        afk_since = arg4
    else:
        user_id = arg1
        reason = arg2 if arg2 is not None else ""
        afk_since = arg3

    if afk_since is None:
        afk_since = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    reason = clamp_text(str(reason), 1000)
    if is_mongo():
        db = get_mongo_db()
        db['afk'].update_one(
            {'user_id': user_id},
            {'$set': {'user_id': user_id, 'reason': reason, 'afk_since': afk_since}},
            upsert=True
        )
        return

    with get_conn() as c:
        c.execute(
            'INSERT INTO afk (user_id, reason, afk_since) VALUES (?, ?, ?) '
            'ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, afk_since=excluded.afk_since',
            (user_id, reason, afk_since)
        )
        c.commit()


def get_afk(arg1: int, arg2: int = None):
    user_id = arg2 if arg2 is not None else arg1
    if is_mongo():
        db = get_mongo_db()
        doc = db['afk'].find_one({'user_id': user_id})
        if doc:
            return doc.get('reason', ''), doc.get('afk_since', 0)
        return None

    with get_conn() as c:
        row = c.execute('SELECT reason, afk_since FROM afk WHERE user_id=?', (user_id,)).fetchone()
        if row:
            return row[0], row[1]
        return None


def delete_afk(arg1: int, arg2: int = None) -> None:
    user_id = arg2 if arg2 is not None else arg1
    if is_mongo():
        db = get_mongo_db()
        db['afk'].delete_many({'user_id': user_id})
        return

    with get_conn() as c:
        c.execute('DELETE FROM afk WHERE user_id=?', (user_id,))
        c.commit()


# ============================================================================
# USER AI PERSONA PERSISTENCE
# ============================================================================

VALID_PERSONAS = {"yuki", "rukia", "renji", "zero_two"}
DEFAULT_PERSONA = "yuki"


def get_user_persona(user_id: int) -> str:
    """
    Retrieves the selected AI persona for a given user ID.
    Defaults to 'yuki' if not set or invalid.
    """
    if not user_id:
        return DEFAULT_PERSONA

    try:
        if is_mongo():
            db = get_mongo_db()
            doc = db['user_personas'].find_one({'user_id': user_id})
            if doc and doc.get('persona'):
                val = str(doc.get('persona')).strip().lower()
                return val if val in VALID_PERSONAS else DEFAULT_PERSONA
            return DEFAULT_PERSONA

        with closing(conn()) as c:
            row = c.execute('SELECT persona FROM user_personas WHERE user_id=?', (user_id,)).fetchone()
            if row and row[0]:
                val = str(row[0]).strip().lower()
                return val if val in VALID_PERSONAS else DEFAULT_PERSONA
            return DEFAULT_PERSONA
    except Exception as e:
        logger.warning(f"Error fetching persona for user {user_id}: {e}")
        return DEFAULT_PERSONA


def set_user_persona(user_id: int, persona: str) -> bool:
    """
    Persists the selected AI persona for a given user ID.
    Validates persona identifier against VALID_PERSONAS.
    Returns True if successfully set.
    """
    if not user_id or not persona:
        return False

    val = str(persona).strip().lower()
    if val not in VALID_PERSONAS:
        return False

    now = _now()
    try:
        if is_mongo():
            db = get_mongo_db()
            db['user_personas'].update_one(
                {'user_id': user_id},
                {'$set': {'persona': val, 'updated_at': now}},
                upsert=True
            )
            return True

        with closing(conn()) as c:
            c.execute(
                'INSERT INTO user_personas(user_id, persona, updated_at) VALUES(?,?,?) '
                'ON CONFLICT(user_id) DO UPDATE SET persona=excluded.persona, updated_at=excluded.updated_at',
                (user_id, val, now)
            )
            c.commit()
            return True
    except Exception as e:
        logger.error(f"Error saving persona for user {user_id}: {e}")
        return False


def reset_user_persona(user_id: int) -> bool:
    """
    Resets user's persona preference back to default ('yuki').
    """
    if not user_id:
        return False
    try:
        if is_mongo():
            db = get_mongo_db()
            db['user_personas'].delete_one({'user_id': user_id})
            return True

        with closing(conn()) as c:
            c.execute('DELETE FROM user_personas WHERE user_id=?', (user_id,))
            c.commit()
            return True
    except Exception as e:
        logger.error(f"Error resetting persona for user {user_id}: {e}")
        return False


def get_all_sticker_packs() -> list:
    """Retrieves metadata for all saved sticker packs."""
    try:
        if is_mongo():
            db = get_mongo_db()
            packs = list(db['sticker_packs'].find({}))
            res = []
            for p in packs:
                res.append({
                    'set_name': p.get('set_name', ''),
                    'title': p.get('title', ''),
                    'count': p.get('count', 0),
                    'updated_at': p.get('updated_at', 0)
                })
            return res
        else:
            with closing(conn()) as c:
                rows = c.execute('SELECT set_name, title, count, updated_at FROM sticker_packs ORDER BY updated_at DESC').fetchall()
                return [
                    {'set_name': r[0], 'title': r[1], 'count': r[2], 'updated_at': r[3]}
                    for r in rows
                ]
    except Exception as e:
        logger.error(f"Error fetching sticker packs: {e}")
        return []


def delete_sticker_pack(set_name: str) -> bool:
    """Deletes a sticker pack and its stickers by set_name. Returns True if deleted."""
    if not set_name:
        return False
    try:
        if is_mongo():
            db = get_mongo_db()
            pack_res = db['sticker_packs'].delete_one({'set_name': set_name})
            db['stickers'].delete_many({'set_name': set_name})
            return pack_res.deleted_count > 0
        else:
            with closing(conn()) as c:
                row = c.execute('SELECT set_name FROM sticker_packs WHERE set_name=?', (set_name,)).fetchone()
                if not row:
                    return False
                c.execute('DELETE FROM sticker_packs WHERE set_name=?', (set_name,))
                c.execute('DELETE FROM stickers WHERE set_name=?', (set_name,))
                c.commit()
                return True
    except Exception as e:
        logger.error(f"Error deleting sticker pack '{set_name}': {e}")
        return False



def get_profile_snapshot(chat_id, user_id):
    if not chat_id or not user_id:
        return None
    if is_mongo():
        db = get_mongo_db()
        doc = db['user_profile_snapshots'].find_one({'chat_id': chat_id, 'user_id': user_id})
        if doc:
            return {
                'chat_id': doc.get('chat_id'),
                'user_id': doc.get('user_id'),
                'first_name': doc.get('first_name') or '',
                'last_name': doc.get('last_name') or '',
                'username': doc.get('username') or '',
                'updated_at': doc.get('updated_at', 0)
            }
        return None

    with closing(conn()) as c:
        cur = c.cursor()
        row = cur.execute(
            'SELECT first_name, last_name, username, updated_at FROM user_profile_snapshots WHERE chat_id=? AND user_id=?',
            (chat_id, user_id)
        ).fetchone()
        if row:
            return {
                'chat_id': chat_id,
                'user_id': user_id,
                'first_name': row[0] or '',
                'last_name': row[1] or '',
                'username': row[2] or '',
                'updated_at': row[3] or 0
            }
        return None


def save_profile_snapshot(chat_id, user_id, first_name, last_name, username):
    if not chat_id or not user_id:
        return
    now = _now()
    fn = first_name or ''
    ln = last_name or ''
    un = username or ''
    if is_mongo():
        db = get_mongo_db()
        db['user_profile_snapshots'].update_one(
            {'chat_id': chat_id, 'user_id': user_id},
            {'$set': {
                'first_name': fn,
                'last_name': ln,
                'username': un,
                'updated_at': now
            }},
            upsert=True
        )
        return

    with closing(conn()) as c:
        cur = c.cursor()
        cur.execute(
            'INSERT INTO user_profile_snapshots(chat_id, user_id, first_name, last_name, username, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(chat_id, user_id) DO UPDATE SET '
            'first_name=excluded.first_name, last_name=excluded.last_name, username=excluded.username, updated_at=excluded.updated_at',
            (chat_id, user_id, fn, ln, un, now)
        )
        c.commit()



# ============================================================================
# EMOJI PACK MANAGEMENT
# ============================================================================

def save_emoji_pack(set_name: str, title: str, emojis_data: list, link: str = ""):
    """
    Saves an emoji pack and its emojis to storage.
    emojis_data is a list of dicts: [{'emoji': ..., 'custom_emoji_id': ...}]
    """
    now = _now()
    cleaned = []
    seen = set()
    for e in (emojis_data or []):
        emoji_str = str(e.get('emoji', '') or '').strip()
        custom_id_str = str(e.get('custom_emoji_id', '') or '').strip()
        if not emoji_str and not custom_id_str:
            continue
        key = (emoji_str, custom_id_str)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({'emoji': emoji_str, 'custom_emoji_id': custom_id_str})
    if is_mongo():
        db = get_mongo_db()
        db['emoji_packs'].update_one(
            {'set_name': set_name},
            {'$set': {'set_name': set_name, 'title': title, 'link': link, 'count': len(cleaned), 'updated_at': now}},
            upsert=True
        )
        db['emojis'].delete_many({'set_name': set_name})
        if cleaned:
            docs = [
                {
                    'set_name': set_name,
                    'emoji': item['emoji'],
                    'custom_emoji_id': item['custom_emoji_id'], 'reaction_enabled': True
                }
                for item in cleaned
            ]
            db['emojis'].insert_many(docs)
    else:
        with closing(conn()) as c:
            c.execute(
                'INSERT OR REPLACE INTO emoji_packs(set_name, title, link, count, updated_at) VALUES(?,?,?,?,?)',
                (set_name, title, link, len(cleaned), now)
            )
            c.execute('DELETE FROM emojis WHERE set_name=?', (set_name,))
            for item in cleaned:
                c.execute(
                    'INSERT INTO emojis(set_name, emoji, custom_emoji_id, reaction_enabled) VALUES(?,?,?,1)',
                    (
                        set_name,
                        item['emoji'],
                        item['custom_emoji_id']
                    )
                )
            c.commit()


def get_emoji_pack(set_name: str):
    """Retrieves metadata and emojis for a specific emoji pack set_name."""
    if not set_name:
        return None
    if is_mongo():
        db = get_mongo_db()
        pack = db['emoji_packs'].find_one({'set_name': set_name})
        if not pack:
            return None
        emojis = list(db['emojis'].find({'set_name': set_name}))
        return {
            'set_name': pack['set_name'],
            'title': pack.get('title', ''),
            'link': pack.get('link', ''),
            'count': pack.get('count', len(emojis)),
            'emojis': [{'emoji': e.get('emoji', ''), 'custom_emoji_id': e.get('custom_emoji_id', ''), 'reaction_enabled': bool(e.get('reaction_enabled', True))} for e in emojis]
        }
    else:
        with closing(conn()) as c:
            row = c.execute('SELECT set_name, title, link, count FROM emoji_packs WHERE set_name=?', (set_name,)).fetchone()
            if not row:
                return None
            e_rows = c.execute('SELECT emoji, custom_emoji_id, reaction_enabled FROM emojis WHERE set_name=?', (set_name,)).fetchall()
            emojis = [{'emoji': r[0], 'custom_emoji_id': r[1], 'reaction_enabled': bool(r[2] if r[2] is not None else 1)} for r in e_rows]
            return {
                'set_name': row[0],
                'title': row[1],
                'link': row[2],
                'count': row[3],
                'emojis': emojis
            }


def get_all_emoji_packs() -> list:
    """Retrieves metadata for all saved emoji packs."""
    try:
        if is_mongo():
            db = get_mongo_db()
            packs = list(db['emoji_packs'].find({}))
            res = []
            for p in packs:
                res.append({
                    'set_name': p.get('set_name', ''),
                    'title': p.get('title', ''),
                    'link': p.get('link', ''),
                    'count': p.get('count', 0),
                    'updated_at': p.get('updated_at', 0)
                })
            return res
        else:
            with closing(conn()) as c:
                rows = c.execute('SELECT set_name, title, link, count, updated_at FROM emoji_packs ORDER BY updated_at DESC').fetchall()
                return [
                    {'set_name': r[0], 'title': r[1], 'link': r[2], 'count': r[3], 'updated_at': r[4]}
                    for r in rows
                ]
    except Exception as e:
        logger.error(f"Error fetching emoji packs: {e}")
        return []


def get_all_emojis() -> list:
    """Retrieves all saved emojis across all emoji packs for reaction selection."""
    try:
        if is_mongo():
            db = get_mongo_db()
            emojis = list(db['emojis'].find({}))
            raw_list = [{
                'set_name': e.get('set_name', ''),
                'emoji': str(e.get('emoji', '') or '').strip(),
                'custom_emoji_id': str(e.get('custom_emoji_id', '') or '').strip(),
                'reaction_enabled': bool(e.get('reaction_enabled', True))
            } for e in emojis]
        else:
            with closing(conn()) as c:
                rows = c.execute('SELECT set_name, emoji, custom_emoji_id, reaction_enabled FROM emojis').fetchall()
                raw_list = [{
                    'set_name': r[0],
                    'emoji': str(r[1] or '').strip(),
                    'custom_emoji_id': str(r[2] or '').strip(),
                    'reaction_enabled': bool(r[3] if r[3] is not None else 1)
                } for r in rows]
        res = []
        for item in raw_list:
            if item['emoji'] or item['custom_emoji_id']:
                if item['reaction_enabled'] or item['emoji']:
                    res.append(item)
        return res
    except Exception as e:
        logger.error(f"Error fetching all emojis: {e}")
        return []




def get_all_custom_emojis() -> list:
    """
    Retrieves all stored custom emoji records with their metadata.
    Only returns items that have a non-empty custom_emoji_id.
    """
    try:
        if is_mongo():
            db = get_mongo_db()
            emojis = list(db['emojis'].find({}))
            packs = {p['set_name']: p.get('title', '') for p in db['emoji_packs'].find({})}
            res = []
            for e in emojis:
                cid = str(e.get('custom_emoji_id', '') or '').strip()
                if cid:
                    set_name = e.get('set_name', '')
                    res.append({
                        'set_name': set_name,
                        'title': packs.get(set_name, set_name),
                        'emoji': str(e.get('emoji', '') or '').strip(),
                        'custom_emoji_id': cid,
                        'reaction_enabled': bool(e.get('reaction_enabled', True))
                    })
            return res
        else:
            with closing(conn()) as c:
                rows = c.execute(
                    'SELECT e.set_name, COALESCE(p.title, e.set_name), e.emoji, e.custom_emoji_id, e.reaction_enabled '
                    'FROM emojis e LEFT JOIN emoji_packs p ON e.set_name = p.set_name'
                ).fetchall()
                res = []
                for r in rows:
                    cid = str(r[3] or '').strip()
                    if cid:
                        res.append({
                            'set_name': r[0],
                            'title': r[1],
                            'emoji': str(r[2] or '').strip(),
                            'custom_emoji_id': cid,
                            'reaction_enabled': bool(r[4] if r[4] is not None else 1)
                        })
                return res
    except Exception as e:
        logger.error(f'Error fetching all custom emojis: {e}')
        return []

def add_or_enable_reaction_emoji(custom_emoji_id: str, emoji: str = "", set_name: str = "custom_reactions", title: str = "Custom Reactions") -> bool:
    """
    Adds a custom emoji for reactions or enables reaction_enabled if it already exists.
    Stores custom_emoji_id, fallback emoji, set_name/title, and marks reaction_enabled=True.
    Returns True on success.
    """
    custom_id = str(custom_emoji_id or "").strip()
    if not custom_id:
        return False
    now = _now()
    emoji_char = str(emoji or "").strip()
    set_name = str(set_name or "custom_reactions").strip()
    title = str(title or "Custom Reactions").strip()

    try:
        if is_mongo():
            db = get_mongo_db()
            db["emoji_packs"].update_one(
                {"set_name": set_name},
                {"$setOnInsert": {"set_name": set_name, "title": title, "link": "", "count": 0, "updated_at": now}},
                upsert=True
            )
            existing = db["emojis"].find_one({"custom_emoji_id": custom_id})
            if existing:
                update_fields = {"reaction_enabled": True}
                if emoji_char:
                    update_fields["emoji"] = emoji_char
                db["emojis"].update_one({"custom_emoji_id": custom_id}, {"$set": update_fields})
            else:
                db["emojis"].insert_one({
                    "set_name": set_name,
                    "emoji": emoji_char,
                    "custom_emoji_id": custom_id,
                    "reaction_enabled": True
                })
            count = db["emojis"].count_documents({"set_name": set_name})
            db["emoji_packs"].update_one({"set_name": set_name}, {"$set": {"count": count, "updated_at": now}})
            return True
        else:
            with closing(conn()) as c:
                c.execute(
                    "INSERT OR IGNORE INTO emoji_packs(set_name, title, link, count, updated_at) VALUES(?,?,?,?,?)",
                    (set_name, title, "", 0, now)
                )
                row = c.execute("SELECT id, emoji FROM emojis WHERE custom_emoji_id=?", (custom_id,)).fetchone()
                if row:
                    if emoji_char:
                        c.execute("UPDATE emojis SET reaction_enabled=1, emoji=? WHERE custom_emoji_id=?", (emoji_char, custom_id))
                    else:
                        c.execute("UPDATE emojis SET reaction_enabled=1 WHERE custom_emoji_id=?", (custom_id,))
                else:
                    c.execute(
                        "INSERT INTO emojis(set_name, emoji, custom_emoji_id, reaction_enabled) VALUES(?,?,?,1)",
                        (set_name, emoji_char, custom_id)
                    )
                cnt = c.execute("SELECT COUNT(*) FROM emojis WHERE set_name=?", (set_name,)).fetchone()[0]
                c.execute("UPDATE emoji_packs SET count=?, updated_at=? WHERE set_name=?", (cnt, now, set_name))
                c.commit()
                return True
    except Exception as e:
        logger.error(f"Error adding or enabling reaction emoji custom_emoji_id '{custom_id}': {e}")
        return False


def disable_emoji_reaction(custom_emoji_id: str) -> bool:
    """Disables reaction_enabled for all emoji records matching custom_emoji_id."""
    custom_id = str(custom_emoji_id or '').strip()
    if not custom_id:
        return False
    try:
        if is_mongo():
            db = get_mongo_db()
            res = db['emojis'].update_many({'custom_emoji_id': custom_id}, {'$set': {'reaction_enabled': False}})
            return res.matched_count > 0
        else:
            with closing(conn()) as c:
                cur = c.execute('UPDATE emojis SET reaction_enabled=0 WHERE custom_emoji_id=?', (custom_id,))
                c.commit()
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Error disabling emoji reaction for custom_emoji_id '{custom_id}': {e}")
        return False


def save_join_request_decision(chat_id: int, user_id: int, action: str, admin_id: int):
    now = _now()
    if is_mongo():
        db = get_mongo_db()
        db['join_request_decisions'].update_one(
            {'chat_id': chat_id, 'user_id': user_id},
            {'$set': {'action': action, 'admin_id': admin_id, 'created_at': now}},
            upsert=True
        )
        return
    with get_conn() as c:
        c.execute(
            'INSERT INTO join_request_decisions(chat_id, user_id, action, admin_id, created_at) '
            'VALUES(?,?,?,?,?) ON CONFLICT(chat_id, user_id) DO UPDATE SET action=excluded.action, admin_id=excluded.admin_id, created_at=excluded.created_at',
            (chat_id, user_id, action, admin_id, now)
        )
        c.commit()


def get_join_request_decision(chat_id: int, user_id: int):
    if is_mongo():
        db = get_mongo_db()
        doc = db['join_request_decisions'].find_one({'chat_id': chat_id, 'user_id': user_id})
        if doc:
            return doc.get('action'), doc.get('admin_id'), doc.get('created_at', 0)
        return None
    with get_conn() as c:
        row = c.execute('SELECT action, admin_id, created_at FROM join_request_decisions WHERE chat_id=? AND user_id=?', (chat_id, user_id)).fetchone()
        if row:
            return row[0], row[1], row[2]
        return None


def clear_join_request_decision(chat_id: int, user_id: int):
    if is_mongo():
        db = get_mongo_db()
        db['join_request_decisions'].delete_one({'chat_id': chat_id, 'user_id': user_id})
        return
    with get_conn() as c:
        c.execute('DELETE FROM join_request_decisions WHERE chat_id=? AND user_id=?', (chat_id, user_id))
        c.commit()


def delete_emoji_pack(set_name: str) -> bool:
    """Deletes an emoji pack and its emojis by set_name. Returns True if deleted."""
    if not set_name:
        return False
    try:
        if is_mongo():
            db = get_mongo_db()
            pack_res = db['emoji_packs'].delete_one({'set_name': set_name})
            db['emojis'].delete_many({'set_name': set_name})
            return pack_res.deleted_count > 0
        else:
            with closing(conn()) as c:
                row = c.execute('SELECT set_name FROM emoji_packs WHERE set_name=?', (set_name,)).fetchone()
                if not row:
                    return False
                c.execute('DELETE FROM emoji_packs WHERE set_name=?', (set_name,))
                c.execute('DELETE FROM emojis WHERE set_name=?', (set_name,))
                c.commit()
                return True
    except Exception as e:
        logger.error(f"Error deleting emoji pack '{set_name}': {e}")
        return False


def format_bytes(size_bytes) -> str:
    """Formats bytes into human readable string (B, KB, MB, GB)."""
    if size_bytes is None:
        return "Unavailable"
    try:
        size_bytes = float(size_bytes)
    except (ValueError, TypeError):
        return "Unavailable"
    if size_bytes < 0:
        return "Unavailable"
    if size_bytes < 1024:
        return f"{int(size_bytes)} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_db_stats() -> dict:
    """
    Gathers real-time database statistics for the configured database backend (MongoDB or SQLite).
    Returns accurate metrics conforming to /db requirements:
    - Total Documents/Rows
    - Database Storage Used (disk usage vs logical data size)
    - Total Storage Capacity & quota type
    - Available Storage Remaining
    - Storage Usage Percentage
    - Index Count and Index Storage Size
    - Current Database Connections and Maximum Connections
    - Per-Collection/Table breakdown with document counts, storage size, and index size when supported.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    configured_limit_mb = getattr(config, 'DB_STORAGE_LIMIT_MB', None)
    if configured_limit_mb is None:
        raw_env = os.getenv('DB_STORAGE_LIMIT_MB', '').strip()
        if raw_env and raw_env.replace('.', '', 1).isdigit():
            configured_limit_mb = float(raw_env)

    stats = {
        'status': 'Disconnected',
        'backend': 'Unknown',
        'db_name': 'Unknown',
        'total_tables': 0,
        'total_documents': 0,
        'per_item_counts': {},
        'per_item_details': {},
        'db_storage_used_bytes': None,
        'db_storage_used': 'Unavailable',
        'logical_data_size_bytes': None,
        'logical_data_size': None,
        'storage_capacity_bytes': None,
        'storage_capacity': 'Unavailable',
        'storage_capacity_type': None,
        'storage_available_bytes': None,
        'storage_available': 'Unavailable',
        'storage_available_type': None,
        'storage_usage_percent': 'Unavailable',
        'storage_usage_type': None,
        'index_count': 'Unavailable',
        'index_storage_bytes': None,
        'index_storage_size': 'Unavailable',
        'connections_current': 'Unavailable',
        'connections_max': 'Unavailable',
        'connections_str': 'Unavailable',
        'version': 'Unavailable',
        'checked_at': now_utc,
        'error': None
    }

    if is_mongo():
        stats['backend'] = 'MongoDB'
        try:
            db = get_mongo_db()
            if db is None:
                stats['status'] = 'Disconnected'
                stats['error'] = 'MongoDB instance not initialized'
                return stats

            # Health check ping
            db.command('ping')
            stats['status'] = 'Connected'
            stats['db_name'] = getattr(db, 'name', 'yuuki_bot')

            # Server status (version & connections)
            try:
                server_status = db.command('serverStatus')
                if 'version' in server_status:
                    stats['version'] = f"MongoDB {server_status['version']}"
                if 'connections' in server_status:
                    curr_conn = server_status['connections'].get('current')
                    avail_conn = server_status['connections'].get('available')
                    stats['connections_current'] = curr_conn
                    if curr_conn is not None and avail_conn is not None:
                        max_conn = curr_conn + avail_conn
                        stats['connections_max'] = max_conn
                        stats['connections_str'] = f"{curr_conn} / {max_conn}"
                    elif curr_conn is not None:
                        stats['connections_str'] = f"{curr_conn}"
            except Exception:
                try:
                    build_info = db.command('buildInfo')
                    stats['version'] = f"MongoDB {build_info.get('version', 'Unavailable')}"
                except Exception:
                    stats['version'] = 'Unavailable'

            # dbStats command
            db_stats_cmd = {}
            try:
                db_stats_cmd = db.command('dbStats')
            except Exception as e:
                logger.debug(f"dbStats command failed or unsupported: {e}")

            # Collect collections list
            coll_names = []
            try:
                coll_names = db.list_collection_names()
                stats['total_tables'] = len(coll_names)
            except Exception as e:
                stats['total_tables'] = 'Unavailable'

            total_docs = 0
            per_coll_counts = {}
            per_coll_details = {}
            total_indexes = 0
            coll_sum_storage = 0
            coll_sum_index = 0
            has_coll_stats = False

            if isinstance(coll_names, list):
                for cname in sorted(coll_names):
                    if cname.startswith('system.'):
                        continue
                    try:
                        coll = db[cname]
                        # Use estimated document count or count_documents
                        cnt = coll.count_documents({})
                        per_coll_counts[cname] = cnt
                        total_docs += cnt

                        # Index count
                        idx_count = 0
                        try:
                            idx_info = coll.index_information()
                            idx_count = len(idx_info)
                            total_indexes += idx_count
                        except Exception:
                            pass

                        # Collection stats (collStats)
                        c_storage = None
                        c_idx_size = None
                        try:
                            cstats = db.command('collStats', cname)
                            c_storage = cstats.get('storageSize', cstats.get('size'))
                            c_idx_size = cstats.get('totalIndexSize')
                            if c_storage is not None:
                                coll_sum_storage += c_storage
                                has_coll_stats = True
                            if c_idx_size is not None:
                                coll_sum_index += c_idx_size
                        except Exception:
                            pass

                        per_coll_details[cname] = {
                            'count': cnt,
                            'storage_bytes': c_storage,
                            'storage_size': format_bytes(c_storage) if c_storage is not None else None,
                            'index_count': idx_count,
                            'index_bytes': c_idx_size,
                            'index_size': format_bytes(c_idx_size) if c_idx_size is not None else None
                        }
                    except Exception as err:
                        per_coll_counts[cname] = 'Error'
                        per_coll_details[cname] = {'count': 'Error', 'storage_size': None, 'index_size': None}

            stats['total_documents'] = total_docs
            stats['per_item_counts'] = per_coll_counts
            stats['per_item_details'] = per_coll_details

            # Total indexes
            if 'indexes' in db_stats_cmd:
                stats['index_count'] = db_stats_cmd['indexes']
            elif total_indexes > 0:
                stats['index_count'] = total_indexes

            # Storage sizes from dbStats or collection aggregation
            storage_bytes = db_stats_cmd.get('storageSize')
            data_bytes = db_stats_cmd.get('dataSize')
            idx_bytes = db_stats_cmd.get('indexSize')

            if storage_bytes is None and has_coll_stats:
                storage_bytes = coll_sum_storage
            if idx_bytes is None and has_coll_stats and coll_sum_index > 0:
                idx_bytes = coll_sum_index

            stats['db_storage_used_bytes'] = storage_bytes
            if storage_bytes is not None:
                stats['db_storage_used'] = format_bytes(storage_bytes)
            elif data_bytes is not None:
                stats['db_storage_used_bytes'] = data_bytes
                stats['db_storage_used'] = format_bytes(data_bytes)

            stats['logical_data_size_bytes'] = data_bytes
            if data_bytes is not None and storage_bytes is not None and data_bytes != storage_bytes:
                stats['logical_data_size'] = format_bytes(data_bytes)

            stats['index_storage_bytes'] = idx_bytes
            if idx_bytes is not None:
                stats['index_storage_size'] = format_bytes(idx_bytes)

            # Capacity / Quotas
            # MongoDB does not expose cluster plan quotas via normal client commands, unless maxStorage is in dbStats
            provider_quota_bytes = None
            if 'maxStorageSize' in db_stats_cmd:
                provider_quota_bytes = db_stats_cmd['maxStorageSize']

            if provider_quota_bytes is not None and provider_quota_bytes > 0:
                stats['storage_capacity_bytes'] = provider_quota_bytes
                stats['storage_capacity'] = format_bytes(provider_quota_bytes)
                stats['storage_capacity_type'] = 'provider quota'
                metric_used = storage_bytes if storage_bytes is not None else data_bytes
                if metric_used is not None:
                    avail_b = max(0.0, provider_quota_bytes - metric_used)
                    stats['storage_available_bytes'] = avail_b
                    stats['storage_available'] = format_bytes(avail_b)
                    stats['storage_available_type'] = 'verified'
                    pct = (metric_used / provider_quota_bytes) * 100.0
                    stats['storage_usage_percent'] = f"{pct:.2f}%"
                    stats['storage_usage_type'] = 'verified'
            elif configured_limit_mb is not None and configured_limit_mb > 0:
                limit_bytes = configured_limit_mb * 1024 * 1024
                stats['storage_capacity_bytes'] = limit_bytes
                stats['storage_capacity'] = f"{int(configured_limit_mb) if configured_limit_mb.is_integer() else configured_limit_mb} MB"
                stats['storage_capacity_type'] = 'configured limit'
                metric_used = storage_bytes if storage_bytes is not None else data_bytes
                if metric_used is not None:
                    avail_b = max(0.0, limit_bytes - metric_used)
                    stats['storage_available_bytes'] = avail_b
                    stats['storage_available'] = format_bytes(avail_b)
                    stats['storage_available_type'] = 'estimated'
                    pct = (metric_used / limit_bytes) * 100.0
                    stats['storage_usage_percent'] = f"{pct:.2f}%"
                    stats['storage_usage_type'] = 'estimated'

        except Exception as e:
            stats['status'] = 'Disconnected'
            stats['error'] = str(e)
            logger.error(f"Error gathering MongoDB stats: {e}")
        return stats

    # SQLite backend
    stats['backend'] = 'SQLite'
    try:
        import sqlite3
        stats['version'] = f"SQLite {sqlite3.sqlite_version}"

        db_file = DB_PATH
        stats['db_name'] = os.path.basename(db_file) if db_file else 'bot.db'

        # Connections for SQLite embedded database
        stats['connections_current'] = 1
        stats['connections_max'] = 1
        stats['connections_str'] = "1 / 1"

        with closing(conn()) as c:
            cur = c.cursor()
            cur.execute("SELECT 1")
            stats['status'] = 'Connected'

            # Table count and list
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            tables = [row[0] for row in cur.fetchall()]
            stats['total_tables'] = len(tables)

            # Gather per-page breakdown if dbstat virtual table is available
            dbstat_sizes = {}
            has_dbstat = False
            try:
                cur.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name")
                dbstat_sizes = dict(cur.fetchall())
                has_dbstat = True
            except Exception:
                has_dbstat = False

            total_rows = 0
            per_table_counts = {}
            per_table_details = {}
            total_indexes = 0
            total_index_bytes = 0

            for t in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM \"{t}\"")
                    cnt = cur.fetchone()[0]
                    per_table_counts[t] = cnt
                    total_rows += cnt

                    # Check indexes for table
                    t_idx_count = 0
                    t_idx_bytes = 0
                    try:
                        cur.execute(f"PRAGMA index_list(\"{t}\")")
                        for idx_row in cur.fetchall():
                            t_idx_count += 1
                            idx_name = idx_row[1]
                            if has_dbstat:
                                t_idx_bytes += dbstat_sizes.get(idx_name, 0)
                    except Exception:
                        pass

                    total_indexes += t_idx_count
                    total_index_bytes += t_idx_bytes

                    t_data_size = dbstat_sizes.get(t) if has_dbstat else None
                    per_table_details[t] = {
                        'count': cnt,
                        'storage_bytes': t_data_size,
                        'storage_size': format_bytes(t_data_size) if t_data_size is not None else None,
                        'index_count': t_idx_count,
                        'index_bytes': t_idx_bytes if has_dbstat else None,
                        'index_size': format_bytes(t_idx_bytes) if has_dbstat and t_idx_bytes > 0 else (format_bytes(0) if has_dbstat else None)
                    }
                except Exception:
                    per_table_counts[t] = 'Error'
                    per_table_details[t] = {'count': 'Error', 'storage_size': None, 'index_size': None}

            stats['total_documents'] = total_rows
            stats['per_item_counts'] = per_table_counts
            stats['per_item_details'] = per_table_details

            # Index count from sqlite_master
            cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")
            idx_row = cur.fetchone()
            stats['index_count'] = total_indexes if total_indexes > 0 else (idx_row[0] if idx_row else 0)

            # SQLite page sizing for logical vs file size
            try:
                cur.execute("PRAGMA page_size")
                page_size = cur.fetchone()[0]
                cur.execute("PRAGMA page_count")
                page_count = cur.fetchone()[0]
                cur.execute("PRAGMA freelist_count")
                freelist_count = cur.fetchone()[0]
                logical_bytes = page_size * (page_count - freelist_count)
                stats['logical_data_size_bytes'] = logical_bytes
            except Exception:
                pass

        # Physical file size on disk
        if os.path.exists(db_file):
            size_b = os.path.getsize(db_file)
            stats['db_storage_used_bytes'] = size_b
            stats['db_storage_used'] = format_bytes(size_b)
            if stats.get('logical_data_size_bytes') is not None and stats['logical_data_size_bytes'] != size_b:
                stats['logical_data_size'] = format_bytes(stats['logical_data_size_bytes'])
        else:
            stats['db_storage_used_bytes'] = 0
            stats['db_storage_used'] = '0 B'

        if has_dbstat:
            stats['index_storage_bytes'] = total_index_bytes
            stats['index_storage_size'] = format_bytes(total_index_bytes)
        else:
            stats['index_storage_size'] = 'Embedded in DB file'

        # Capacity and Available calculation for SQLite
        # Check configured_limit_mb first, then disk space
        if configured_limit_mb is not None and configured_limit_mb > 0:
            limit_bytes = configured_limit_mb * 1024 * 1024
            stats['storage_capacity_bytes'] = limit_bytes
            stats['storage_capacity'] = f"{int(configured_limit_mb) if configured_limit_mb.is_integer() else configured_limit_mb} MB"
            stats['storage_capacity_type'] = 'configured limit'
            used_b = stats['db_storage_used_bytes'] or 0
            avail_b = max(0.0, limit_bytes - used_b)
            stats['storage_available_bytes'] = avail_b
            stats['storage_available'] = format_bytes(avail_b)
            stats['storage_available_type'] = 'estimated'
            pct = (used_b / limit_bytes) * 100.0
            stats['storage_usage_percent'] = f"{pct:.2f}%"
            stats['storage_usage_type'] = 'estimated'
        else:
            # Fall back to host disk capacity where db_file resides
            try:
                import shutil
                total, used, free = shutil.disk_usage(os.path.dirname(os.path.abspath(db_file)))
                stats['storage_capacity_bytes'] = total
                stats['storage_capacity'] = format_bytes(total)
                stats['storage_capacity_type'] = 'disk capacity'
                stats['storage_available_bytes'] = free
                stats['storage_available'] = format_bytes(free)
                stats['storage_available_type'] = 'disk free'
                used_pct = (used / total) * 100.0
                stats['storage_usage_percent'] = f"{used_pct:.2f}%"
                stats['storage_usage_type'] = 'disk usage'
            except Exception:
                pass

    except Exception as e:
        stats['status'] = 'Disconnected'
        stats['error'] = str(e)
        logger.error(f"Error gathering SQLite stats: {e}")

    return stats
