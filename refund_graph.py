"""Pause and resume the I01 refund demo with a persisted LangGraph thread."""

import argparse
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from netle import CREATE_CALLS
from rag import DATA_DIR, embed, load_chunks, rank, scenario_query
from refund_demo import chat


CUSTOMER_MESSAGE_RULE = (
    "Yalnızca müşteriye doğrudan hitap eden kısa, nazik bir Türkçe mesaj yaz. "
    "Mesajın dışında açıklama, başlık, liste, iç bilgi dökümü veya kaynak gösterimi yazma."
)
INTERNAL_SECTION = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?"
    r"(?:doğrulanmış bilgiler|bilinen bilgiler|şirket kaynakları|kaynaklar|gerekçe)"
    r"\s*(?:\*\*)?\s*(?::|$)",
    re.IGNORECASE | re.MULTILINE,
)


def customer_message(draft):
    """Keep the customer text ahead of any trailing internal section."""
    message = INTERNAL_SECTION.split(draft, maxsplit=1)[0].strip()
    if not message or re.search(r"IADE-POL#|DESTEK-REH#|teslimattan_sonra_gun",
                                message, re.IGNORECASE):
        raise ValueError("Müşteri taslağında iç bilgi bulundu.")
    return message


QUESTIONS = {
    "teslimattan_sonra_gun": "Teslimattan bu yana kaç takvim günü geçti?",
    "kullanildi": "Ürün kullanıldı mı?",
    "aksesuarlar_tam": "Tüm aksesuarlar eksiksiz mi?",
}


class RefundState(TypedDict, total=False):
    session_id: str
    scenario: dict
    facts: dict
    chat_model: str
    embed_model: str
    sources: list[dict]
    prompt: str
    reason: str
    draft: str
    reviewed_message: str
    preserve_reviewed_message: bool


def missing_fields(state):
    return [field for field in QUESTIONS if state["facts"].get(field) is None]


def clarify(state):
    fields = missing_fields(state)
    answers = interrupt({field: QUESTIONS[field] for field in fields})
    return {"facts": {**state["facts"], **{field: answers[field] for field in fields}}}


def build_prompt(state):
    facts = state["facts"]
    # This interpretation is specific to the reviewed version of the demo policy.
    eligibility = {
        "30 günlük sürenin aşılması": facts["teslimattan_sonra_gun"] > 30,
        "ürünün kullanılmış olması": facts["kullanildi"],
        "aksesuarların eksik olması": not facts["aksesuarlar_tam"],
    }
    unmet = [reason for reason, applies in eligibility.items() if applies]
    if unmet:
        reason = f"Standart iade koşullarından karşılanmayanlar: {', '.join(unmet)}."
        guidance = "Standart iade koşullarının neden karşılanmadığını nazikçe açıkla."
        example = (
            "Merhaba, iade talebinizi inceledik. Şu nedenle standart iade koşulları "
            f"karşılanmıyor: {', '.join(unmet)}. Anlayışınız için teşekkür ederiz."
        )
    else:
        reason = (
            f"Teslimattan sonra {facts['teslimattan_sonra_gun']} gün geçmiş. "
            "Ürün kullanılmamış ve aksesuarları tam. Açılmış kutu tek başına engel değil."
        )
        guidance = (
            "'İade talebinizi oluşturabilirsiniz' diyerek doğrudan müşteriye hitap et. "
            "Hesabım → Siparişlerim → İade Talebi yolunu göster."
        )
        example = (
            "Merhaba, ürününüz için iade talebi oluşturabilirsiniz. "
            "Kutunun açılması tek başına iadeye engel değildir. "
            "Hesabım → Siparişlerim → İade Talebi adımlarını izleyebilirsiniz."
        )
    prompt = (
        f"{facts.get('urun') or 'İade edilen ürün'} için müşteriye kısa ve nazik bir Türkçe mesaj taslağı yaz. "
        f"{reason} {guidance} "
        f"Müşteriye yalnızca mesajı göster. Yanıt örneğinin üslubunu ve kısalığını koru: {example}"
    )
    return {"prompt": prompt, "reason": reason, "reviewed_message": example}


def make_graph(db, saver):
    def retrieve(state):
        chunks = load_chunks(DATA_DIR / "belgeler")
        session_id, model = state["session_id"], state["embed_model"]
        vectors = embed(db, session_id, model, [
            f"{chunk.title}\n{chunk.text}" for chunk in chunks
        ], "embed_index")
        query, = embed(db, session_id, model, [
            scenario_query(state["scenario"], clarified=False)
        ], "embed_query")
        hits = {chunk.source: chunk for chunk, _ in rank(query, chunks, vectors)[:3]}
        required = ("IADE-POL#P1", "DESTEK-REH#R1")
        if any(source not in hits or hits[source].version != "1.0" for source in required):
            raise ValueError("Standart iade için incelenmiş v1.0 politika ve rehber kaynakları bulunamadı.")
        return {"sources": [asdict(hits[source]) for source in required]}

    def generate(state):
        if state.get("preserve_reviewed_message"):
            reviewed = state["reviewed_message"]
            draft = chat(
                db, state["session_id"], state["chat_model"], "generate_draft",
                CUSTOMER_MESSAGE_RULE + " Son satırdaki müşteri mesajını aynen döndür.",
                f"{state['prompt']}\n\nMüşteri mesajı:\n{reviewed}",
            )
            return {"draft": draft if draft == reviewed else reviewed}
        draft = chat(
            db, state["session_id"], state["chat_model"], "generate_draft",
            CUSTOMER_MESSAGE_RULE + " Örnekteki kararı ve sonraki adımı koru.",
            state["prompt"],
        )
        return {"draft": customer_message(draft)}

    graph = StateGraph(RefundState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("clarify", clarify)
    graph.add_node("build_prompt", build_prompt)
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_conditional_edges("retrieve", lambda state: bool(missing_fields(state)),
                                {True: "clarify", False: "build_prompt"})
    graph.add_edge("clarify", "build_prompt")
    graph.add_edge("build_prompt", "generate")
    graph.add_edge("generate", END)
    return graph.compile(checkpointer=saver)


def main():
    parser = argparse.ArgumentParser(description="I01 lamba iadesi LangGraph demosu")
    parser.add_argument("--db", default="netle.sqlite3")
    commands = parser.add_subparsers(dest="action", required=True)
    start = commands.add_parser("start")
    start.add_argument("--chat-model", default="qwen2.5:1.5b")
    start.add_argument("--embed-model", default="embeddinggemma:latest")
    resume = commands.add_parser("resume")
    resume.add_argument("session_id")
    resume.add_argument("--days", type=int, required=True)
    resume.add_argument("--used", choices=["yes", "no"], required=True)
    resume.add_argument("--accessories", choices=["yes", "no"], required=True)
    args = parser.parse_args()
    if args.action == "resume" and args.days < 0:
        parser.error("--days negatif olamaz.")

    session_id = uuid4().hex if args.action == "start" else args.session_id
    config = {"configurable": {"thread_id": session_id}}
    print(f"Oturum: {session_id}", flush=True)
    try:
        with (closing(sqlite3.connect(args.db, check_same_thread=False)) as db,
              SqliteSaver.from_conn_string(args.db) as saver):
            db.execute(CREATE_CALLS)
            graph = make_graph(db, saver)
            if args.action == "start":
                scenarios = json.loads((DATA_DIR / "senaryolar.json").read_text(encoding="utf-8"))
                scenario = next(item for item in scenarios if item["id"] == "I01")
                result = graph.invoke({
                    "session_id": session_id,
                    "scenario": {"ilk_istek": scenario["ilk_istek"],
                                 "ilk_bilgiler": scenario["ilk_bilgiler"]},
                    "facts": scenario["ilk_bilgiler"],
                    "chat_model": args.chat_model,
                    "embed_model": args.embed_model,
                }, config)
            else:
                if graph.get_state(config).next != ("clarify",):
                    raise ValueError("Bu oturum kullanıcı yanıtı beklemiyor.")
                result = graph.invoke(Command(resume={
                    "teslimattan_sonra_gun": args.days,
                    "kullanildi": args.used == "yes",
                    "aksesuarlar_tam": args.accessories == "yes",
                }), config)

            if result.get("__interrupt__"):
                print("\nEksik bilgiler")
                for question in result["__interrupt__"][0].value.values():
                    print(f"- {question}")
                print(f"\nDevam: python refund_graph.py resume {session_id} "
                      "--days GÜN --used yes/no --accessories yes/no")
                return

            print(f"\nİyileştirilmiş prompt\n{result['prompt']}")
            print(f"\nGerekçe\n{result['reason']}")
            print("\nKaynaklar")
            for source in result["sources"]:
                print(f"- {source['source']} · {source['title']} · v{source['version']}")
            print(f"\nMüşteri yanıt taslağı\n{result['draft']}")
            print("\nOturum çağrıları")
            for operation, input_tokens, output_tokens, ms in db.execute(
                "SELECT operation, input_tokens, output_tokens, elapsed_ms "
                "FROM model_calls WHERE run_id = ? ORDER BY id", (session_id,),
            ):
                print(f"- {operation}: girdi={input_tokens}, çıktı={output_tokens}, {ms:.1f} ms")
    except (OSError, ValueError, KeyError, IndexError, TypeError, StopIteration, sqlite3.Error) as error:
        parser.exit(1, f"İade oturumu başarısız ({session_id}): {error}\n")


if __name__ == "__main__":
    main()
