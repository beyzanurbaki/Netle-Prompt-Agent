"""Preview, approve and render reviewed refund prompt templates."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from uuid import uuid4

from netle import CREATE_CALLS
from company_documents import (active_chunks, retrieve as retrieve_company_documents,
                               setup as setup_documents)
from refund_demo import chat


NAME = "standart-iade"
TEMPLATE = """{urun} ürünü için iade talebi.
Teslimattan geçen gün: {teslimattan_sonra_gun}
Ürün kullanıldı mı: {kullanildi}
Aksesuarlar eksiksiz mi: {aksesuarlar_tam}
Kutu açıldı mı: {kutu_acik}
İsteği yalnızca etkin ve onaylı şirket belgelerine göre değerlendir."""

DAMAGE = "hasarli-teslimat"
HYGIENE = "hijyen-muhurlu-urun"
TEMPLATES = {
    NAME: TEMPLATE,
    DAMAGE: """Hasarlı ulaştığı bildirilen {urun} ürünü için iade talebi.

Sipariş numarası mevcut mu: {siparis_no_var}
Hasarlı ürünün fotoğrafı mevcut mu: {urun_fotografi_var}
Dış ambalajın fotoğrafı mevcut mu: {ambalaj_fotografi_var}

Bu bilgileri yalnızca etkin ve onaylı şirket belgelerine göre değerlendir.""",
    HYGIENE: """Hijyen mühürlü olduğu bildirilen {urun} ürünü için iade talebi.

Hijyen mührü açıldı mı: {hijyen_muhru_acik}
Teslimattan geçen takvim günü: {teslimattan_sonra_gun}
Ürün kullanıldı mı: {kullanildi}
Aksesuarlar eksiksiz mi: {aksesuarlar_tam}

Bu bilgileri yalnızca etkin ve onaylı şirket belgelerine göre değerlendir.""",
}

CREATE_TEMPLATES = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    approval_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def current_sources(name, db):
    if name not in TEMPLATES:
        raise ValueError("Şablon bulunamadı.")
    chunks = active_chunks(db)
    if not chunks:
        raise ValueError("Şablon için önce yönetici tarafından onaylanmış belge eklenmeli.")
    sources = []
    for chunk in chunks:
        metadata = asdict(chunk)
        metadata["sha256"] = digest(metadata.pop("text"))
        sources.append(metadata)
    return sources


def proposal(name, db):
    sources = current_sources(name, db)
    template = TEMPLATES[name]
    sources_json = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    approval_id = digest(json.dumps([name, template, sources_json], ensure_ascii=False))
    return approval_id, sources_json


def approve_template(db, approval_id, name=NAME):
    expected_id, sources_json = proposal(name, db)
    if approval_id != expected_id:
        raise ValueError("Onay kimliği güncel önizlemeyle eşleşmiyor.")
    with db:
        db.execute(CREATE_TEMPLATES)
        db.execute(
            "INSERT INTO prompt_templates (approval_id, name, prompt, sources_json) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(approval_id) DO NOTHING",
            (approval_id, name, TEMPLATES[name], sources_json),
        )


def approved_template(db, approval_id):
    row = db.execute(
        "SELECT name, prompt, sources_json FROM prompt_templates WHERE approval_id = ?", (approval_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Onaylı şablon bulunamadı.")
    name, template, sources_json = row
    sources = json.loads(sources_json)
    if sources != current_sources(name, db):
        raise ValueError("Kaynaklar değişti. Şablon yeniden incelenip onaylanmalı.")
    return name, template, sources


def render(db, approval_id, answers):
    _, template, _ = approved_template(db, approval_id)
    return template.format_map(answers)


def main():
    parser = argparse.ArgumentParser(description="Standart iade prompt şablonu")
    parser.add_argument("--db", default="netle.sqlite3")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("preview")
    approve = actions.add_parser("approve", help="Önizleme kimliğini açıkça onaylayıp kaydet")
    approve.add_argument("approval_id")
    fill = actions.add_parser("render", help="Onaylı şablonu yeni ürün için doldur")
    fill.add_argument("approval_id")
    fill.add_argument("--product", required=True)
    fill.add_argument("--days", type=int)
    fill.add_argument("--chat-model", help="Doldurulan şablonu bu Ollama modeliyle çalıştır")
    for flag in ("used", "accessories", "box-open"):
        fill.add_argument(f"--{flag}", choices=("yes", "no", "unknown"), default="unknown")
    args = parser.parse_args()

    try:
        if args.action == "preview":
            with closing(sqlite3.connect(args.db)) as db:
                setup_documents(db)
                approval_id, sources_json = proposal(NAME, db)
                print(TEMPLATE)
                print("\nDayanak belgeler")
                for source in json.loads(sources_json):
                    print(f"- {source['source']} · v{source['version']} · {source['effective_date']}")
                print(f"\nOnay kimliği: {approval_id}")
            return

        if args.action == "approve":
            with closing(sqlite3.connect(args.db)) as db:
                setup_documents(db)
                approve_template(db, args.approval_id)
            print(f"Onaylı şablon kaydedildi: {NAME} ({args.approval_id})")
            return

        if args.days is not None and args.days < 0:
            raise ValueError("Teslimattan geçen gün negatif olamaz.")
        if not args.product.strip() or len(args.product.splitlines()) != 1:
            raise ValueError("Ürün adı tek satır ve dolu olmalı.")
        labels = {"yes": "Evet", "no": "Hayır", "unknown": "Bilinmiyor"}
        with closing(sqlite3.connect(args.db)) as db:
            setup_documents(db)
            db.execute(CREATE_TEMPLATES)
            prompt = render(db, args.approval_id, {
                "urun": args.product.strip(),
                "teslimattan_sonra_gun": "Bilinmiyor" if args.days is None else args.days,
                "kullanildi": labels[args.used],
                "aksesuarlar_tam": labels[args.accessories],
                "kutu_acik": labels[args.box_open],
            })
            if not args.chat_model:
                print(prompt)
                return

            from refund_graph import CUSTOMER_MESSAGE_RULE, build_prompt, customer_message

            db.execute(CREATE_CALLS)
            run_id = uuid4().hex
            print(f"Çalıştırma kimliği: {run_id}", flush=True)
            chunks = retrieve_company_documents(db, run_id, "embeddinggemma:latest", prompt)
            prompt = build_prompt({"scenario": {"ilk_istek": prompt}, "facts": {
                "urun": args.product.strip(), "teslimattan_sonra_gun": args.days,
                "kullanildi": args.used == "yes" if args.used != "unknown" else None,
                "aksesuarlar_tam": args.accessories == "yes" if args.accessories != "unknown" else None,
            }, "sources": [asdict(chunk) for chunk in chunks]})["prompt"]
            draft = chat(
                db, run_id, args.chat_model, "template_generate_draft",
                CUSTOMER_MESSAGE_RULE + " Merhaba ile başla ve en fazla üç cümle yaz. "
                "Kuralları değerlendir ve sonucu müşteriye bildir. Talimatları kopyalama.",
                prompt + "\n\nMüşteriye gönderilecek kısa mesaj:\nMerhaba,",
            )
            print(customer_message(draft))
            input_tokens, output_tokens, elapsed_ms = db.execute(
                "SELECT input_tokens, output_tokens, elapsed_ms FROM model_calls WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            print(f"Girdi: {input_tokens} token | Çıktı: {output_tokens} token | Süre: {elapsed_ms:.1f} ms")
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Şablon işlemi başarısız: {error}\n")


if __name__ == "__main__":
    main()
