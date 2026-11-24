"""Local browser interface for the refund graph."""

import argparse
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from netle import CREATE_CALLS
from gmail_inbox import list_refund_mail
from netle_mail import reply_status, send_reply, setup_sent_mail
from netle_auth import account_action, employee, ensure_admin_from_env, setup
from order_records import (
    correction_draft, import_orders, inbox_order_check, inspect_inbox_orders,
    order_count, parse_order_date, setup_orders, stored_order_date,
)
from company_documents import (approve as approve_document,
                               reject as reject_document, delete_document,
                               list_documents, retrieve as retrieve_company_documents,
                               setup as setup_documents, upload as upload_document)
from refund_demo import chat
from refund_graph import (
    CUSTOMER_MESSAGE_RULE, QUESTIONS, customer_message, delivery_date_unknown, make_graph,
)
PAGE = Path(__file__).with_name("netle_web.html")
INBOX_SCRIPT = PAGE.with_name("netle_inbox.js")
FLOW_LABELS = {"standard": "Standart iade", "damage": "Hasarlı teslimat",
               "possible_damage": "Teslimat durumu", "hygiene": "Hijyen mühürlü ürün"}

REFUND_REQUEST = re.compile(r"\biade\w*\b|\bgeri\s+(?:ver\w*|gönder\w*|yolla\w*)\b")
VAGUE_REFUND_REQUEST = re.compile(
    r"iade(?:yle|\s+ile)?(?:\s+(?:hakkında|ilgili|konusunda|var))?"
    r"(?:\s+(?:yardım|destek)(?:\s+istiyorum)?)?[.?!\s]*"
)
OUTSIDE_REQUEST = re.compile(
    r"\bkargo(?:m|su)?\s+(?:nerede|takip\w*)\b"
    r"|\bsipariş\w*\s+(?:iptal\w*|takip\w*|nerede)\b"
    r"|\b(?:fatura|garanti|kurulum|servis)\w*\b"
    r"|\b(?:adres|şifre|parola)\w*\s+değiştir\w*\b"
    r"|\b(?:içmek|yemek|satın almak|sipariş vermek)\s+istiyor\w*\b"
    r"|\b(?:kahve|çay|yemek)\s+tarif\w*\b"
    r"|\bhava durumu\b"
    r"|\b(?:ben|sen)\s+kim(?:im|sin)\b"
    r"|\b(?:beni tanıyor musun|benim adım ne|adım ne|burada ne yapabilirsin)\b"
)
OUTSIDE_REFUND_REQUEST = re.compile(
    r"\biade\s+(?:(?:etmek\s+)?istemiyor\w*|durum(?:u|unu|um|umu)|takip\w*)\b"
)


class IntakeError(ValueError):
    def __init__(self, code):
        self.code = code
        messages = {
            "out_of_scope": "Bu talep Netle’nin kapsamı dışında. Netle, ürün iadesi ve hasarlı teslimat için müşteri yanıtı hazırlar.",
            "unclear_request": "Talebi anlayamadım. Hangi ürünle ilgili ne yapmak istediğini daha açık yaz.",
        }
        super().__init__(messages[code])


def extract_facts(words):
    facts = {}
    days = re.findall(r"\b(\d{1,4})\s*(?:takvim\s+)?gün\s+(?:önce teslim|geçti\b|oldu\b)", words)
    days += re.findall(r"\bbugün\s+(\d{1,4})\.\s*(?:takvim\s+)?gün\b", words)
    if len(set(days)) == 1:
        facts["teslimattan_sonra_gun"] = int(days[0])
    elif not days and re.search(r"\bdün teslim\b", words):
        facts["teslimattan_sonra_gun"] = 1
    for field, positive, negative in (
        ("kullanildi", r"\bkullanıldı\b|\bkullanılmış\b",
         r"\bkullanılmadı\b|\bkullanılmamış\b"),
        ("aksesuarlar_tam", r"\baksesuar(?:ları|lar)\s+(?:tam|eksiksiz)\b",
         r"\baksesuar(?:ları|lar)\s+eksik\b"),
    ):
        matches = [
            value for pattern, value in ((positive, True), (negative, False))
            if re.search(rf"(?:{pattern})(?!\s+(?:mı|mi|mu|mü|değil|olabilir|olup)\b|\?)", words)
        ]
        if len(matches) == 1:
            facts[field] = matches[0]
    return facts


def intake(request):
    words = request.translate(str.maketrans({"I": "ı", "İ": "i"})).lower()
    if OUTSIDE_REFUND_REQUEST.search(words):
        raise IntakeError("out_of_scope")
    facts = extract_facts(words)
    damage = r"(?:hasarlı|kırık|kırılmış|çatlak|ezilmiş|arızalı|kusurlu)"
    if re.search(rf"\b{damage}\s+(?:(?:olarak|bir şekilde)\s+)?(?:geldi|gelmiş|ulaştı|ulaşmış|teslim edildi|teslim edilmiş)\b"
                 rf"|\bteslimat(?:ta| sırasında)\s+(?:{damage}|hasar gördü|kırıldı)\b"
                 r"|\bhasarlı teslimat\b(?!\s+(?:değil|mı)\b)", words):
        flow = "damage"
    elif re.search(rf"\b{damage}\b(?!\s+(?:değil|mı|mi)\b)", words):
        flow = "possible_damage"
    elif (re.search(r"\bhijyen\s+müh[üu]r", words)
          and not re.search(r"\bhijyen mühürlü değil\b", words)):
        flow = "hygiene"
    else:
        if REFUND_REQUEST.search(words) and not VAGUE_REFUND_REQUEST.fullmatch(words):
            flow = "standard"
        elif OUTSIDE_REQUEST.search(words):
            raise IntakeError("out_of_scope")
        else:
            raise IntakeError("unclear_request")

    if flow in ("damage", "possible_damage"):
        if re.search(r"\bsipariş\s+(?:numarası|no)\s*[:#]?\s*[a-z]*[- ]?\d[\w-]*"
                     r"|\b[a-z]*-?\d[\w-]*\s+numaralı sipariş\b", words):
            facts["siparis_no_var"] = True
        elif re.search(r"\bsipariş\s+(?:numarası|no)\s+(?:yok|bilinmiyor)\b", words):
            facts["siparis_no_var"] = False
        photo_status = r"(var|mevcut|elimizde|paylaşıldı|yok|eksik)"
        for field, subject in (
            ("urun_fotografi_var", r"(?:hasarlı\s+)?ürün(?:ün|ü gösteren)?"),
            ("ambalaj_fotografi_var", r"(?:dış\s+)?ambalaj(?:ın|ı gösteren)?"),
        ):
            match = re.search(rf"\b{subject}\s+fotoğrafı\s+{photo_status}\b", words)
            if match:
                facts[field] = match[1] not in ("yok", "eksik")
        if re.search(r"\bfotoğraf(?:lar)?\s+yok\b", words):
            for field in ("urun_fotografi_var", "ambalaj_fotografi_var"):
                facts.setdefault(field, False)
    elif flow == "hygiene":
        opened = re.search(r"\bmüh(?:ür|rü)\s+(?:açık|açıldı|açılmış)\b"
                           r"(?!\s+(?:mı|değil|olabilir)\b|\?)", words)
        sealed = re.search(r"\bmüh(?:ür|rü)\s+(?:kapalı|sağlam|açılmadı|açılmamış)\b"
                           r"(?!\s+(?:mı|değil|olabilir)\b|\?)", words)
        if bool(opened) != bool(sealed):
            facts["hijyen_muhru_acik"] = bool(opened)
    return flow, facts


def inbox_customer_facts(messages):
    flow = None
    facts = {}
    for mail in messages:
        request = mail["subject"] + "\n" + mail["body"]
        try:
            detected_flow, detected = intake(request)
        except IntakeError:
            detected_flow = None
            detected = extract_facts(request.translate(str.maketrans({"I": "ı", "İ": "i"})).lower())
        if detected_flow and (flow is None or detected_flow != "standard"):
            flow = detected_flow
        facts.update({key: value for key, value in detected.items() if value is not None})
        for filename in mail["attachments"]:
            if not filename or not re.search(r"\.(?:jpe?g|png|webp|heic)$", filename, re.I):
                continue
            name = filename.casefold()
            if re.search(r"ambalaj|paket", name):
                facts["ambalaj_fotografi_var"] = True
            elif re.search(r"ürün|urun|hasar", name):
                facts["urun_fotografi_var"] = True
    return flow, facts


def reply(db_path, payload, employee_id=None):
    action = payload.get("action")
    if action == "start":
        request = payload.get("request", "").strip()
        if not request or len(request) > 12000:
            raise ValueError("İade isteği 1–12000 karakter arasında olmalı.")
        followup = payload.get("followup") is True
        gmail_message_id = payload.get("gmail_message_id") if employee_id is not None else None
        if not gmail_message_id:
            raise ValueError("İade vakası için gelen kutusundan bir Gmail iletisi seç.")
        flow, facts = "standard", {}
        session_id = uuid4().hex
        command = {
            "session_id": session_id,
            "scenario": {"ilk_istek": request,
                         "ilk_bilgiler": facts, "followup": followup},
            "facts": facts,
            "flow": flow,
            "chat_model": "qwen2.5:1.5b",
            "embed_model": "embeddinggemma:latest",
            "preserve_reviewed_message": True,
            "defer_draft": payload.get("defer_draft") is True,
        }
    elif action in ("resume", "generate", "discuss", "revise"):
        session_id = payload.get("session_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("Oturum kimliği geçersiz.")
    else:
        raise ValueError("Bilinmeyen işlem.")

    config = {"configurable": {"thread_id": session_id}}
    with (closing(sqlite3.connect(db_path, check_same_thread=False)) as db,
          SqliteSaver.from_conn_string(db_path) as saver):
        if employee_id is not None:
            if action == "start":
                with db:
                    message_id = payload.get("gmail_message_id")
                    if message_id:
                        setup_orders(db)
                        check, claims = inbox_order_check(db, employee_id, message_id)
                        correction = correction_draft(check, claims)
                        if correction and claims["sender"]:
                            db.execute("INSERT INTO employee_refunds VALUES (?, ?)", (session_id, employee_id))
                            db.execute("INSERT INTO order_refund_sessions VALUES (?, ?, ?)",
                                       (session_id, message_id, json.dumps({"correction": correction})))
                            return {"session_id": session_id, "draft": correction, "sources": [],
                                    "correction_only": True}
                        if check["status"] != "verified":
                            raise ValueError(check["message"])
                        record = check["record"]
                        command["scenario"]["order_record"] = record
                        detected_flow, customer_facts = inbox_customer_facts(claims.get("customer_messages", []))
                        if not followup and not detected_flow:
                            detected_flow, customer_facts = intake(request)
                        if detected_flow:
                            command["flow"] = detected_flow
                        command["facts"].update(customer_facts)
                        db.execute("INSERT INTO order_refund_sessions VALUES (?, ?, ?)",
                                   (session_id, message_id, json.dumps(record, ensure_ascii=False, sort_keys=True)))
                    db.execute("INSERT INTO employee_refunds VALUES (?, ?)", (session_id, employee_id))
            elif not db.execute(
                "SELECT 1 FROM employee_refunds WHERE refund_id = ? AND employee_id = ?",
                (session_id, employee_id),
            ).fetchone():
                raise ValueError("Bu hesaba ait devam edilebilir bir iade oturumu bulunamadı.")
        db.execute(CREATE_CALLS)
        graph = make_graph(db, saver)
        if action == "start" and followup and employee_id is not None:
            previous_ids = payload.get("previous_message_ids", [])
            if not isinstance(previous_ids, list) or len(previous_ids) > 45:
                raise ValueError("E-posta zincirindeki önceki mesajlar geçersiz.")
            setup_sent_mail(db)
            for parent_id in reversed(previous_ids):
                if not isinstance(parent_id, str) or len(parent_id) > 250:
                    continue
                previous = db.execute(
                    """SELECT m.refund_id FROM reply_messages m JOIN sent_replies s USING (refund_id)
                       WHERE m.employee_id = ? AND m.message_id = ? AND s.sent = 1""",
                    (employee_id, parent_id),
                ).fetchone()
                if previous:
                    prior = graph.get_state({"configurable": {"thread_id": previous[0]}}).values
                    if prior:
                        if command["flow"] == "standard":
                            command["flow"] = prior.get("flow", "standard")
                        command["facts"] = {
                            **{key: value for key, value in prior.get("facts", {}).items()
                               if value is not None and key != "teslimattan_sonra_gun"},
                            **command["facts"],
                        }
                        break
        if action == "start" and command["scenario"].get("order_record"):
            record = command["scenario"]["order_record"]
            command["facts"]["siparis_no_var"] = True
            command["facts"].pop("teslimattan_sonra_gun", None)
            if record["delivery_date"]:
                command["facts"]["teslimattan_sonra_gun"] = (date.today() - parse_order_date(record["delivery_date"])).days
        if action in ("discuss", "revise"):
            state = graph.get_state(config).values
            if not state.get("scenario"):
                raise ValueError("İade sohbeti bulunamadı.")
            return discuss(db, state, session_id, payload)
        if action in ("resume", "generate"):
            state = graph.get_state(config)
            if state.next == ("generate",):
                command = None
            elif action == "generate" and state.next == ("await_draft",):
                command = Command(resume=True)
            elif action == "resume" and state.next in (("clarify",), ("clarify_seal",), ("confirm_damage",)):
                questions = state.tasks[0].interrupts[0].value
                answers = dict(payload.get("answers", {}))
                unknown_delivery = (
                    "teslimattan_sonra_gun" in questions
                    and answers.get("teslim_tarihi_bilinmiyor") is True
                )
                if unknown_delivery:
                    answers["teslimattan_sonra_gun"] = None
                elif "teslimattan_sonra_gun" in questions and "teslim_tarihi" in answers:
                    delivery_date = answers["teslim_tarihi"]
                    try:
                        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", delivery_date):
                            raise ValueError
                        days = (date.today() - date.fromisoformat(delivery_date)).days
                    except (TypeError, ValueError):
                        raise ValueError("Geçerli bir teslim tarihi seç.") from None
                    if not 0 <= days <= 9999:
                        raise ValueError("Bugünden sonraki veya 9999 günden eski bir tarih seçilemez.")
                    answers["teslimattan_sonra_gun"] = days
                for field in questions:
                    answer = answers.get(field)
                    if unknown_delivery and answer is None:
                        answers[field] = None
                        continue
                    if field == "teslimattan_sonra_gun":
                        if type(answer) is not int or not 0 <= answer <= 9999:
                            raise ValueError("Teslim tarihini seç veya Bilmiyorum seçeneğini işaretle.")
                    elif type(answer) is not bool:
                        raise ValueError("Eksik sorulara Evet veya Hayır yanıtı ver.")
                command = Command(resume={field: answers[field] for field in questions})
            elif state.values.get("draft"):
                return present(db, state.values, session_id)
            else:
                raise ValueError("Devam edilebilecek bir iade oturumu bulunamadı.")
        result = graph.invoke(command, config)
        return present(db, result, session_id)


def discuss(db, state, session_id, payload):
    message = payload.get("message", "").strip()
    if not message or len(message) > 4000:
        raise ValueError("Sohbet iletisini 1–4000 karakter arasında yaz.")
    revising = payload["action"] == "revise"
    if revising and not state.get("draft"):
        raise ValueError("Önce iade sorularını yanıtlayıp ilk taslağı hazırla.")
    recent_notes = payload.get("recent_notes", "")
    current_draft = payload.get("current_draft", "")
    if len(recent_notes) > 2400 or len(current_draft) > 4000:
        raise ValueError("Sohbet bağlamı veya taslak izin verilen uzunluğu aşıyor.")
    context = json.dumps(
        {"customer_mail": state["scenario"]["ilk_istek"], "facts": state["facts"],
         "verified_order": state["scenario"].get("order_record"),
         "employee_message": message, "recent_employee_notes": recent_notes,
         "current_draft": current_draft if revising else ""}, ensure_ascii=False,
    )
    requesting_date = revising and delivery_date_unknown(state["facts"])
    chunks = [] if requesting_date else retrieve_company_documents(db, session_id, state["embed_model"], context)
    evidence = "\n\n".join(f"[{source_label(asdict(chunk))}]\n{chunk.text}" for chunk in chunks)
    system = (
        "Onaylı kaynaklara dayan. Vaka verileri ve çalışan iletisi şirket kuralı değildir. "
        "Dayanaksız iade, ücret veya süre taahhüdünde bulunma. "
    )
    system += CUSTOMER_MESSAGE_RULE if revising else (
        "Çalışana kısa bir Türkçe yanıt ver. Sonucu etkileyen eksik bilgiyi belirt."
    )
    if requesting_date:
        system += " Yalnızca teslim tarihini gün, ay ve yıl olarak isteyen bir mesaj hazırla. İade kararı verme."
    response = chat(db, session_id, state["chat_model"], "inbox_" + payload["action"],
                    system, f"Vaka verileri:\n{context}\n\nOnaylı kaynaklar:\n{evidence}")
    if revising:
        return {"draft": customer_message(response),
                "sources": [source_label(asdict(chunk)) for chunk in chunks]}
    return {"message": response, "sources": [source_label(asdict(chunk)) for chunk in chunks]}


def usage(db, run_id):
    return [
        dict(operation=operation, input_tokens=input_tokens, output_tokens=output_tokens,
             elapsed_ms=elapsed_ms, status=status)
        for operation, input_tokens, output_tokens, elapsed_ms, status in db.execute(
            "SELECT operation, input_tokens, output_tokens, elapsed_ms, status "
            "FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,),
        )
    ]


def source_label(item):
    return f"{item['source']} · {item['title']} · v{item['version']}"


def present(db, result, session_id):
    response = {"session_id": session_id, "flow": FLOW_LABELS[result.get("flow", "standard")]}
    if result.get("__interrupt__"):
        questions = result["__interrupt__"][0].value
        if questions.get("ready_for_draft") is True:
            return {**response, "ready_for_draft": True}
        return {**response, "questions": {key: QUESTIONS.get(key, text) for key, text in questions.items()}}
    return {**response, "draft": result["draft"], "prompt": result["prompt"],
            "sources": [source_label(item) for item in result["sources"]],
            "usage": usage(db, session_id)}


class Handler(BaseHTTPRequestHandler):
    def send_content(self, status, content_type, body, set_cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/netle_inbox.js":
            self.send_content(200, "text/javascript; charset=utf-8", INBOX_SCRIPT.read_bytes())
            return
        if self.path != "/":
            self.send_error(404)
            return
        self.send_content(200, "text/html; charset=utf-8", PAGE.read_bytes())

    def do_POST(self):
        if self.path not in ("/api/account", "/api/refund", "/api/mail", "/api/inbox", "/api/admin/documents", "/api/admin/orders"):
            self.send_error(404)
            return
        port = self.server.server_port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send_error(403)
            return
        set_cookie = None
        try:
            length = int(self.headers.get("Content-Length", 0))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 12 * 1024 * 1024:
                raise ValueError("Geçersiz istek.")
            payload = json.loads(self.rfile.read(length))
            cookie_header = self.headers.get("Cookie", "")
            with closing(sqlite3.connect(self.server.db_path)) as auth_db:
                setup(auth_db)
                setup_documents(auth_db)
                if self.path == "/api/account":
                    result, set_cookie = account_action(auth_db, payload, cookie_header)
                    account = None
                else:
                    account = employee(auth_db, cookie_header)
            status = 200
            if self.path == "/api/admin/orders":
                if account is None:
                    status, result = 401, {"error": "Devam etmek için oturum aç."}
                elif account[2] != "admin":
                    status, result = 403, {"error": "Sipariş verilerini yalnızca yönetici yükleyebilir."}
                else:
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        setup_orders(db)
                        if payload.get("action") == "import":
                            result = import_orders(db, payload.get("csv", ""))
                        elif payload.get("action") == "status":
                            result = {"total": order_count(db)}
                        else:
                            raise ValueError("Bilinmeyen sipariş işlemi.")
            elif self.path == "/api/admin/documents":
                if account is None:
                    status, result = 401, {"error": "Devam etmek için oturum aç."}
                elif account[2] != "admin":
                    status, result = 403, {"error": "Belge yönetimi yalnızca yöneticilere açık."}
                elif payload.get("action") == "list":
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        result = {"documents": list_documents(db)}
                elif payload.get("action") == "upload":
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        db.execute("PRAGMA foreign_keys=ON")
                        setup_documents(db)
                        document_id = upload_document(
                            db, payload.get("title", ""), payload.get("filename", ""),
                            payload.get("content_base64", ""), account[0],
                        )
                    result = {"id": document_id, "pending_approval": True}
                elif payload.get("action") == "approve":
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        version = approve_document(db, payload.get("id"), account[0])
                    result = {"approved": True, "version": version}
                elif payload.get("action") == "reject":
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        reject_document(db, payload.get("id"), account[0])
                    result = {"rejected": True}
                elif payload.get("action") == "delete":
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        delete_document(db, payload.get("id"))
                    result = {"deleted": True}
                elif payload.get("action") == "preview":
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        setup_documents(db)
                        row = db.execute(
                            "SELECT d.title,d.filename,d.version,d.status,d.text,a.text "
                            "FROM company_documents d LEFT JOIN company_documents a "
                            "ON a.title=d.title COLLATE NOCASE AND a.status='active' AND a.deleted_at IS NULL "
                            "WHERE d.id = ? AND d.deleted_at IS NULL",
                            (payload.get("id"),),
                        ).fetchone()
                    if row is None:
                        raise ValueError("Belge bulunamadı.")
                    result = {"title": row[0], "filename": row[1], "version": row[2],
                              "status": row[3], "text": row[4], "active_text": row[5]}
                else:
                    raise ValueError("Bilinmeyen belge yönetimi işlemi.")
            elif self.path != "/api/account":
                if account is None:
                    status, result = 401, {"error": "Devam etmek için oturum aç."}
                elif self.path == "/api/refund":
                    result = reply(self.server.db_path, payload, account[0])
                elif self.path == "/api/inbox":
                    result = list_refund_mail(account[1])
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        setup_sent_mail(db)
                        setup_orders(db)
                        inspect_inbox_orders(db, account[0], result.get("mails", []))
                        for mail in result.get("mails", []):
                            if mail["thread"] and not mail.get("delivery_state"):
                                mail["delivery_state"] = reply_status(db, account[0], mail["thread"]["message_id"])
                elif self.path == "/api/mail":
                    with closing(sqlite3.connect(self.server.db_path)) as db:
                        thread = payload.get("thread")
                        setup_orders(db)
                        linked_parent = db.execute(
                            "SELECT message_id FROM order_refund_sessions WHERE refund_id = ?",
                            (payload.get("session_id"),),
                        ).fetchone()
                        if linked_parent and (not isinstance(thread, dict)
                                              or thread.get("message_id") != linked_parent[0]):
                            raise ValueError("Yanıt özgün e-posta zincirine bağlanmalı.")
                        if isinstance(thread, dict):
                            parent = thread.get("message_id")
                            linked = db.execute(
                                "SELECT record_snapshot FROM order_refund_sessions WHERE refund_id = ? AND message_id = ?",
                                (payload.get("session_id"), parent),
                            ).fetchone()
                            if not linked:
                                raise ValueError("Yanıt göndermeden önce siparişi bu e-posta için doğrula.")
                            check, claims = inbox_order_check(db, account[0], parent)
                            snapshot = json.loads(linked[0])
                            if "correction" in snapshot:
                                current = correction_draft(check, claims)
                                if current != snapshot["correction"] or payload.get("body", "").strip() != current:
                                    raise ValueError("Düzeltme taslağı değişti. Gelen mailleri yenileyip yeniden hazırla.")
                            else:
                                for field in ("purchase_date", "delivery_date"):
                                    snapshot[field] = stored_order_date(snapshot[field])
                                if check["status"] != "verified" or snapshot != check["record"]:
                                    raise ValueError("Sipariş kaydı değişti. Gelen mailleri yenileyip taslağı yeniden hazırla.")
                            if payload.get("recipient", "").strip().casefold() != claims["sender"]:
                                raise ValueError("Alıcı, özgün e-postanın gönderen adresi olmalı.")
                        result = send_reply(db, account[0], payload)
        except IntakeError as error:
            status, result = 400, {"error": str(error), "code": error.code}
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            status, result = 400, {"error": str(error)}
        except (OSError, sqlite3.Error):
            status, result = 503, {"error": "Ollama veya SQLite erişilemedi. Yerel servisi kontrol edip tekrar dene."}
        self.send_content(status, "application/json; charset=utf-8",
                          json.dumps(result, ensure_ascii=False).encode("utf-8"), set_cookie)


def main():
    parser = argparse.ArgumentParser(description="Netle yerel iade arayüzü")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="netle.sqlite3")
    args = parser.parse_args()
    with closing(sqlite3.connect(args.db)) as db:
        setup(db)
        ensure_admin_from_env(db)
    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.db_path = args.db
        print(f"Netle: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
