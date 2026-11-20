"""Preview, approve and render the standard refund prompt template."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from uuid import uuid4

from netle import CREATE_CALLS
from rag import DATA_DIR, load_chunks
from refund_demo import chat


NAME = "standart-iade"
REVIEWED_VERSIONS = {"IADE-POL#P1": "1.0", "DESTEK-REH#R1": "1.0"}
TEMPLATE = """{urun} için müşteriye kısa, nazik ve doğrudan hitap eden bir Türkçe yanıt yaz.

Teslimattan geçen takvim günü: {teslimattan_sonra_gun}
Ürün kullanıldı mı: {kullanildi}
Aksesuarlar eksiksiz mi: {aksesuarlar_tam}
Kutu açıldı mı: {kutu_acik}

Standart iade süresi teslimattan itibaren 30 takvim günüdür. Teslimat günü 0. gündür ve 30. gün süreye dahildir. Ürün kullanılmamış ve tüm aksesuarları eksiksiz olmalıdır. Kutunun açılması tek başına engel değildir.

Tüm koşullar doğrulanmışsa müşteriye Hesabım → Siparişlerim → İade Talebi yolunu göster. Bir koşulun karşılanmadığı kesinleşmişse nedenini açıkla. Uygunluk belirlenemiyorsa yalnızca kararı etkileyen eksik bilgileri sor. Açılmamış bir iade kaydının açıldığını söyleme.

Yalnızca müşteri mesajını yaz. İç notları, ham görev bilgilerini ve kaynak kimliklerini mesajın dışında tut."""

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


def current_sources():
    chunks = {chunk.source: chunk for chunk in load_chunks(DATA_DIR / "belgeler")}
    sources = []
    for source_id in REVIEWED_VERSIONS:
        chunk = chunks.get(source_id)
        if chunk is None:
            raise ValueError(f"Dayanak bölüm bulunamadı: {source_id}")
        metadata = asdict(chunk)
        metadata["sha256"] = digest(metadata.pop("text"))
        sources.append(metadata)
    return sources


def proposal():
    sources = current_sources()
    if any(source["version"] != REVIEWED_VERSIONS[source["source"]] for source in sources):
        raise ValueError("Kaynak sürümü değişti. İade kuralları yeniden incelenmeli.")
    sources_json = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    approval_id = digest(json.dumps([NAME, TEMPLATE, sources_json], ensure_ascii=False))
    return approval_id, sources_json


def render(db, approval_id, answers):
    row = db.execute(
        "SELECT prompt, sources_json FROM prompt_templates WHERE approval_id = ?", (approval_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Onaylı şablon bulunamadı.")
    template, sources_json = row
    if json.loads(sources_json) != current_sources():
        raise ValueError("Kaynaklar değişti. Şablon yeniden incelenip onaylanmalı.")
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
            approval_id, sources_json = proposal()
            print(TEMPLATE)
            print("\nDayanak belgeler")
            for source in json.loads(sources_json):
                print(f"- {source['source']} · v{source['version']} · {source['effective_date']}")
            print(f"\nOnay kimliği: {approval_id}")
            return

        if args.action == "approve":
            approval_id, sources_json = proposal()
            if args.approval_id != approval_id:
                raise ValueError("Onay kimliği güncel önizlemeyle eşleşmiyor.")
            with closing(sqlite3.connect(args.db)) as db, db:
                db.execute(CREATE_TEMPLATES)
                db.execute(
                    "INSERT INTO prompt_templates (approval_id, name, prompt, sources_json) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(approval_id) DO NOTHING",
                    (approval_id, NAME, TEMPLATE, sources_json),
                )
            print(f"Onaylı şablon kaydedildi: {NAME} ({approval_id})")
            return

        if args.days is not None and args.days < 0:
            raise ValueError("Teslimattan geçen gün negatif olamaz.")
        if not args.product.strip() or len(args.product.splitlines()) != 1:
            raise ValueError("Ürün adı tek satır ve dolu olmalı.")
        labels = {"yes": "Evet", "no": "Hayır", "unknown": "Bilinmiyor"}
        with closing(sqlite3.connect(args.db)) as db:
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

            from refund_graph import CUSTOMER_MESSAGE_RULE, customer_message

            db.execute(CREATE_CALLS)
            run_id = uuid4().hex
            print(f"Çalıştırma kimliği: {run_id}", flush=True)
            if (args.days is not None and args.days <= 30
                    and args.used == "no" and args.accessories == "yes"):
                prompt += (
                    "\n\nBu örnekte standart iade koşulları doğrulandı. "
                    "Yalnızca şu kısa müşteri mesajı biçiminde yanıtla: "
                    "Merhaba, ürününüz için iade talebi oluşturabilirsiniz. "
                    "Hesabım → Siparişlerim → İade Talebi adımlarını izleyebilirsiniz."
                )
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
