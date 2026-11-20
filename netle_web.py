"""Local browser interface for the standard refund graph."""

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
from refund_graph import make_graph


PAGE = Path(__file__).with_name("netle_web.html")


def extract_facts(request):
    words = request.translate(str.maketrans({"I": "ı", "İ": "i"})).lower()
    if re.search(r"hasar|kırık|kırılmış|çatlak|hijyen|mühür|arızalı|kusurlu", words):
        raise ValueError("Bu ekran standart iadeler için. Hasar, arıza ve mühürlü ürünleri ayrı inceleyin.")
    facts = {}
    days = re.findall(r"\b(\d{1,4})\s*(?:takvim\s+)?gün\s+(?:önce teslim|geçti\b|oldu\b)", words)
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


def reply(db_path, payload):
    action = payload.get("action")
    if action == "start":
        request = payload.get("request", "").strip()
        if not request or len(request) > 2000:
            raise ValueError("İade isteğini 1–2000 karakter arasında yaz.")
        facts = extract_facts(request)
        session_id = uuid4().hex
        command = {
            "session_id": session_id,
            "scenario": {"ilk_istek": f"Standart iade koşulları ve başvuru adımı. {request}",
                         "ilk_bilgiler": facts},
            "facts": facts,
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
            elif state.next == ("clarify",):
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
                return present(state.values, session_id)
            else:
                raise ValueError("Devam edilebilecek bir iade oturumu bulunamadı.")
        result = graph.invoke(command, config)
    return present(result, session_id)


def present(result, session_id):
    response = {"session_id": session_id}
    if result.get("__interrupt__"):
        return {**response, "questions": result["__interrupt__"][0].value}
    return {**response, "draft": result["draft"], "prompt": result["prompt"],
            "sources": [f"{item['source']} · {item['title']} · v{item['version']}"
                        for item in result["sources"]]}


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
        if self.path != "/api/refund":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 16000:
                raise ValueError("Geçersiz istek.")
            result = reply(self.server.db_path, json.loads(self.rfile.read(length)))
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
