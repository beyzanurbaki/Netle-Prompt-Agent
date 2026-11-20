"""Local browser interface for the refund graph."""

import argparse
import json
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from netle import CREATE_CALLS
from refund_demo import chat
from refund_graph import CUSTOMER_MESSAGE_RULE, build_prompt, make_graph
from refund_template import CREATE_TEMPLATES, NAME, TEMPLATE, approve_template, current_sources, proposal, render


PAGE = Path(__file__).with_name("netle_web.html")
FLOW_LABELS = {"standard": "Standart iade", "damage": "Hasarlı teslimat",
               "possible_damage": "Teslimat durumu", "hygiene": "Hijyen mühürlü ürün"}


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
    facts = extract_facts(words)
    damage = r"(?:hasarlı|kırık|kırılmış|çatlak|ezilmiş|arızalı|kusurlu)"
    if re.search(rf"\b{damage}\s+(?:(?:olarak|bir şekilde)\s+)?(?:geldi|ulaştı|teslim edildi)\b"
                 rf"|\bteslimat(?:ta| sırasında)\s+(?:{damage}|hasar gördü|kırıldı)\b"
                 r"|\bhasarlı teslimat\b(?!\s+(?:değil|mı)\b)", words):
        flow = "damage"
    elif re.search(rf"\b{damage}\b(?!\s+(?:değil|mı|mi)\b)", words):
        flow = "possible_damage"
    elif (re.search(r"\bhijyen\s+müh[üu]r", words)
          and not re.search(r"\bhijyen mühürlü değil\b", words)):
        flow = "hygiene"
    else:
        flow = "standard"

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


def reply(db_path, payload):
    action = payload.get("action")
    if action == "start":
        request = payload.get("request", "").strip()
        if not request or len(request) > 2000:
            raise ValueError("İade isteğini 1–2000 karakter arasında yaz.")
        flow, facts = intake(request)
        session_id = uuid4().hex
        command = {
            "session_id": session_id,
            "scenario": {"ilk_istek": request,
                         "ilk_bilgiler": facts},
            "facts": facts,
            "flow": flow,
            "chat_model": "qwen2.5:1.5b",
            "embed_model": "embeddinggemma:latest",
            "preserve_reviewed_message": True,
        }
    elif action == "resume":
        session_id = payload.get("session_id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("Oturum kimliği geçersiz.")
    else:
        raise ValueError("Bilinmeyen işlem.")

    config = {"configurable": {"thread_id": session_id}}
    with (closing(sqlite3.connect(db_path, check_same_thread=False)) as db,
          SqliteSaver.from_conn_string(db_path) as saver):
        db.execute(CREATE_CALLS)
        graph = make_graph(db, saver)
        if action == "resume":
            state = graph.get_state(config)
            if state.next == ("generate",):
                command = None
            elif state.next in (("clarify",), ("clarify_seal",), ("confirm_damage",)):
                questions = state.tasks[0].interrupts[0].value
                answers = payload.get("answers", {})
                for field in questions:
                    answer = answers.get(field)
                    if field == "teslimattan_sonra_gun":
                        if type(answer) is not int or not 0 <= answer <= 9999:
                            raise ValueError("Gün sayısı 0–9999 arasında bir tam sayı olmalı.")
                    elif type(answer) is not bool:
                        raise ValueError("Eksik sorulara Evet veya Hayır yanıtı ver.")
                command = Command(resume={field: answers[field] for field in questions})
            elif state.values.get("draft"):
                return present(db, state.values, session_id)
            else:
                raise ValueError("Devam edilebilecek bir iade oturumu bulunamadı.")
        result = graph.invoke(command, config)
        return present(db, result, session_id)


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
        return {**response, "questions": result["__interrupt__"][0].value}
    return {**response, "draft": result["draft"], "prompt": result["prompt"],
            "sources": [source_label(item) for item in result["sources"]],
            "usage": usage(db, session_id)}


def template_action(db_path, payload):
    action = payload.get("action")
    if action == "preview":
        approval_id, sources_json = proposal()
        return {"approval_id": approval_id, "name": NAME, "prompt": TEMPLATE,
                "sources": [source_label(item) for item in json.loads(sources_json)]}
    with closing(sqlite3.connect(db_path)) as db:
        db.execute(CREATE_TEMPLATES)
        if action == "list":
            try:
                sources = current_sources()
            except (ValueError, OSError):
                sources = None
            return {"templates": [
                {"approval_id": key, "name": name, "approved_at": approved_at,
                 "available": json.loads(stored) == sources}
                for key, name, approved_at, stored in db.execute(
                    "SELECT approval_id, name, approved_at, sources_json FROM prompt_templates "
                    "ORDER BY approved_at DESC, approval_id"
                )
            ]}
        if action == "approve":
            approve_template(db, payload["approval_id"])
            return {"approved": True}
        if action != "use":
            raise ValueError("Bilinmeyen şablon işlemi.")

        product = payload.get("product", "").strip()
        days = payload.get("days")
        if not product or len(product) > 120 or len(product.splitlines()) != 1:
            raise ValueError("Ürün adı 1–120 karakter ve tek satır olmalı.")
        if days is not None and (type(days) is not int or not 0 <= days <= 9999):
            raise ValueError("Gün sayısı 0–9999 arasında bir tam sayı olmalı.")
        facts = {"urun": product, "teslimattan_sonra_gun": days}
        labels = {True: "Evet", False: "Hayır", None: "Bilinmiyor"}
        fields = ("kullanildi", "aksesuarlar_tam", "kutu_acik")
        for field in fields:
            value = payload.get(field)
            if value is not None and type(value) is not bool:
                raise ValueError("Ürün bilgileri için Evet, Hayır veya Bilinmiyor seç.")
            facts[field] = value
        approval_id = payload["approval_id"]
        prompt = render(db, approval_id, {
            **facts, **{field: labels[facts[field]] for field in fields},
            "teslimattan_sonra_gun": days if days is not None else "Bilinmiyor",
        })
        stored_sources, = db.execute(
            "SELECT sources_json FROM prompt_templates WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        reviewed = build_prompt({"facts": facts})["reviewed_message"]
        run_id = uuid4().hex
        db.execute(CREATE_CALLS)
        draft = chat(
            db, run_id, "qwen2.5:1.5b", "template_generate_draft",
            CUSTOMER_MESSAGE_RULE + " Son satırdaki müşteri mesajını aynen döndür.",
            f"{prompt}\n\nMüşteri mesajı:\n{reviewed}",
        )
        return {"flow": "Onaylı şablon", "draft": draft if draft == reviewed else reviewed,
                "prompt": prompt, "sources": [source_label(item) for item in json.loads(stored_sources)],
                "usage": usage(db, run_id), "session_id": run_id}


class Handler(BaseHTTPRequestHandler):
    def send_content(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        self.send_content(200, "text/html; charset=utf-8", PAGE.read_bytes())

    def do_POST(self):
        actions = {"/api/refund": reply, "/api/templates": template_action}
        action = actions.get(self.path)
        if action is None:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 16000:
                raise ValueError("Geçersiz istek.")
            result = action(self.server.db_path, json.loads(self.rfile.read(length)))
            status = 200
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            status, result = 400, {"error": str(error)}
        except (OSError, sqlite3.Error):
            status, result = 503, {"error": "Ollama veya SQLite erişilemedi. Yerel servisi kontrol edip tekrar dene."}
        self.send_content(status, "application/json; charset=utf-8",
                          json.dumps(result, ensure_ascii=False).encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Netle yerel iade arayüzü")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="netle.sqlite3")
    args = parser.parse_args()
    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.db_path = args.db
        print(f"Netle: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
