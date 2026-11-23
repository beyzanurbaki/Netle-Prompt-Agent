"""Import order records and verify Gmail claims against the local SQLite database."""

import csv
import io
import json
import re
from datetime import date


COLUMNS = ("order_id", "customer_email", "product", "purchase_date", "delivery_date")
ORDER_ID = re.compile(r"[A-Z0-9][A-Z0-9-]{0,63}", re.ASCII)
ORDER_IN_MAIL = re.compile(
    r"\bsipariş\s*(?:(?:numarası|numaram|numaranız|no\.?|nolu|kodu)\s*[:#]?|#)\s*([\w-]+)"
    r"|\b([\w-]+)\s+numaralı\s+sipariş", re.IGNORECASE,
)
DELIVERY_IN_MAIL = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})[^\S\r\n]*(?:tarihinde[^\S\r\n]+)?teslim\b(?![^\S\r\n]+tarihi\b)"
    r"|\bteslim\s+tarihi\s*[:=]\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})",
    re.IGNORECASE,
)
PURCHASE_IN_MAIL = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})\s*(?:tarihinde\s+)?satın\s+al"
    r"|\bsatın\s+alma\s+tarihi\s*[:=]\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})",
    re.IGNORECASE,
)
PRODUCT_IN_MAIL = re.compile(r"^\s*ürün(?:\s+adı)?\s*:\s*([^\r\n]+)", re.IGNORECASE | re.MULTILINE)


def setup_orders(db):
    db.execute("SAVEPOINT setup_order_products")
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS products (
            products_id INTEGER PRIMARY KEY, products_name TEXT NOT NULL UNIQUE
        )""")
        db.executemany("INSERT OR IGNORE INTO products VALUES (?, ?)", (
            (1, "Seramik Kupa"), (2, "Cam Sürahi"), (3, "Bambu Tepsi"),
        ))
        columns = {column[1] for column in db.execute("PRAGMA table_info(order_records)")}
        if "product" in columns:
            db.execute("ALTER TABLE order_records RENAME TO legacy_order_records")
        db.execute("""CREATE TABLE IF NOT EXISTS order_records (
            order_id TEXT PRIMARY KEY, customer_email TEXT NOT NULL,
            products_id INTEGER NOT NULL REFERENCES products(products_id),
            purchase_date TEXT NOT NULL, delivery_date TEXT NOT NULL
        )""")
        if "product" in columns:
            db.execute("""INSERT INTO products (products_name)
                SELECT DISTINCT product FROM legacy_order_records WHERE 1
                ON CONFLICT(products_name) DO NOTHING""")
            db.execute("""INSERT INTO order_records
                SELECT o.order_id, o.customer_email, p.products_id, o.purchase_date, o.delivery_date
                FROM legacy_order_records AS o JOIN products AS p ON p.products_name = o.product""")
            db.execute("DROP TABLE legacy_order_records")
        for column in ("purchase_date", "delivery_date"):
            db.execute(f"""UPDATE order_records
                SET {column} = substr({column}, 9, 2) || '.' ||
                    substr({column}, 6, 2) || '.' || substr({column}, 1, 4)
                WHERE {column} GLOB '????-??-??'""")
    except Exception:
        db.execute("ROLLBACK TO setup_order_products")
        raise
    finally:
        db.execute("RELEASE setup_order_products")
    db.execute("""CREATE TABLE IF NOT EXISTS order_mail_evidence (
        employee_id INTEGER NOT NULL, message_id TEXT NOT NULL, claims TEXT NOT NULL,
        PRIMARY KEY (employee_id, message_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS order_refund_sessions (
        refund_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, record_snapshot TEXT NOT NULL
    )""")


def parse_order_date(value):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    if re.fullmatch(r"\d{1,2}[./]\d{1,2}[./]\d{4}", value):
        day, month, year = map(int, re.split(r"[./]", value))
        return date(year, month, day)
    raise ValueError("Geçersiz sipariş tarihi.")


def stored_order_date(value):
    return parse_order_date(value).strftime("%d.%m.%Y") if value else ""


def import_orders(db, content):
    if not 0 < len(content) <= 2_000_000:
        raise ValueError("Sipariş CSV dosyası boş olamaz ve en fazla 2 MB olabilir.")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
    try:
        headers = reader.fieldnames
    except csv.Error:
        raise ValueError("CSV başlıkları okunamadı.") from None
    if not headers or len(headers) != len(COLUMNS) or set(headers) != set(COLUMNS):
        raise ValueError("CSV başlıkları şu alanları içermeli: " + ", ".join(COLUMNS))
    rows = {}
    try:
        for line in reader:
            order_id, email, product, purchased, delivered = (line[key].strip() for key in COLUMNS)
            order_id, email = order_id.upper(), email.casefold()
            if (None in line or not ORDER_ID.fullmatch(order_id) or order_id in rows
                    or len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
                    or not product or len(product) > 250
                    or parse_order_date(purchased) > date.today()
                    or (delivered and not parse_order_date(purchased) <= parse_order_date(delivered) <= date.today())):
                raise ValueError
            rows[order_id] = (order_id, email, product, stored_order_date(purchased), stored_order_date(delivered))
    except (ValueError, AttributeError, csv.Error):
        raise ValueError(f"CSV satır {reader.line_num} geçersiz veya yinelenen sipariş numarası içeriyor.") from None
    if not rows:
        raise ValueError("CSV dosyasında sipariş kaydı yok.")
    with db:
        db.executemany("""INSERT INTO products (products_name) VALUES (?)
            ON CONFLICT(products_name) DO NOTHING""", ((row[2],) for row in rows.values()))
        db.executemany("""INSERT INTO order_records
            (order_id, customer_email, products_id, purchase_date, delivery_date)
            VALUES (?, ?, (SELECT products_id FROM products WHERE products_name = ?), ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET customer_email=excluded.customer_email,
                products_id=excluded.products_id, purchase_date=excluded.purchase_date, delivery_date=excluded.delivery_date
        """, rows.values())
    return {"imported": len(rows), "total": order_count(db)}


def order_count(db):
    return db.execute("SELECT COUNT(*) FROM order_records").fetchone()[0]


def normalized(value):
    return " ".join(value.translate(str.maketrans({"I": "ı", "İ": "i"})).lower().split())


def claimed_dates(pattern, text):
    dates = set()
    for match in pattern.finditer(normalized(text)):
        value = match[1] or match[2]
        try:
            dates.add(stored_order_date(value))
        except ValueError:
            continue
    return dates


def mail_claims(mail):
    sender = mail["sender_address"].casefold()
    sources = [item for item in mail.get("messages", [mail]) if item["sender_address"].casefold() == sender]
    ids = set()
    delivery_dates = set()
    purchase_dates = set()
    products = set()
    for item in sources:
        text = item["subject"] + "\n" + item["body"]
        explicit_ids = set()
        for match in ORDER_IN_MAIL.finditer(normalized(text)):
            order_id = (match[1] or match[2]).upper()
            if ORDER_ID.fullmatch(order_id) and any(char.isdigit() for char in order_id):
                explicit_ids.add(order_id)
        ids = explicit_ids or ids
        delivery_dates = claimed_dates(DELIVERY_IN_MAIL, item["body"]) or delivery_dates
        purchase_dates = claimed_dates(PURCHASE_IN_MAIL, item["body"]) or purchase_dates
        products = {normalized(value) for value in PRODUCT_IN_MAIL.findall(item["body"])} or products
    return {"sender": sender, "order_ids": sorted(ids), "delivery_dates": sorted(delivery_dates),
            "purchase_dates": sorted(purchase_dates), "products": sorted(products)}


def verify_order(db, claims):
    if not order_count(db):
        return {"status": "no_data", "message": "Yönetim panelinden sipariş kayıtları yüklenmeli."}
    ids = claims["order_ids"]
    if len(ids) != 1:
        return {"status": "unmatched", "message": "E-posta zincirinde tek bir sipariş numarası bulunamadı."}
    row = db.execute("""SELECT o.order_id, p.products_name, o.purchase_date, o.delivery_date
        FROM order_records AS o JOIN products AS p ON p.products_id = o.products_id
        WHERE o.order_id = ? AND o.customer_email = ?""", (ids[0], claims["sender"])).fetchone()
    if not row:
        return {"status": "unmatched", "message": "Sipariş numarası ve gönderen adresi kayıtlarla eşleşmedi."}
    record = dict(zip(("order_id", "product", "purchase_date", "delivery_date"), row))
    differences = []
    if claims.get("purchase_dates") and {stored_order_date(value) for value in claims["purchase_dates"]} != {stored_order_date(record["purchase_date"])}:
        differences.append("Müşterinin belirttiği satın alma tarihi sipariş kaydından farklı.")
    if claims["delivery_dates"] and record["delivery_date"] and {stored_order_date(value) for value in claims["delivery_dates"]} != {stored_order_date(record["delivery_date"])}:
        differences.append("Müşterinin belirttiği teslim tarihi sipariş kaydından farklı.")
    if claims["products"] and set(claims["products"]) != {normalized(record["product"])}:
        differences.append("Müşterinin belirttiği ürün sipariş kaydındaki ürün adıyla eşleşmiyor.")
    return {"status": "review" if differences else "verified", "record": record,
            "message": " ".join(differences) or "Sipariş numarası ve gönderen adresi kayıtla eşleşti."}


def inspect_inbox_orders(db, employee_id, mails):
    with db:
        for mail in mails:
            if not mail["thread"]:
                continue
            message_id = mail["thread"]["message_id"]
            claims = mail_claims(mail)
            claims["customer_messages"] = [
                {"subject": item["subject"][:300], "body": item["body"][:12000],
                 "attachments": item.get("attachments", [])}
                for item in mail.get("messages", [mail])
                if item["sender_address"].casefold() == claims["sender"]
            ]
            mail["order_check"] = verify_order(db, claims)
            db.execute("""INSERT INTO order_mail_evidence VALUES (?, ?, ?)
                ON CONFLICT(employee_id, message_id) DO UPDATE SET claims=excluded.claims""",
                       (employee_id, message_id, json.dumps(claims, ensure_ascii=False)))


def correction_draft(check, claims):
    if check["status"] == "unmatched":
        detail = (
            "Sipariş numaranızı bu e-postaya yanıt vererek paylaşabilir misiniz?"
            if len(claims["order_ids"]) != 1 else
            "Sipariş numaranızı kontrol edip siparişte kullanılan e-posta adresinden "
            "bize tekrar yazabilir misiniz?"
        )
    elif check["status"] == "review":
        detail = "Paylaştığınız ürün veya tarih bilgilerinde uyuşmazlık var. "
        detail += "Bu bilgileri kontrol edip doğru bilgileri paylaşabilir misiniz?"
    else:
        return None
    return "Merhaba,\n\n" + detail + "\n\nTeşekkür ederiz."


def inbox_order_check(db, employee_id, message_id):
    row = db.execute("SELECT claims FROM order_mail_evidence WHERE employee_id = ? AND message_id = ?",
                     (employee_id, message_id)).fetchone()
    if not row:
        raise ValueError("Sipariş kontrolü için gelen mailleri yenile.")
    claims = json.loads(row[0])
    return verify_order(db, claims), claims


def require_verified_order(db, employee_id, message_id):
    result, claims = inbox_order_check(db, employee_id, message_id)
    if result["status"] != "verified":
        raise ValueError(result["message"])
    return result["record"], claims["sender"]
