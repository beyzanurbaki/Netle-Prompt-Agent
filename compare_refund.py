"""Compare the original request, direct RAG and Netle for the I01 scenario."""

import argparse
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

from netle import CREATE_CALLS
from rag import DATA_DIR, embed, load_chunks, rank, scenario_query
from refund_demo import chat
from refund_graph import CUSTOMER_MESSAGE_RULE, build_prompt, customer_message


def main():
    parser = argparse.ArgumentParser(description="I01 iade yanıtlarını karşılaştır")
    parser.add_argument("--chat-model", default="qwen2.5:1.5b")
    parser.add_argument("--embed-model", default="embeddinggemma:latest")
    parser.add_argument("--db", default="netle.sqlite3")
    args = parser.parse_args()
    run_id = uuid4().hex
    print(f"Karşılaştırma: {run_id}", flush=True)

    try:
        scenarios = json.loads((DATA_DIR / "senaryolar.json").read_text(encoding="utf-8"))
        scenario = next(item for item in scenarios if item["id"] == "I01")
        facts = {**scenario["ilk_bilgiler"], **scenario["ek_bilgiler"]}
        query = scenario_query(scenario, clarified=True)
        chunks = load_chunks(DATA_DIR / "belgeler")
        system = CUSTOMER_MESSAGE_RULE

        with closing(sqlite3.connect(args.db)) as db:
            db.execute(CREATE_CALLS)
            vectors = embed(db, run_id, args.embed_model, [
                f"{chunk.title}\n{chunk.text}" for chunk in chunks
            ], "compare_embed_index")
            vector, = embed(db, run_id, args.embed_model, [query], "compare_embed_query")
            hits = rank(vector, chunks, vectors)[:3]
            required_sources = {"IADE-POL#P1", "DESTEK-REH#R1"}
            reviewed_sources = {chunk.source for chunk, _ in hits if chunk.version == "1.0"}
            if not required_sources.issubset(reviewed_sources):
                raise ValueError("I01 için incelenmiş v1.0 politika ve rehber kaynakları bulunamadı.")

            sources = "\n\n".join(
                f"[{chunk.source} · v{chunk.version}] {chunk.title}\n{chunk.text}"
                for chunk, _ in hits
            )
            print("\nOrtak kaynaklar: " + ", ".join(chunk.source for chunk, _ in hits))
            context = (
                f"Doğrulanmış bilgiler:\n{json.dumps(facts, ensure_ascii=False)}"
                f"\n\nŞirket kaynakları:\n{sources}"
            )
            netle_prompt = build_prompt({"facts": facts})["prompt"]
            methods = [
                ("compare_original", "İlk istek", scenario["ilk_istek"]),
                ("compare_direct_rag", "Doğrudan RAG", f"{scenario['ilk_istek']}\n\n{context}"),
                ("compare_netle", "Netle", f"{context}\n\nGörev:\n{netle_prompt}"),
            ]
            for operation, label, prompt in methods:
                draft = chat(db, run_id, args.chat_model, operation, system, prompt)
                if operation == "compare_netle":
                    message = customer_message(draft)
                    if message != draft:
                        print("\nNetle: Müşteri mesajının sonuna eklenen iç bölüm çıkarıldı.")
                    draft = message
                print(f"\n{label}\n{draft}")

            print("\nÇağrı ölçümleri")
            for operation, input_tokens, output_tokens, elapsed_ms in db.execute(
                "SELECT operation, input_tokens, output_tokens, elapsed_ms "
                "FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,),
            ):
                print(f"- {operation}: girdi={input_tokens}, çıktı={output_tokens}, {elapsed_ms:.1f} ms")
    except (OSError, ValueError, KeyError, IndexError, TypeError, StopIteration, sqlite3.Error) as error:
        parser.exit(1, f"Karşılaştırma başarısız ({run_id}): {error}\n")


if __name__ == "__main__":
    main()
