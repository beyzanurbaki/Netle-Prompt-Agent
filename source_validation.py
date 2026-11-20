"""Match the pilot's policy sources to the sections reviewed for its rules."""

import hashlib

from rag import load_chunks


REVIEWED_SECTIONS = {
    "IADE-POL#P1": ("1.0", "1e5b8e5d388993302ee5c8f17ecc50dc0fcfabfc3eb98148e3b6ac12d8f01a51"),
    "IADE-POL#P2": ("1.0", "6de629efca05c9c5921bf1286ff7aff0b97a96a612cdb2c433237be9366e7e2f"),
    "IADE-POL#P3": ("1.0", "134d42c089b15f8073a5ef5fb21442f49c684f2bd2c4d0d56a417c79b162e984"),
    "DESTEK-REH#R1": ("1.0", "a8beba8578c2fc30e8100d893ed5b54320397352178c0fe945e6d52ae0cc4e90"),
    "DESTEK-REH#R2": ("1.0", "2b6de201e0cc11d28c69449864d80ab0d801b968f23b3d6d51f195ca2dd246f2"),
    "DESTEK-REH#R3": ("1.0", "d267b702074322e04ca93240aa996c6c59c5f410c5bc0cc2154efa101fe89643"),
}


def load_reviewed_sources(directory, required=()):
    try:
        chunks = load_chunks(directory)
    except ValueError as error:
        raise ValueError(f"Kaynaklar doğrulanamadı. Taslak oluşturulmadı. {error}") from error

    unknown = [chunk.source for chunk in chunks if chunk.source not in REVIEWED_SECTIONS]
    if unknown:
        raise ValueError(
            f"İncelenmemiş kaynak bulundu ({', '.join(unknown)}). "
            "Kaynaklar arasında çelişki olabileceği için taslak oluşturulmadı."
        )

    for chunk in chunks:
        version, expected = REVIEWED_SECTIONS[chunk.source]
        if chunk.version != version:
            raise ValueError(f"{chunk.source} sürümü incelenmemiş. Taslak oluşturulmadı.")
        actual = hashlib.sha256(f"{chunk.title}\n{chunk.text}".encode("utf-8")).hexdigest()
        if actual != expected:
            raise ValueError(
                f"{chunk.source} içeriği aynı sürümde değişmiş. "
                "Kaynak yeniden incelenene kadar taslak oluşturulmadı."
            )

    present = {chunk.source for chunk in chunks}
    missing = [source for source in required if source not in present]
    if missing:
        raise ValueError(f"Gerekli kaynak bulunamadı ({', '.join(missing)}). Taslak oluşturulmadı.")
    return chunks
