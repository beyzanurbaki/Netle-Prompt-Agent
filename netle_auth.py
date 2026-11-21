"""Local employee accounts and browser sessions."""

import hashlib
import hmac
import re
import secrets
import time
from http.cookies import CookieError, SimpleCookie


COOKIE = "netle_session"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ROUNDS = 600_000


def setup(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            salt BLOB NOT NULL,
            password_hash BLOB NOT NULL
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
        "SELECT e.id, e.username FROM employee_sessions AS s "
        "JOIN employees AS e ON e.id = s.employee_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?", (key, int(time.time())),
    ).fetchone()


def cookie(token="", max_age=0):
    return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"


def account_action(db, payload, cookie_header):
    action = payload.get("action")
    old_key = session_hash(cookie_header)
    if action == "status":
        account = employee(db, cookie_header)
        return {"username": account[1] if account else None}, None
    if action == "logout":
        with db:
            db.execute("DELETE FROM employee_sessions WHERE token_hash = ?", (old_key,))
        return {"username": None}, cookie()
    if action not in ("register", "login"):
        raise ValueError("Bilinmeyen oturum işlemi.")

    username = payload.get("username", "").strip().lower()
    password = payload.get("password", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,39}", username):
        raise ValueError("Kullanıcı adı 3–40 karakter olmalı ve harf, sayı, nokta, tire veya alt çizgi içerebilir.")
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise ValueError("Parola 1–128 karakter olmalı.")
    if action == "register" and (len(password) < 8 or not re.search(r"[0-9]", password)):
        raise ValueError("Parola en az 8 karakter ve en az bir rakam içermeli.")

    with db:
        if action == "register":
            salt = secrets.token_bytes(16)
            added = db.execute(
                "INSERT INTO employees (username,salt,password_hash) VALUES (?,?,?) "
                "ON CONFLICT(username) DO NOTHING", (username, salt, password_hash(password, salt)),
            )
            if not added.rowcount:
                raise ValueError("Bu kullanıcı adı zaten kayıtlı.")
            employee_id = added.lastrowid
        else:
            row = db.execute(
                "SELECT id,salt,password_hash FROM employees WHERE username = ?", (username,),
            ).fetchone()
            salt = row[1] if row else bytes(16)
            candidate = password_hash(password, salt)
            if not hmac.compare_digest(candidate, row[2] if row else bytes(32)) or not row:
                raise ValueError("Kullanıcı adı veya parola yanlış.")
            employee_id = row[0]
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM employee_sessions WHERE token_hash = ? OR expires_at <= ?", (old_key, now))
        db.execute(
            "INSERT INTO employee_sessions VALUES (?,?,?)",
            (hashlib.sha256(token.encode("ascii")).hexdigest(), employee_id, now + SESSION_SECONDS),
        )
    return {"username": username}, cookie(token, SESSION_SECONDS)
