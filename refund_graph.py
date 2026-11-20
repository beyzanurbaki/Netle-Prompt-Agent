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
from rag import DATA_DIR, embed, embed_documents, rank, scenario_query
from refund_demo import chat
from source_validation import load_reviewed_sources


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
    "hijyen_muhru_acik": "Ürünün hijyen mührü açıldı mı?",
    "teslimatta_hasarli": "Ürün müşteriye hasarlı olarak mı ulaştı?",
    "siparis_no_var": "Sipariş numarası elinizde mi?",
    "urun_fotografi_var": "Hasarlı ürünü gösteren fotoğraf elinizde mi?",
    "ambalaj_fotografi_var": "Dış ambalajı gösteren fotoğraf elinizde mi?",
}
STANDARD_FIELDS = ("teslimattan_sonra_gun", "kullanildi", "aksesuarlar_tam")
DAMAGE_FIELDS = ("siparis_no_var", "urun_fotografi_var", "ambalaj_fotografi_var")
STANDARD_SOURCES = ("IADE-POL#P1", "DESTEK-REH#R1")
STANDARD_RETURN_DAYS = 30


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
    flow: str


def missing_fields(state):
    facts = state["facts"]
    flow = state.get("flow", "standard")
    if flow == "damage":
        fields = DAMAGE_FIELDS
    elif flow == "hygiene" and facts.get("hijyen_muhru_acik") is not False:
        fields = ("hijyen_muhru_acik",)
    else:
        fields = STANDARD_FIELDS
    return [field for field in fields if facts.get(field) is None]


def clarify(state):
    fields = missing_fields(state)
    answers = interrupt({field: QUESTIONS[field] for field in fields})
    return {"facts": {**state["facts"], **{field: answers[field] for field in fields}}}


def confirm_damage(state):
    if not interrupt({"teslimatta_hasarli": QUESTIONS["teslimatta_hasarli"]})["teslimatta_hasarli"]:
        raise ValueError("Teslimat sonrasında oluşan hasar için pilot belgelerde bir iade kuralı bulunmuyor.")
    return {"flow": "damage"}


def special_prompt(state):
    facts = state["facts"]
    if state.get("flow") == "damage":
        items = ("sipariş numaranızı", "hasarlı ürünü gösteren fotoğrafı",
                 "dış ambalajı gösteren fotoğrafı")
        missing = [item for field, item in zip(DAMAGE_FIELDS, items) if not facts[field]]
        request = f" İnceleyebilmemiz için {', '.join(missing)} paylaşabilir misiniz?" if missing else (
            " Paylaştığınız sipariş bilgisi ve fotoğraflarla talebinizi inceleyebiliriz."
        )
        reason = "Hasarlı teslimat, sipariş numarası ve fotoğraflarla incelemeye yönlendirilir."
        example = f"Merhaba, ürününüzün hasarlı ulaşmasına üzüldük.{request}"
        guidance = "Yalnızca eksik kanıtı iste. İade veya değişim onayı ya da kesin süre verme."
    else:
        reason = "Hijyen mühürlü ürünün mührü açıldığı için standart iade yapılamaz."
        example = "Merhaba, ürününüzün hijyen mührü açıldığı için standart iade talebi oluşturulamıyor."
        guidance = "Standart iade uygunluğunu müşteriye nazikçe açıkla."
    return {"prompt": f"{reason} {guidance} Yalnızca kısa müşteri mesajını yaz. Örnek: {example}",
            "reason": reason, "reviewed_message": example}


def build_prompt(state):
    facts = state["facts"]
    if state.get("flow") == "damage" or facts.get("hijyen_muhru_acik") is True:
        return special_prompt(state)
    days = facts.get("teslimattan_sonra_gun")
    # This interpretation is specific to the reviewed version of the demo policy.
    eligibility = {
        "30 günlük sürenin aşılması": days is not None and days > STANDARD_RETURN_DAYS,
        "ürünün kullanılmış olması": facts.get("kullanildi") is True,
        "aksesuarların eksik olması": facts.get("aksesuarlar_tam") is False,
    }
    unmet = [reason for reason, applies in eligibility.items() if applies]
    if unmet:
        reason = f"Standart iade koşullarından karşılanmayanlar: {', '.join(unmet)}."
        guidance = "Standart iade koşullarının neden karşılanmadığını nazikçe açıkla."
        example = (
            "Merhaba, iade talebinizi inceledik. Şu nedenle standart iade koşulları "
            f"karşılanmıyor: {', '.join(unmet)}. Anlayışınız için teşekkür ederiz."
        )
    elif fields := missing_fields(state):
        missing = {
            "teslimattan_sonra_gun": "ürünü hangi tarihte teslim aldığınızı",
            "kullanildi": "ürünü kullanıp kullanmadığınızı",
            "aksesuarlar_tam": "aksesuarların eksiksiz olup olmadığını",
        }
        reason = "Standart iade uygunluğunu değerlendirmek için bilgi eksik."
        guidance = "Sadece eksik bilgileri iste ve iade kararı verme."
        details = ", ".join(missing[field] for field in fields)
        example = f"Merhaba, iade talebinizi değerlendirebilmemiz için {details} paylaşabilir misiniz?"
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
        if days == STANDARD_RETURN_DAYS:
            guidance += " Bugünün iade süresinin son günü olduğunu ve bugün başvurabileceğini belirt."
            example = (
                "Merhaba, bugün iade süresinin son günü. "
                "İade talebinizi bugün Hesabım → Siparişlerim → İade Talebi üzerinden oluşturabilirsiniz."
            )
    prompt = (
        f"{facts.get('urun') or 'İade edilen ürün'} için müşteriye kısa ve nazik bir Türkçe mesaj taslağı yaz. "
        f"{reason} {guidance} "
        f"Müşteriye yalnızca mesajı göster. Yanıt örneğinin üslubunu ve kısalığını koru: {example}"
    )
    return {"prompt": prompt, "reason": reason, "reviewed_message": example}


def required_sources(state):
    flow = state.get("flow", "standard")
    if flow == "damage":
        return ("IADE-POL#P3", "DESTEK-REH#R2")
    if flow == "hygiene":
        hygiene = ("IADE-POL#P2",)
        return hygiene + STANDARD_SOURCES if state["facts"].get("hijyen_muhru_acik") is False else hygiene
    return STANDARD_SOURCES


def make_graph(db, saver):
    def retrieve(state):
        required = required_sources(state)
        chunks = load_reviewed_sources(DATA_DIR / "belgeler", required)
        session_id, model = state["session_id"], state["embed_model"]
        vectors = embed_documents(db, session_id, model, chunks)
        query, = embed(db, session_id, model, [
            scenario_query(state["scenario"], clarified=False)
        ], "embed_query")
        hits = {chunk.source: chunk for chunk, _ in rank(query, chunks, vectors)}
        return {"sources": [asdict(hits[source]) for source in required]}

    def generate(state):
        load_reviewed_sources(DATA_DIR / "belgeler", required_sources(state))
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
    graph.add_node("confirm_damage", confirm_damage)
    graph.add_node("clarify_seal", clarify)
    graph.add_node("retrieve", retrieve)
    graph.add_node("clarify", clarify)
    graph.add_node("build_prompt", build_prompt)
    graph.add_node("generate", generate)
    def first_step(state):
        if state.get("flow") == "possible_damage":
            return "confirm_damage"
        if state.get("flow") == "hygiene" and state["facts"].get("hijyen_muhru_acik") is None:
            return "clarify_seal"
        return "retrieve"

    graph.add_conditional_edges(START, first_step,
                                ["confirm_damage", "clarify_seal", "retrieve"])
    graph.add_edge("confirm_damage", "retrieve")
    graph.add_edge("clarify_seal", "retrieve")
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
