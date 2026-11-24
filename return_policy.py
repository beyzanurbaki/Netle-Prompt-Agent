"""Read supported standard-return rules from active policy passages."""

import re
from dataclasses import asdict, dataclass

from company_documents import active_chunks


@dataclass(frozen=True)
class ReturnRules:
    send_days: int
    refund_days: int
    pickup_exception: bool


SEND = re.compile(
    r"cayma bildiriminizi ilettiğiniz tarihten itibaren (\d+) gün içinde geri gönderin",
    re.IGNORECASE,
)
REFUND = re.compile(
    r"ön bilgilendirmede belirtilen taşıyıcıya verildiyse (\d+) günlük geri ödeme süresi "
    r"taşıyıcıya teslim tarihinde başlar\. farklı taşıyıcı kullanıldıysa süre ürünün "
    r"[^.]+? ulaştığı tarihte",
    re.IGNORECASE,
)


def read_return_rules(db):
    by_document = {}
    for chunk in active_chunks(db):
        text = " ".join(chunk.text.split())
        document_id = chunk.source.partition("#")[0]
        sends, refunds, sources = by_document.setdefault(document_id, (set(), set(), []))
        send_matches = {int(match[1]) for match in SEND.finditer(text)}
        refund_matches = {int(match[1]) for match in REFUND.finditer(text)}
        if send_matches or refund_matches:
            sources.append(asdict(chunk))
        sends.update(send_matches)
        refunds.update(refund_matches)

    if not by_document:
        return None, []
    send_days = set().union(*(values[0] for values in by_document.values()))
    refund_days = set().union(*(values[1] for values in by_document.values()))
    if len(send_days) != 1 or len(refund_days) != 1 or 0 in send_days | refund_days:
        return None, []
    for sends, refunds, sources in by_document.values():
        if sends == send_days and refunds == refund_days:
            pickup = any("kendisi teslim almayı teklif ederse sizin gönderim yapmanız gerekmez"
                         in " ".join(source["text"].lower().split()) for source in sources)
            return ReturnRules(next(iter(sends)), next(iter(refunds)), pickup), sources
    return None, []


def return_instructions(rules, carrier=None):
    steps = [
        f"Ürünü, cayma bildiriminizi ilettiğiniz tarihten itibaren {rules.send_days} gün içinde geri gönderin."
    ]
    if rules.pickup_exception:
        steps.append("Ürünü biz teslim almayı teklif edersek kargoya vermeniz gerekmez.")
    if carrier != "different":
        steps.append(
            "Ön bilgilendirmede belirtilen taşıyıcıyı kullanırsanız "
            f"{rules.refund_days} günlük geri ödeme süresi ürünü taşıyıcıya teslim ettiğiniz tarihte başlar."
        )
    if carrier != "designated":
        steps.append(
            f"Farklı bir taşıyıcı kullanırsanız {rules.refund_days} günlük geri ödeme süresi "
            "ürünün bize ulaştığı tarihte başlar."
        )
    return " ".join(steps)
