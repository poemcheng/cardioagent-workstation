import re
import threading
import time
from werkzeug.security import generate_password_hash, check_password_hash

ROLES = {'admin', 'radiologist', 'coordinator', 'viewer'}


class Auth:
    def __init__(self, store):
        self.store = store
        self.failures = {}
        self.lock = threading.RLock()

    def users(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT username,role,active FROM users ORDER BY username')]

    def add(self, username, password, role, actor='initial-setup', initial=False):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}', username):
            raise ValueError('帳號須為 3–40 位英數或 _ . -。')
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise ValueError('密碼須為 12–128 字元。')
        if role not in ROLES:
            raise ValueError('角色無效。')
        with self.store.connect() as db:
            if initial and db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise PermissionError('初始設定已完成。')
            if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                raise ValueError('帳號已存在。')
            db.execute('INSERT INTO users(username,password_hash,role) VALUES (?,?,?)', (username, generate_password_hash(password), role))
            self.store.record_event('auth', 'user_created', {'actor': actor, 'username': username, 'role': role}, db)
        return {'username': username, 'role': role, 'active': True}

    def user(self, username):
        with self.store.connect() as db:
            row = db.execute('SELECT username,role,active FROM users WHERE username=?', (username,)).fetchone()
            return dict(row) if row and row['active'] else None

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username)>40 or len(password)>128:
            raise ValueError('登入資料無效。')
        with self.lock:
            stamp=time.monotonic(); attempts=[t for t in self.failures.get(username,[]) if stamp-t<60]
            if len(attempts)>=5:
                raise PermissionError('嘗試次數過多，請於一分鐘後再試。')
            with self.store.connect() as db:
                row=db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if not row or not row['active'] or not check_password_hash(row['password_hash'],password):
                self.failures[username]=attempts+[stamp]
                self.store.record_event('auth', 'login_failed', {'username': username})
                raise PermissionError('帳號或密碼錯誤。')
            self.failures.pop(username,None)
            self.store.record_event('auth', 'login_success', {'username': username, 'role': row['role']})
            return {'username': username, 'role': row['role']}
