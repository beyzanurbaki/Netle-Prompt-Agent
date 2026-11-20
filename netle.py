"""Run a local refund example and record each Ollama call in SQLite."""

import argparse
import json
import sqlite3
from contextlib import closing
from time import perf_counter
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


REFUND_REQUEST = (
    "Müşteri dün teslim aldığı ürünü iade etmek istiyor. "
    "Müşteriye gönderebileceğim kısa bir Türkçe yanıt taslağı hazırla."
)

CREATE_CALLS = """
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    elapsed_ms REAL NOT NULL,
    ollama_ms REAL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def call_ollama(db, run_id, endpoint, payload, *, operation=None, timeout=120):
    request = Request(
        f"http://localhost:11434/api/{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = perf_counter()
    response = {}
    status = "error"
    try:
        with urlopen(request, timeout=timeout) as stream:
            body = json.load(stream)
        if not isinstance(body, dict):
            raise ValueError("Ollama geçersiz bir yanıt döndürdü.")
        response = body
        if "error" in response:
            raise ValueError(response["error"])
        if endpoint == "chat":
            if not response["message"]["content"].strip():
                raise ValueError("Ollama boş bir taslak döndürdü.")
        elif not response["embeddings"][0]:
            raise ValueError("Ollama boş bir embedding döndürdü.")
        status = "ok"
        return response
    finally:
        elapsed_ms = (perf_counter() - started) * 1000
        duration = response.get("total_duration")
        with db:
            db.execute(
                """
                INSERT INTO model_calls (
                    run_id, operation, model, input_tokens, output_tokens,
                    elapsed_ms, ollama_ms, attempt, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, operation or endpoint, response.get("model", payload["model"]),
                    response.get("prompt_eval_count"),
                    response.get("eval_count") if endpoint == "chat" else None,
                    elapsed_ms, duration / 1_000_000 if duration is not None else None,
                    1, status,
                ),
            )


def main():
    parser = argparse.ArgumentParser(description="Netle iade isteği örneği")
    parser.add_argument("--chat-model", required=True)
    parser.add_argument("--embed-model", required=True)
    parser.add_argument("--db", default="netle.sqlite3")
    args = parser.parse_args()
    run_id = uuid4().hex
    print(f"Çalıştırma kimliği: {run_id}", flush=True)

    with closing(sqlite3.connect(args.db)) as db:
        db.execute(CREATE_CALLS)
        try:
            chat = call_ollama(db, run_id, "chat", {
                "model": args.chat_model,
                "messages": [
                    {"role": "system", "content": "Türkçe yaz. Şirket politikası verilmediyse koşul veya süre uydurma."},
                    {"role": "user", "content": REFUND_REQUEST},
                ],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 180},
            })
            embedding = call_ollama(db, run_id, "embed", {
                "model": args.embed_model, "input": REFUND_REQUEST, "truncate": False,
            })
        except (URLError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
            parser.exit(1, f"Ollama çağrısı başarısız: {error}\n")
        print(chat["message"]["content"])
        print(f"Embedding boyutu: {len(embedding['embeddings'][0])}")
        for row in db.execute(
            """
            SELECT operation, input_tokens, output_tokens, round(elapsed_ms, 1)
            FROM model_calls WHERE run_id = ? ORDER BY id
            """,
            (run_id,),
        ):
            print(f"{row[0]}: girdi={row[1]}, çıktı={row[2]}, süre={row[3]} ms")


if __name__ == "__main__":
    main()
