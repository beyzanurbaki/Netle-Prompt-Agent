"""Read the Netle İade label from one Gmail account."""

import base64
import imaplib
import os
import re
import ssl
import time
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from netle_mail import message_ids


LABEL = "Netle İade"


class MailText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = None
        self.quote_start = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if self.quote_start is None and (
            "gmail_quote" in classes or "gmail_attr" in classes
            or (tag == "blockquote" and attributes.get("type") == "cite")
        ):
            self.quote_start = len(self.parts)
        if tag in ("style", "script", "head"):
            self.hidden = tag
        if tag in ("br", "p", "div", "li") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden = None

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def split_plain_reply(body):
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith(">"):
            continue
        prefix = "\n".join(lines[:index]).rstrip()
        if not prefix:
            break
        attribution = re.search(
            r"(?im)(?:^|\n)(?:On\s+[^\n]{1,500}\bwrote\s*:"
            r"|[^\n]{1,500}\byazdı\s*:"
            r"|[^\n]*<[^<>\n]+>\s+adresine sahip kullanıcı[^\n]*(?:\n[^\n]{0,150})?)\s*\Z",
            prefix,
        )
        start = len(prefix[:attribution.start()].splitlines()) if attribution else index
        current = "\n".join(lines[:start]).strip()
        if current and all(not line.strip() or line.lstrip().startswith(">") for line in lines[index:]):
            return current, "\n".join(lines[start:]).strip()
        break
    return body.strip(), ""


def mail_body(message):
    plain = message.get_body(preferencelist=("plain",))
    body, quoted = split_plain_reply(plain.get_content()) if plain else ("", "")
    if quoted:
        return body, quoted
    html = message.get_body(preferencelist=("html",))
    if not html:
        return body, ""
    parser = MailText()
    parser.feed(html.get_content())
    if parser.quote_start is not None:
        current = "".join(parser.parts[:parser.quote_start]).strip()
        if current:
            return current, "".join(parser.parts[parser.quote_start:]).strip()
    return body or "".join(parser.parts).strip(), ""


def imap_label(label):
    def encode(match):
        encoded = base64.b64encode(match[0].encode("utf-16-be")).decode("ascii")
        return "&" + encoded.rstrip("=").replace("/", ",") + "-"
    return re.sub(r"[^\x20-\x7e]+", encode, label.replace("&", "&-"))


def gmail_settings():
    values = {}
    path = Path(__file__).with_name(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip().startswith("NETLE_GMAIL_"):
                values[key.strip()] = value.strip().strip("\"'")
    return [os.environ.get(key, values.get(key, ""))
            for key in ("NETLE_GMAIL_ACCOUNT", "NETLE_GMAIL_APP_PASSWORD", "NETLE_GMAIL_EMPLOYEE")]


def parse_mail(raw, identifier):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    sender = message["From"]
    senders = getattr(sender, "addresses", ())
    reply_to = message["Reply-To"] or sender
    addresses = getattr(reply_to, "addresses", ())
    body, quoted = mail_body(message)
    parent = message_ids(message["Message-ID"])
    references = message_ids(message["References"])
    if not references:
        previous = message_ids(message["In-Reply-To"])
        references = previous if len(previous) == 1 else []
    thread = {"message_id": parent[0], "references": references[-32:]} if len(parent) == 1 else None
    return {"id": identifier, "sender": str(sender or ""), "subject": str(message["Subject"] or "Konusuz"),
            "sender_address": senders[0].addr_spec if len(senders) == 1 else "",
            "recipient": addresses[0].addr_spec if len(addresses) == 1 else "",
            "thread": thread,
            "body": body[:12000], "quoted_body": quoted[:12000], "attachments": [
                part.get_filename() for part in message.iter_attachments() if part.get_filename()
            ]}


def mark_sent_replies(inbox, items, thread_ids):
    if not thread_ids:
        return
    try:
        status, folders = inbox.list()
        if status != "OK":
            return
        sent = next((folder.split(b'"', 2)[-1].strip() for folder in folders
                     if isinstance(folder, bytes) and b"\\Sent" in folder.split(b")", 1)[0].lstrip(b"(").split()), None)
        if sent is None or inbox.select(sent.decode("ascii"), readonly=True)[0] != "OK":
            return
        status, uids = inbox.uid("SEARCH", None, thread_search(thread_ids))
        if status != "OK" or not uids[0]:
            return
        status, parts = inbox.uid("FETCH", b",".join(uids[0].split()[-100:]),
                                  "(UID BODY.PEEK[HEADER.FIELDS (IN-REPLY-TO)])")
        if status != "OK":
            return
        incoming = {}
        for mail in items:
            if mail["thread"]:
                incoming.setdefault(mail["thread"]["message_id"], []).append(mail)
        sent_ids = {}
        for part in parts:
            if not isinstance(part, tuple):
                continue
            metadata, header = part
            uid = re.search(rb"\bUID (\d+)\b", metadata)
            for parent in message_ids(BytesParser(policy=policy.default).parsebytes(header)["In-Reply-To"]):
                for mail in incoming.get(parent, []):
                    mail["delivery_state"] = "sent"
                if uid and parent in incoming:
                    sent_ids[parent] = uid[1]
        for parent, uid in sent_ids.items():
            sent_message = read_mail(inbox, uid, "sent")
            if sent_message and sent_message["body"]:
                for mail in incoming[parent]:
                    mail["sent_reply"] = sent_message["body"]
    except (imaplib.IMAP4.error, UnicodeError):
        return


def thread_search(thread_ids):
    criteria = ["X-GM-THRID " + thread_id for thread_id in sorted(thread_ids)]
    return "OR " * (len(criteria) - 1) + " ".join(criteria)


def read_mail(inbox, uid, validity):
    status, parts = inbox.uid("FETCH", uid, "(X-GM-THRID INTERNALDATE BODY.PEEK[]<0.262144>)")
    if status != "OK":
        return None
    message_part = next((part for part in parts if isinstance(part, tuple)), None)
    if not message_part:
        return None
    metadata = b" ".join(part[0] if isinstance(part, tuple) else part for part in parts if part)
    mail = parse_mail(message_part[1], f"gmail-{validity}-{uid.decode()}")
    thread = re.search(rb"\bX-GM-THRID (\d+)\b", metadata)
    internal_date = imaplib.Internaldate2tuple(metadata)
    mail["conversation_id"] = "gmail-thread-" + thread[1].decode() if thread else mail["id"]
    mail["arrived"] = time.mktime(internal_date) if internal_date else 0
    return mail


def group_conversations(items):
    conversations = {}
    seen = set()
    for mail in sorted(items, key=lambda item: item["arrived"]):
        identity = (mail["thread"] or {}).get("message_id", mail["id"])
        if identity in seen:
            continue
        seen.add(identity)
        conversations.setdefault(mail["conversation_id"], []).append(mail)
    return [
        {**messages[-1], "id": key, "messages": messages,
         "followup": len(messages) > 1}
        for key, messages in reversed(sorted(conversations.items(), key=lambda entry: entry[1][-1]["arrived"]))
    ]


def list_refund_mail(username):
    address, app_password, employee = gmail_settings()
    if not employee or username != employee.lower().strip():
        return {"enabled": False}
    if not address:
        raise ValueError(".env dosyasında NETLE_GMAIL_ACCOUNT alanına Gmail adresini yaz.")
    app_password = "".join(app_password.split())
    if len(app_password) != 16:
        raise ValueError(
            ".env dosyasındaki NETLE_GMAIL_APP_PASSWORD alanına Google’ın oluşturduğu "
            "16 karakterlik uygulama şifresini yaz."
        )
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=12) as inbox:
            inbox.login(address, app_password)
            status, _ = inbox.select(f'"{imap_label(LABEL)}"', readonly=True)
            if status != "OK":
                raise ValueError("Gmail’de Netle İade etiketini kontrol et.")
            _, validity = inbox.response("UIDVALIDITY")
            if not validity or not validity[0]:
                raise ValueError("Gmail etiketinin kimliği alınamadı.")
            status, numbers = inbox.uid("SEARCH", None, "ALL")
            if status != "OK":
                raise ValueError("Gmail etiketi okunamadı.")
            items = [mail for uid in numbers[0].split()[-15:]
                     if (mail := read_mail(inbox, uid, validity[0].decode()))
                     and mail["sender_address"].casefold() != address.casefold()]
            thread_ids = {mail["conversation_id"].removeprefix("gmail-thread-") for mail in items
                          if mail["conversation_id"].startswith("gmail-thread-")}
            if thread_ids and inbox.select("INBOX", readonly=True)[0] == "OK":
                status, uids = inbox.uid("SEARCH", None, thread_search(thread_ids))
                if status == "OK" and uids[0]:
                    for uid in uids[0].split()[-30:]:
                        mail = read_mail(inbox, uid, "inbox")
                        if mail and mail["sender_address"].casefold() != address.casefold():
                            items.append(mail)
            mark_sent_replies(inbox, items, thread_ids)
            return {"enabled": True, "mails": group_conversations(items)}
    except (OSError, imaplib.IMAP4.error):
        raise ValueError("Gmail bağlantısı kurulamadı. Hesabı, uygulama şifresini ve Netle İade etiketini kontrol et.") from None
