"""Local employee accounts and browser sessions."""

import hashlib
import hmac
import os
import re
import secrets
import time
from http.cookies import CookieError, SimpleCookie
from pathlib import Path


COOKIE = "netle_session"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ROUNDS = 600_000


def setup(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            role TEXT NOT NULL DEFAULT 'employee',
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS employee_sessions (
            token_hash TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS employee_refunds (
            refund_id TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id)
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(employees)")}
    if "role" not in columns:
        db.execute("ALTER TABLE employees ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'")
    if "active" not in columns:
        db.execute("ALTER TABLE employees ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if not db.execute("SELECT 1 FROM employees WHERE role = 'admin' AND active = 1 LIMIT 1").fetchone():
        first = db.execute("SELECT id FROM employees WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
        if first:
            with db:
                db.execute("UPDATE employees SET role = 'admin' WHERE id = ?", (first[0],))


def ensure_admin_from_env(db):
    """Use local .env credentials as the single configured administrator."""
    env_path = Path(__file__).with_name(".env")
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("\"'")
    username = os.environ.get("NETLE_ADMIN_USERNAME", values.get("NETLE_ADMIN_USERNAME", "")).strip().lower()
    password = os.environ.get("NETLE_ADMIN_PASSWORD", values.get("NETLE_ADMIN_PASSWORD", ""))
    if not username and not password:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,39}", username):
        raise ValueError(".env içindeki NETLE_ADMIN_USERNAME 3–40 karakter olmalı; harf, sayı, nokta, tire veya alt çizgi kullanılmalı.")
    if len(password) < 8 or len(password) > 128 or not re.search(r"[0-9]", password):
        raise ValueError(".env içindeki NETLE_ADMIN_PASSWORD en az 8 karakter ve bir rakam içermeli.")
    salt = secrets.token_bytes(16)
    with db:
        db.execute("UPDATE employees SET role='employee' WHERE role='admin'")
        existing = db.execute("SELECT id FROM employees WHERE username=?", (username,)).fetchone()
        if existing:
            employee_id = existing[0]
            db.execute("UPDATE employees SET salt=?, password_hash=?, role='admin', active=1 WHERE id=?",
                       (salt, password_hash(password, salt), employee_id))
            db.execute("DELETE FROM employee_sessions WHERE employee_id=?", (employee_id,))
        else:
            cursor = db.execute(
                "INSERT INTO employees (username,salt,password_hash,role) VALUES (?,?,?,'admin')",
                (username, salt, password_hash(password, salt)),
            )
            employee_id = cursor.lastrowid
    return username


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ROUNDS)


def session_hash(cookie_header):
    try:
        cookie = SimpleCookie(cookie_header).get(COOKIE)
    except CookieError:
        return None
    if cookie and re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie.value):
        return hashlib.sha256(cookie.value.encode("ascii")).hexdigest()
    return None


def employee(db, cookie_header):
    key = session_hash(cookie_header)
    if not key:
        return None
    return db.execute(
        "SELECT e.id, e.username, e.role FROM employee_sessions AS s "
        "JOIN employees AS e ON e.id = s.employee_id "
        "WHERE s.token_hash = ? AND s.expires_at > ? AND e.active = 1", (key, int(time.time())),
    ).fetchone()


def cookie(token="", max_age=0):
    return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"


def account_action(db, payload, cookie_header):
    action = payload.get("action")
    old_key = session_hash(cookie_header)
    if action == "status":
        account = employee(db, cookie_header)
        return {"username": account[1] if account else None,
                "role": account[2] if account else None}, None
    if action == "logout":
        with db:
            db.execute("DELETE FROM employee_sessions WHERE token_hash = ?", (old_key,))
        return {"username": None}, cookie()
    if action in ("list_employees", "delete_employee"):
        account = employee(db, cookie_header)
        if account is None or account[2] != "admin":
            raise ValueError("Çalışanları yalnızca yönetici yönetebilir.")
        if action == "list_employees":
            return {"employees": [row[0] for row in db.execute(
                "SELECT username FROM employees WHERE role = 'employee' AND active = 1 ORDER BY username"
            )]}, None
        username = payload.get("username")
        if not isinstance(username, str):
            raise ValueError("Geçerli bir çalışan kullanıcı adı gir.")
        username = username.strip().lower()
        with db:
            updated = db.execute(
                "UPDATE employees SET active = 0 WHERE username = ? AND role = 'employee' AND active = 1",
                (username,),
            )
            if not updated.rowcount:
                raise ValueError("Bu kullanıcı adıyla etkin bir çalışan hesabı bulunamadı.")
            db.execute(
                "DELETE FROM employee_sessions WHERE employee_id = (SELECT id FROM employees WHERE username = ?)",
                (username,),
            )
        return {"deleted_username": username}, None
    if action == "reset_employee_password":
        account = employee(db, cookie_header)
        if account is None or account[2] != "admin":
            raise ValueError("Çalışan parolasını yalnızca yönetici sıfırlayabilir.")
        username = payload.get("username")
        if not isinstance(username, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,39}", username.strip().lower()):
            raise ValueError("Geçerli bir çalışan kullanıcı adı gir.")
        username = username.strip().lower()
        temporary_password = secrets.token_urlsafe(18) + "1"
        salt = secrets.token_bytes(16)
        with db:
            updated = db.execute(
                "UPDATE employees SET salt = ?, password_hash = ? WHERE username = ? AND role = 'employee' AND active = 1",
                (salt, password_hash(temporary_password, salt), username),
            )
            if not updated.rowcount:
                raise ValueError("Bu kullanıcı adıyla bir çalışan hesabı bulunamadı.")
            db.execute(
                "DELETE FROM employee_sessions WHERE employee_id = (SELECT id FROM employees WHERE username = ?)",
                (username,),
            )
        return {"temporary_password": temporary_password}, None
    if action == "change_password":
        account = employee(db, cookie_header)
        if account is None:
            raise ValueError("Parola değiştirmek için yeniden oturum aç.")
        current = payload.get("current_password", "")
        new_password = payload.get("new_password", "")
        if not isinstance(current, str) or not 1 <= len(current) <= 128:
            raise ValueError("Mevcut parolanı gir.")
        if not isinstance(new_password, str) or not 8 <= len(new_password) <= 128 or not re.search(r"[0-9]", new_password):
            raise ValueError("Yeni parola en az 8 karakter ve en az bir rakam içermeli.")
        row = db.execute("SELECT salt,password_hash FROM employees WHERE id = ?", (account[0],)).fetchone()
        if not hmac.compare_digest(password_hash(current, row[0]), row[1]):
            raise ValueError("Mevcut şifrenizi yanlış girdiniz.")
        salt = secrets.token_bytes(16)
        with db:
            updated = db.execute("UPDATE employees SET salt = ?, password_hash = ? WHERE id = ? AND password_hash = ?",
                                 (salt, password_hash(new_password, salt), account[0], row[1]))
            if not updated.rowcount:
                raise ValueError("Parola değişti. Yeniden oturum aç.")
        return {"username": account[1], "role": account[2]}, None
    if action == "create_employee":
        account = employee(db, cookie_header)
        if account is None or account[2] != "admin":
            raise ValueError("Çalışan hesabını yalnızca yönetici oluşturabilir.")
    elif action != "login":
        raise ValueError("Bilinmeyen oturum işlemi.")

    username = payload.get("username", "")
    password = payload.get("password", "")
    if not isinstance(username, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{2,39}", username.strip().lower()
    ):
        raise ValueError("Kullanıcı adı 3–40 karakter olmalı ve harf, sayı, nokta, tire veya alt çizgi içerebilir.")
    username = username.strip().lower()
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise ValueError("Parola 1–128 karakter olmalı.")

    if action == "create_employee":
        if len(password) < 8 or not re.search(r"[0-9]", password):
            raise ValueError("Parola en az 8 karakter ve en az bir rakam içermeli.")
        salt = secrets.token_bytes(16)
        with db:
            added = db.execute(
                "INSERT INTO employees (username,salt,password_hash,role) VALUES (?,?,?,'employee') "
                "ON CONFLICT(username) DO NOTHING",
                (username, salt, password_hash(password, salt)),
            )
            if not added.rowcount:
                raise ValueError("Bu kullanıcı adı zaten kayıtlı.")
        return {"created_username": username}, None

    with db:
        row = db.execute(
            "SELECT id,salt,password_hash,role FROM employees WHERE username = ? AND active = 1", (username,),
        ).fetchone()
        salt = row[1] if row else bytes(16)
        candidate = password_hash(password, salt)
        if not hmac.compare_digest(candidate, row[2] if row else bytes(32)) or not row:
            raise ValueError("Kullanıcı adı veya parola yanlış.")
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM employee_sessions WHERE token_hash = ? OR expires_at <= ?", (old_key, now))
        added_session = db.execute(
            "INSERT INTO employee_sessions SELECT ?, ?, ? FROM employees WHERE id = ? AND password_hash = ? AND active = 1",
            (hashlib.sha256(token.encode("ascii")).hexdigest(), row[0], now + SESSION_SECONDS, row[0], row[2]),
        )
        if not added_session.rowcount:
            raise ValueError("Kullanıcı adı veya parola yanlış.")
    return {"username": username, "role": row[3]}, cookie(token, SESSION_SECONDS)
