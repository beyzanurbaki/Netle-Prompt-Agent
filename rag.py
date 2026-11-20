"""Index the fictional Luma Ev policies and benchmark source retrieval."""

import argparse
import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

from netle import CREATE_CALLS, call_ollama


DATA_DIR = Path(__file__).resolve().parent / "ornek-veri"
SECTION = re.compile(r"^## (\w+) · (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    source: str
    version: str
    effective_date: str
    filename: str
    title: str
    text: str


def load_chunks(directory):
    chunks = []
    for path in sorted(directory.glob("*.md")):
        header, *sections = SECTION.split(path.read_text(encoding="utf-8"))
        metadata = dict(
            (key, value.strip())
            for key, value in re.findall(
                r"^(Belge kimliği|Sürüm|Yürürlük tarihi): (.+)$", header, re.MULTILINE
            )
        )
        if not sections or len(metadata) != 3:
            raise ValueError(f"Belge üst bilgisi veya bölüm başlığı eksik: {path.name}")
        for section_id, title, body in zip(sections[::3], sections[1::3], sections[2::3]):
            chunks.append(Chunk(
                f"{metadata['Belge kimliği']}#{section_id}", metadata["Sürüm"],
                metadata["Yürürlük tarihi"], path.name, title.strip(), body.strip(),
            ))
    if not chunks:
        raise ValueError(f"İndekslenecek belge bulunamadı: {directory}")
    if len({chunk.source for chunk in chunks}) != len(chunks):
        raise ValueError("Belge kaynak kimlikleri benzersiz olmalı.")
    return chunks


def embed(db, run_id, model, inputs, operation):
    response = call_ollama(db, run_id, "embed", {
        "model": model, "input": inputs, "truncate": False,
    }, operation=operation)
    vectors = response["embeddings"]
    if len(vectors) != len(inputs):
        raise ValueError("Embedding sayısı girdi sayısıyla eşleşmiyor.")
    normalized = []
    for vector in vectors:
        norm = math.hypot(*vector)
        if not norm or not math.isfinite(norm):
            raise ValueError("Geçersiz embedding normu.")
        normalized.append(tuple(value / norm for value in vector))
    return normalized


def embedding_model_digest(model):
    name = model if ":" in model.rsplit("/", 1)[-1] else f"{model}:latest"
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
        models = json.load(response)["models"]
    for entry in models:
        if name in (entry.get("name"), entry.get("model")) and entry.get("digest"):
            return entry["digest"]
    raise ValueError(f"Ollama'da embedding modeli bulunamadı: {model}")


def embed_documents(db, run_id, model, chunks):
    inputs = [f"{chunk.title}\n{chunk.text}" for chunk in chunks]
    manifest = json.dumps(
        {"chunks": [asdict(chunk) for chunk in chunks], "inputs": inputs},
        ensure_ascii=False, sort_keys=True,
    )
    fingerprint = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    model_digest = embedding_model_digest(model)
    db.execute("""
        CREATE TABLE IF NOT EXISTS document_embeddings (
            model TEXT PRIMARY KEY,
            model_digest TEXT NOT NULL,
            document_fingerprint TEXT NOT NULL,
            vectors_json TEXT NOT NULL
        )
    """)
    cached = db.execute(
        "SELECT vectors_json FROM document_embeddings "
        "WHERE model = ? AND model_digest = ? AND document_fingerprint = ?",
        (model, model_digest, fingerprint),
    ).fetchone()
    if cached is not None:
        return json.loads(cached[0])

    vectors = embed(db, run_id, model, inputs, "embed_index")
    if embedding_model_digest(model) != model_digest:
        raise ValueError("Embedding modeli indeksleme sırasında değişti. İsteği yeniden başlat.")
    with db:
        db.execute(
            """
            INSERT INTO document_embeddings VALUES (?, ?, ?, ?)
            ON CONFLICT(model) DO UPDATE SET
                model_digest = excluded.model_digest,
                document_fingerprint = excluded.document_fingerprint,
                vectors_json = excluded.vectors_json
            """,
            (model, model_digest, fingerprint, json.dumps(vectors)),
        )
    return vectors


def rank(query, chunks, vectors):
    scored = [
        (chunk, math.fsum(a * b for a, b in zip(query, vector, strict=True)))
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    return sorted(scored, key=lambda result: (-result[1], result[0].source))


def scenario_query(scenario, clarified):
    facts = dict(scenario["ilk_bilgiler"])
    if clarified:
        facts.update(scenario["ek_bilgiler"])
    return f"{scenario['ilk_istek']}\n{json.dumps(facts, ensure_ascii=False)}"


def main():
    parser = argparse.ArgumentParser(description="Örnek belgelerde kaynak araması")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--evaluate", action="store_true")
    parser.add_argument("--clarified", action="store_true", help="Senaryolardaki ek bilgileri kullan")
    parser.add_argument("--embed-model", default="embeddinggemma:latest")
    parser.add_argument("--db", default="netle.sqlite3")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k en az 1 olmalı.")
    if args.clarified and not args.evaluate:
        parser.error("--clarified yalnızca --evaluate ile kullanılabilir.")

    run_id = uuid4().hex
    try:
        chunks = load_chunks(DATA_DIR / "belgeler")
        if args.evaluate:
            scenarios = json.loads((DATA_DIR / "senaryolar.json").read_text(encoding="utf-8"))
            queries = [
                (scenario["id"], scenario_query(scenario, args.clarified),
                 set(scenario["referans"]["kaynaklar"]))
                for scenario in scenarios
            ]
        else:
            queries = [("Sorgu", args.query, set())]

        with closing(sqlite3.connect(args.db)) as db:
            db.execute(CREATE_CALLS)
            vectors = embed_documents(db, run_id, args.embed_model, chunks)
            print(f"Çalıştırma kimliği: {run_id} | İndeks: {len(chunks)} bölüm")
            expected_total = found_total = complete_count = 0
            for label, query, expected in queries:
                query_vector, = embed(db, run_id, args.embed_model, [query], "embed_query")
                hits = rank(query_vector, chunks, vectors)[:args.top_k]
                print(f"\n{label}")
                for chunk, score in hits:
                    print(f"  {score:.4f}  {chunk.source} · {chunk.title} "
                          f"(v{chunk.version}, {chunk.effective_date}, {chunk.filename})")
                    if not args.evaluate:
                        print(f"    {chunk.text}")
                if args.evaluate:
                    found = expected.intersection(chunk.source for chunk, _ in hits)
                    expected_total += len(expected)
                    found_total += len(found)
                    complete_count += found == expected
                    print(f"  Beklenen kaynaklar: {', '.join(sorted(expected))} "
                          f"| Bulunan: {len(found)}/{len(expected)}")
            if args.evaluate:
                print(f"\nRecall@{args.top_k}: {found_total}/{expected_total} "
                      f"| Tüm kaynakları bulunan senaryo: {complete_count}/{len(queries)}")
    except (OSError, ValueError, KeyError, IndexError, TypeError, sqlite3.Error) as error:
        parser.exit(1, f"RAG araması başarısız ({run_id}): {error}\n")


if __name__ == "__main__":
    main()
