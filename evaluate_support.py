"""Evaluate the current Prompt Developer against two customer-service baselines."""

import argparse
import json
import math
import random
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from uuid import uuid4

from company_documents import active_chunks, retrieve, setup
from netle import CREATE_CALLS
from prompt_developer import MODEL, develop, source_details
from refund_demo import chat


EMBED_MODEL = "embeddinggemma:latest"
ANSWER_RULE = (
    "Yalnızca müşteriye hitap eden kısa ve nazik bir Türkçe yanıt yaz. "
    "Doğrulanmamış işlemleri veya şirket kurallarını varmış gibi anlatma. "
    "Kaynak, açıklama veya başlık ekleme. Kaynaklar karar için birbiriyle çelişiyorsa "
    "yalnızca [[SOURCE_CONFLICT]] yaz."
)
RUBRIC = {
    "dogruluk": "Karar, politika dayanağı ve verilen bilgiler doğru mu?",
    "eksiksizlik": "Müşterinin sonraki adımları ve gerekli koşullar açıklanmış mı?",
    "bicim": "Kısa, nazik ve doğrudan müşteriye hitap eden Türkçe bir mesaj mı?",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_report(path, report):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def cases_from(path):
    cases = read_json(path)
    if not isinstance(cases, list) or not cases:
        raise ValueError("Senaryo dosyası boş olmayan bir JSON listesi olmalı.")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or any(
            not isinstance(case.get(key), str) or not case[key].strip()
            for key in ("id", "ilk_istek", "referans")
        ):
            raise ValueError("Her senaryoda id, ilk_istek ve referans metni olmalı.")
        if case["id"] in seen:
            raise ValueError("Senaryo kimlikleri benzersiz olmalı.")
        seen.add(case["id"])
        facts = case.get("ilk_bilgiler", {})
        answers = case.get("ek_bilgiler", [])
        if not isinstance(facts, dict) or not isinstance(answers, list) or len(answers) > 9 or any(
            not isinstance(pair, list) or len(pair) != 2
            or any(not isinstance(value, str) or not 0 < len(value.strip()) <= 1200 for value in pair)
            for pair in answers
        ):
            raise ValueError(f"{case['id']}: ilk_bilgiler nesne, ek_bilgiler soru ve yanıt çiftleri olmalı.")
        initial = case["ilk_istek"].strip()
        if facts:
            initial += "\nİlk bilgiler: " + json.dumps(facts, ensure_ascii=False)
        if len(initial) > 3000:
            raise ValueError(f"{case['id']}: ilk istek ve bilgiler 3000 karakteri aşıyor.")
        yield case, initial, answers


def model_metrics(db, run_id, prices):
    rows = db.execute(
        "SELECT operation, model, input_tokens, output_tokens, elapsed_ms, status "
        "FROM model_calls WHERE run_id=? ORDER BY id", (run_id,),
    )
    calls = [dict(row) for row in rows]
    operations = Counter()
    estimated_cost = 0.0
    cost_complete = True
    for call in calls:
        is_embedding = call["operation"].startswith("embed_")
        if not is_embedding:
            operations[call["operation"]] += 1
        rates = (prices["embedding"], 0) if is_embedding else (prices["input"], prices["output"])
        for key, rate in zip(("input_tokens", "output_tokens"), rates, strict=True):
            if rate and call[key] is None:
                cost_complete = False
            estimated_cost += (call[key] or 0) * rate / 1_000_000
    return {
        "calls": calls,
        "input_tokens": sum(call["input_tokens"] or 0 for call in calls),
        "output_tokens": sum(call["output_tokens"] or 0 for call in calls),
        "calls_with_missing_usage": sum(
            call["input_tokens"] is None
            or (not call["operation"].startswith("embed_") and call["output_tokens"] is None)
            for call in calls
        ),
        "model_ms": round(sum(call["elapsed_ms"] for call in calls), 1),
        "retries": sum(count - 1 for count in operations.values()),
        "estimated_cost": round(estimated_cost, 8) if cost_complete else None,
    }


def attempt(snapshot, method, initial, answers, prices):
    run_id = uuid4().hex
    with closing(sqlite3.connect(":memory:")) as db:
        snapshot.backup(db)
        db.row_factory = sqlite3.Row
        started = perf_counter()
        result = {"method": method, "status": "ready", "draft": "", "sources": []}
        try:
            facts = "\n".join(f"- {question} {answer}" for question, answer in answers)
            task = initial + (f"\nEk görev bilgileri\n{facts}" if facts else "")
            if method == "direct_rag":
                result["sources"] = [
                    source_details(chunk) for chunk in retrieve(db, run_id, EMBED_MODEL, task, top_k=4)
                ]
            if method in ("netle", "netle_initial"):
                # Ek bilgiler çalışanın düzeltmeleri gibi verilir; yorum otomatik onaylanır.
                request = {"request": initial[:1500],
                           "corrections": [f"{question} {answer}" for question, answer in answers]}
                interpretation = develop(db, run_id, request)
                result["interpretation"] = interpretation["interpretation"]
                result["unclear"] = interpretation["unclear"]
                development = develop(db, run_id, {**request, "action": "answer",
                                                   "confirmed": interpretation["interpretation"]})
                result["sources"] = development.get("sources", [])
                if development["status"] == "answered":
                    result.update(answer=development["answer"], draft=development["draft"])
                else:
                    result.update(status=development["status"], message=development.get("message", ""))
            elif result["status"] == "ready":
                if result["sources"]:
                    task += "\n\nOnaylı şirket kaynakları\n" + json.dumps(
                        result["sources"], ensure_ascii=False,
                    )
                draft = chat(db, run_id, MODEL, "evaluation_draft", ANSWER_RULE, task)
                if draft == "[[SOURCE_CONFLICT]]":
                    result.update(status="conflict", message="Model kaynak çelişkisi bildirdi.")
                else:
                    result["draft"] = draft
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            result.update(status="error", message=str(error))
        result["wall_ms"] = round((perf_counter() - started) * 1000, 1)
        return {**result, **model_metrics(db, run_id, prices)}


def run(args):
    cases = list(cases_from(args.cases))
    if args.case:
        cases = [item for item in cases if item[0]["id"] == args.case]
    if not cases:
        raise ValueError("Senaryo bulunamadı.")
    if args.output.exists():
        raise ValueError("Rapor dosyası zaten var. Yeni bir çıktı yolu belirt.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prices = {"input": args.input_price, "output": args.output_price, "embedding": args.embedding_price}
    methods = ["original", "direct_rag", "netle"]
    if args.with_ablation:
        methods.append("netle_initial")
    with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
            closing(sqlite3.connect(":memory:")) as snapshot:
        source.backup(snapshot)
        setup(snapshot)
        documents = active_chunks(snapshot)
        if not documents:
            raise ValueError("Değerlendirme için etkin ve onaylı şirket belgesi gerekli.")
        # Each method and case starts without an embedding cache and records its own indexing.
        snapshot.execute("DROP TABLE IF EXISTS document_embeddings")
        snapshot.execute("DROP TABLE IF EXISTS model_calls")
        snapshot.execute(CREATE_CALLS)
        snapshot.commit()
        report = {
            "chat_model": MODEL, "embedding_model": EMBED_MODEL,
            "currency": args.currency, "price_per_million": prices,
            "indexing": "cold_per_method_and_case",
            "expected_cases": [case["id"] for case, _, _ in cases],
            "methods": methods,
            "documents": sorted({f"{item.filename} · v{item.version}" for item in documents}),
            "cases": [],
        }
        for case, initial, answers in cases:
            entry = {"id": case["id"], "reference": case["referans"], "initial": initial,
                     "answers": answers, "results": []}
            report["cases"].append(entry)
            for method in methods:
                supplied = [] if method in ("original", "netle_initial") else answers
                result = attempt(snapshot, method, initial, supplied, prices)
                entry["results"].append(result)
                save_report(args.output, report)
                print(
                    f"{entry['id']} · {method} · {result['status']} · "
                    f"{result['input_tokens']} + {result['output_tokens']} token · "
                    f"{result['wall_ms'] / 1000:.2f} sn · {result['estimated_cost']} {args.currency}",
                    flush=True,
                )


def integer_input(label, maximum=None):
    while True:
        value = input(label).strip()
        if value.isdecimal() and (maximum is None or int(value) <= maximum):
            return int(value)
        print("Geçerli bir sayı gir.")


def review(args):
    report = read_json(args.report)
    for case in report["cases"]:
        pending = [result for result in case["results"]
                   if result["draft"] and "review" not in result]
        random.shuffle(pending)
        for index, result in enumerate(pending, 1):
            print(f"\n{case['id']} · Taslak {index}/{len(pending)}\nReferans\n{case['reference']}")
            input("Başlamak için Enter'a bas. Metni hazır bulursan bitiş satırını doğrudan gir. ")
            started = perf_counter()
            print(f"\n{result['draft']}\n\nDüzenlenmiş metni yaz veya yapıştır. Bitirmek için tek satırda . yaz.")
            lines = []
            while (line := input()) != ".":
                lines.append(line)
            edit_seconds = round(perf_counter() - started, 2)
            corrections = (
                integer_input("Anlamlı düzeltme sayısı: ")
                if lines and "\n".join(lines).strip() != result["draft"].strip() else 0
            )
            print("İlk taslağı puanla. 0 karşılamıyor, 1 kısmen, 2 karşılıyor.")
            scores = {key: integer_input(f"{label} [0–2] ", 2) for key, label in RUBRIC.items()}
            result["review"] = {"scores": scores, "quality_out_of_6": sum(scores.values()),
                                "edit_seconds": edit_seconds, "corrections": corrections,
                                "edited_draft": "\n".join(lines) if lines else result["draft"]}
            save_report(args.report, report)
    summary(report)


def summary(report):
    for method in report["methods"]:
        results = [result for case in report["cases"] for result in case["results"]
                   if result["method"] == method]
        reviewed = [item["review"] for item in results if "review" in item]
        print(f"\n{method} · {len(results)}/{len(report['expected_cases'])} çalıştırma · "
              f"{sum(item['status'] == 'ready' for item in results)} taslak")
        if not results:
            continue
        costs = [item["estimated_cost"] for item in results]
        print(f"Token: {sum(item['input_tokens'] for item in results)} girdi, "
              f"{sum(item['output_tokens'] for item in results)} çıktı · "
              f"Toplam süre: {sum(item['wall_ms'] for item in results) / 1000:.2f} sn · "
              f"Tekrar çağrı: {sum(item['retries'] for item in results)}")
        print("Maliyet:", "eksik kullanım verisi" if None in costs
              else f"{sum(costs):.8f} {report['currency']}")
        if reviewed:
            count = len(reviewed)
            print(f"{count} taslak incelendi · "
                  f"Kalite: {sum(item['quality_out_of_6'] for item in reviewed) / count:.2f}/6 · "
                  f"Düzenleme: {sum(item['edit_seconds'] for item in reviewed) / count:.2f} sn · "
                  f"Düzeltme: {sum(item['corrections'] for item in reviewed) / count:.2f}")
        else:
            print("Kalite ve düzenleme için değerlendirici kaydı bekleniyor.")
    for baseline in ("direct_rag", "netle_initial"):
        if baseline not in report["methods"]:
            continue
        pairs = []
        for case in report["cases"]:
            results = {item["method"]: item for item in case["results"]}
            if baseline in results and "netle" in results:
                pairs.append((results["netle"], results[baseline]))
        if not pairs:
            continue
        print(f"\nNETLE − {baseline} · {len(pairs)} ortak vaka")
        differences = [
            f"Girdi tokenı {sum(new['input_tokens'] - old['input_tokens'] for new, old in pairs):+}",
            f"Çıktı tokenı {sum(new['output_tokens'] - old['output_tokens'] for new, old in pairs):+}",
            f"Süre {sum(new['wall_ms'] - old['wall_ms'] for new, old in pairs) / 1000:+.2f} sn",
        ]
        if all(item["estimated_cost"] is not None for pair in pairs for item in pair):
            cost = sum(new["estimated_cost"] - old["estimated_cost"] for new, old in pairs)
            differences.append(f"Maliyet {cost:+.8f} {report['currency']}")
        print(" · ".join(differences))
        reviewed_pairs = [(new["review"], old["review"]) for new, old in pairs
                          if "review" in new and "review" in old]
        if reviewed_pairs:
            count = len(reviewed_pairs)
            quality = sum(new["quality_out_of_6"] - old["quality_out_of_6"]
                          for new, old in reviewed_pairs) / count
            editing = sum(new["edit_seconds"] - old["edit_seconds"]
                          for new, old in reviewed_pairs) / count
            print(f"{count} çift incelendi · Ortalama kalite farkı {quality:+.2f}/6 · "
                  f"Ortalama düzenleme farkı {editing:+.2f} sn")


def rate(text):
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("Fiyat sonlu ve negatif olmayan bir sayı olmalı.")
    return value


def main():
    parser = argparse.ArgumentParser(description="NETLE müşteri hizmetleri değerlendirme modu")
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("run", help="Aynı vakalarda taslakları üret ve ölç")
    execute.add_argument("--db", type=Path, default=Path("netle.sqlite3"))
    execute.add_argument("--cases", type=Path, required=True)
    execute.add_argument("--case")
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--with-ablation", action="store_true", help="NETLE'yi ek bilgiler olmadan da çalıştır")
    execute.add_argument("--currency", default="TRY")
    for name in ("input", "output", "embedding"):
        execute.add_argument(f"--{name}-price", type=rate, default=0, help="Milyon token başına birim fiyat")
    for name in ("review", "summary"):
        commands.add_parser(name).add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "run":
            run(args)
        elif args.command == "review":
            review(args)
        else:
            summary(read_json(args.report))
    except (OSError, ValueError, sqlite3.Error, EOFError) as error:
        parser.exit(1, f"Değerlendirme tamamlanamadı. {error}\n")


if __name__ == "__main__":
    main()
