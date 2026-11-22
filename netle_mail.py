"""Send an employee-approved reply through the configured SMTP relay."""

import os
import re
import smtplib
import ssl
from email.headerregistry import Address
from email.errors import HeaderParseError
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path


def message_ids(header):
    return re.findall(r"<[^<>\s@]+@[^<>\s@]+>", str(header or ""))


def reply_headers(thread):
    parent = thread.get("message_id")
    references = thread.get("references", [])
    ids = [parent, *references] if isinstance(references, list) else []
    if not ids or len(ids) > 33 or any(
        not isinstance(value, str) or len(value) > 250 or message_ids(value) != [value]
        for value in ids
    ):
        raise ValueError("Özgün e-postanın zincir bilgisi geçersiz. Gelen mailleri yenile.")
    chain = [value for value in dict.fromkeys(references) if value != parent]
    return {"In-Reply-To": parent, "References": " ".join([*chain, parent])}


def smtp_settings():
    values = {}
    path = Path(__file__).with_name(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip().startswith("NETLE_SMTP_"):
                values[key.strip()] = value.strip().strip("\"'")
    return {key: os.environ.get("NETLE_SMTP_" + key, values.get("NETLE_SMTP_" + key, default))
            for key, default in {"HOST": "", "PORT": "587", "USER": "", "PASSWORD": "",
                                 "FROM": "", "TLS": "starttls"}.items()}


def mailbox(value):
    try:
        address = Address(addr_spec=value.strip())
        if not address.username or not address.domain:
            raise ValueError
        return address
    except (HeaderParseError, ValueError):
        raise ValueError("Geçerli, tek bir e-posta adresi gir.") from None


def send_reply(db, employee_id, payload):
    session_id = payload.get("session_id", "")
    if not re.fullmatch(r"[0-9a-f]{32}", session_id) or not db.execute(
        "SELECT 1 FROM employee_refunds WHERE refund_id = ? AND employee_id = ?",
        (session_id, employee_id),
    ).fetchone():
        raise ValueError("Bu hesaba ait iade oturumu bulunamadı.")
    recipient = mailbox(payload.get("recipient", ""))
    if recipient.domain.lower().endswith((".test", ".invalid", ".example", ".localhost")):
        raise ValueError("Örnek adres yerine teslim alabileceğin gerçek bir alıcı adresi gir.")
    subject = payload.get("subject", "").strip()
    body = payload.get("body", "").strip()
    if not subject or len(subject) > 250 or len(subject.splitlines()) != 1 or not 0 < len(body) <= 12000:
        raise ValueError("Tek satırlık bir konu ve 1–12000 karakterlik bir mesaj gir.")

    db.execute("CREATE TABLE IF NOT EXISTS sent_replies (refund_id TEXT PRIMARY KEY, sent INTEGER NOT NULL)")
    previous = db.execute("SELECT sent FROM sent_replies WHERE refund_id = ?", (session_id,)).fetchone()
    if previous:
        if previous[0]:
            return {"sent": True}
        raise ValueError("Önceki gönderimin sonucu belirsiz. Yeniden göndermeden önce posta sunucusunu kontrol et.")

    settings = smtp_settings()
    if not settings["HOST"] or not settings["FROM"]:
        raise ValueError("Gönderim için .env dosyasında NETLE_SMTP_HOST ve NETLE_SMTP_FROM ayarlanmalı.")
    if settings["TLS"] not in ("ssl", "starttls"):
        raise ValueError("NETLE_SMTP_TLS değeri ssl veya starttls olmalı.")
    message = EmailMessage()
    message["From"] = mailbox(settings["FROM"])
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=message["From"].addresses[0].domain)
    thread = payload.get("thread")
    if thread is not None:
        if not isinstance(thread, dict):
            raise ValueError("Geçersiz e-posta zinciri.")
        for key, value in reply_headers(thread).items():
            message[key] = value
    message.set_content(body)

    context = ssl.create_default_context()
    server = None
    try:
        host, port = settings["HOST"], int(settings["PORT"])
        if settings["TLS"] == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls(context=context)
        if settings["USER"]:
            server.login(settings["USER"], settings["PASSWORD"])
        with db:
            db.execute("INSERT INTO sent_replies VALUES (?, 0)", (session_id,))
        try:
            server.send_message(message)
        except (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused):
            with db:
                db.execute("DELETE FROM sent_replies WHERE refund_id = ?", (session_id,))
            raise ValueError("Posta sunucusu gönderimi reddetti. Alıcıyı ve posta ayarlarını kontrol et.") from None
        except OSError:
            raise ValueError("Gönderim sonucu belirsiz. Posta sunucusunu kontrol et.") from None
        with db:
            db.execute("UPDATE sent_replies SET sent = 1 WHERE refund_id = ?", (session_id,))
        return {"sent": True}
    except OSError:
        raise ValueError("Posta sunucusuna bağlanılamadı veya oturum açılamadı. SMTP ayarlarını kontrol et.") from None
    finally:
        if server is not None:
            server.close()
