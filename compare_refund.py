"""Compare the original request, direct RAG and Netle on the refund fixtures."""

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from uuid import uuid4

from netle import CREATE_CALLS
from rag import DATA_DIR, embed, embed_documents, rank, scenario_query
from refund_demo import chat
from refund_graph import CUSTOMER_MESSAGE_RULE, build_prompt
from source_validation import load_reviewed_sources


CREATE_ANSWERS = """
CREATE TABLE IF NOT EXISTS comparison_answers (
    run_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    method TEXT NOT NULL,
    draft TEXT NOT NULL,
    used_reviewed_message INTEGER NOT NULL,
    sources_json TEXT NOT NULL,
    PRIMARY KEY (run_id, scenario_id, method)
)
"""


def comparison_state(facts):
    if facts.get("iade_nedeni") == "Teslimatta hasar":
        photos = facts.get("fotograflar_mevcut")
        return {"flow": "damage", "facts": {
            **facts, "siparis_no_var": bool(facts.get("siparis_no")),
            "urun_fotografi_var": photos, "ambalaj_fotografi_var": photos,
        }}
    return {"flow": "hygiene" if facts.get("hijyen_muhurlu") else "standard", "facts": facts}


def required_demo_sources(state):
    standard = ("IADE-POL#P1", "DESTEK-REH#R1")
    if state["flow"] == "damage":
        return ("IADE-POL#P3", "DESTEK-REH#R2")
    if state["flow"] == "hygiene":
        hygiene = ("IADE-POL#P2",)
        if state["facts"].get("hijyen_muhru_acik") is False:
            return hygiene + standard
        return hygiene
    return standard


def compare(db, args, run_id, scenario, chunks, vectors):
    scenario_id = scenario["id"]
    call_id = f"{run_id}:{scenario_id}"
    facts = {**scenario["ilk_bilgiler"], **scenario["ek_bilgiler"]}
    state = comparison_state(facts)
    query, = embed(db, call_id, args.embed_model, [scenario_query(scenario, clarified=True)],
                   "compare_embed_query")
    ranked = [chunk for chunk, _ in rank(query, chunks, vectors)]
    required = set(required_demo_sources(state))
    sources = [chunk for chunk in ranked if chunk.source in required and chunk.version == "1.0"]
    if {chunk.source for chunk in sources} != required:
        raise ValueError(f"{scenario_id} için incelenmiş v1.0 kaynakları eksik.")
    source_ids = [chunk.source for chunk in sources]
    context = (
        f"Doğrulanmış bilgiler:\n{json.dumps(facts, ensure_ascii=False)}\n\nŞirket kaynakları:\n"
        + "\n\n".join(f"[{chunk.source} · v{chunk.version}] {chunk.title}\n{chunk.text}" for chunk in sources)
    )
    improved = build_prompt({**state, "scenario": {"ilk_istek": scenario["ilk_istek"]},
                             "sources": [asdict(chunk) for chunk in sources]})
    methods = [
        ("original", scenario["ilk_istek"], []),
        ("direct_rag", f"{scenario['ilk_istek']}\n\n{context}", source_ids),
        ("netle", improved["prompt"], source_ids),
    ]
    print(f"\n{scenario_id} · Ortak kaynaklar: {', '.join(source_ids)}")
    for method, prompt, method_sources in methods:
        operation = f"compare_{method}"
        raw = chat(db, call_id, args.chat_model, operation, CUSTOMER_MESSAGE_RULE, prompt)
        use_reviewed = False
        draft = raw
        with db:
            db.execute(
                "INSERT INTO comparison_answers VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, scenario_id, method, draft, use_reviewed,
                 json.dumps(method_sources, ensure_ascii=False)),
            )
        metrics = db.execute(
            "SELECT input_tokens, output_tokens, elapsed_ms FROM model_calls "
            "WHERE run_id = ? AND operation = ? ORDER BY id DESC LIMIT 1",
            (call_id, operation),
        ).fetchone()
        print(f"\n{method} · {metrics[0]} girdi + {metrics[1]} çıktı · {metrics[2]:.1f} ms"
              f"{' · incelenmiş mesaj' if use_reviewed else ''}\n{draft}")


def main():
    parser = argparse.ArgumentParser(description="İade yanıtlarını karşılaştır")
    cases = parser.add_mutually_exclusive_group()
    cases.add_argument("--all", action="store_true", help="Altı örnek iade senaryosunu çalıştır")
    cases.add_argument("--scenario", default="I01")
    parser.add_argument("--chat-model", default="qwen2.5:1.5b")
    parser.add_argument("--embed-model", default="embeddinggemma:latest")
    parser.add_argument("--db", default="netle.sqlite3")
    args = parser.parse_args()
    run_id = uuid4().hex
    print(f"Karşılaştırma: {run_id}", flush=True)
    try:
        scenarios = json.loads((DATA_DIR / "senaryolar.json").read_text(encoding="utf-8"))
        if not args.all:
            scenarios = [scenario for scenario in scenarios if scenario["id"] == args.scenario]
        if not scenarios:
            raise ValueError("Senaryo bulunamadı.")
        chunks = load_reviewed_sources(DATA_DIR / "belgeler")
        with closing(sqlite3.connect(args.db)) as db:
            db.execute(CREATE_CALLS)
            db.execute(CREATE_ANSWERS)
            vectors = embed_documents(db, run_id, args.embed_model, chunks)
            for scenario in scenarios:
                compare(db, args, run_id, scenario, chunks, vectors)
    except (OSError, ValueError, KeyError, IndexError, TypeError, sqlite3.Error) as error:
        parser.exit(1, f"Karşılaştırma başarısız ({run_id}): {error}\n")


if __name__ == "__main__":
    main()
