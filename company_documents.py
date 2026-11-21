"""Versioned company document library for administrator reviewed RAG sources."""

import base64
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from xml.etree import ElementTree


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 300_000
CREATE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS company_documents (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending','active','archived','rejected')),
    content_hash TEXT NOT NULL,
    text TEXT NOT NULL,
    file_bytes BLOB,
    uploaded_by INTEGER NOT NULL,
    reviewed_by INTEGER,
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS company_documents_title_status ON company_documents(title, status);
CREATE TABLE IF NOT EXISTS company_document_chunks (
    document_id INTEGER NOT NULL REFERENCES company_documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    heading TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY(document_id, ordinal)
);
"""


def setup(db):
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(CREATE_DOCUMENTS)
    columns = {row[1] for row in db.execute("PRAGMA table_info(company_documents)")}
    if "file_bytes" not in columns:
        db.execute("ALTER TABLE company_documents ADD COLUMN file_bytes BLOB")


@dataclass(frozen=True)
class Chunk:
    source: str
    version: str
    effective_date: str
    filename: str
    title: str
    text: str


def extract_text(filename, content):
    extension = PurePath(filename).suffix.lower()
    if extension in (".txt", ".md"):
        text = content.decode("utf-8-sig")
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                member = archive.getinfo("word/document.xml")
                if member.file_size > MAX_TEXT_CHARS * 8:
                    raise ValueError("DOCX dosyasının açılmış metin içeriği çok büyük.")
                root = ElementTree.fromstring(archive.read(member))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
            raise ValueError("DOCX dosyası okunamadı.") from error
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", ns):
            value = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
            if value:
                paragraphs.append(value)
        text = "\n\n".join(paragraphs)
    elif extension == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("Şifreli PDF dosyaları desteklenmiyor.")
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError as error:
            raise ValueError("PDF okumak için pypdf paketi kurulu değil. requirements.txt dosyasını yeniden kur.") from error
        except Exception as error:
            if isinstance(error, ValueError):
                raise
            raise ValueError("PDF metni okunamadı.") from error
    else:
        raise ValueError("Yalnızca PDF, DOCX, TXT ve MD dosyaları destekleniyor.")
    text = re.sub(r"[\t\r\f\v ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    if not text:
        raise ValueError("Dosyadan metin çıkarılamadı. Taranmış PDF yerine metin içeren bir dosya yükle.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Belge metni 300.000 karakter sınırını aşıyor.")
    return text


def split_chunks(text, limit=1400):
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        while len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            cut = paragraph.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 2 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def upload(db, title, filename, encoded, employee_id):
    title = title.strip()
    filename = PurePath(filename).name
    if not 2 <= len(title) <= 120 or len(title.splitlines()) != 1:
        raise ValueError("Belge başlığı 2–120 karakter ve tek satır olmalı.")
    if not filename or len(filename) > 180:
        raise ValueError("Dosya adı geçersiz.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Dosya aktarımı okunamadı.") from error
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("Dosya boş olamaz ve en fazla 8 MB olabilir.")
    text = extract_text(filename, content)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = db.execute(
        "SELECT id FROM company_documents WHERE title = ? COLLATE NOCASE "
        "AND status IN ('active','pending') AND content_hash = ? LIMIT 1", (title, content_hash),
    ).fetchone()
    if existing:
        raise ValueError("Bu başlıkta aynı içerik zaten etkin veya onay bekliyor.")
    with db:
        cursor = db.execute(
            "INSERT INTO company_documents (title,filename,status,content_hash,text,file_bytes,uploaded_by) "
            "VALUES (?,?, 'pending', ?, ?, ?, ?)",
            (title, filename, content_hash, text, content, employee_id),
        )
        document_id = cursor.lastrowid
        db.executemany(
            "INSERT INTO company_document_chunks (document_id,ordinal,heading,body) VALUES (?,?,?,?)",
            [(document_id, index, title, body)
             for index, body in enumerate(split_chunks(text), start=1)],
        )
    return document_id


def approve(db, document_id, reviewer_id):
    row = db.execute(
        "SELECT title,status,content_hash FROM company_documents WHERE id = ?", (document_id,),
    ).fetchone()
    if row is None or row[1] != "pending":
        raise ValueError("Onay bekleyen belge bulunamadı.")
    title, _, content_hash = row
    same = db.execute(
        "SELECT 1 FROM company_documents WHERE title = ? COLLATE NOCASE "
        "AND status = 'active' AND content_hash = ? LIMIT 1", (title, content_hash),
    ).fetchone()
    if same:
        raise ValueError("Aynı içerik zaten etkin. Bu yüklemeyi reddet veya sil.")
    current = db.execute(
        "SELECT COALESCE(MAX(version),0) FROM company_documents "
        "WHERE title = ? COLLATE NOCASE AND status IN ('active','archived')", (title,),
    ).fetchone()[0]
    with db:
        db.execute("UPDATE company_documents SET status='archived' WHERE title = ? COLLATE NOCASE AND status='active'", (title,))
        db.execute(
            "UPDATE company_documents SET status='active', version=?, reviewed_by=?, "
            "reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (current + 1, reviewer_id, document_id),
        )
    return current + 1


def reject(db, document_id, reviewer_id):
    with db:
        updated = db.execute(
            "UPDATE company_documents SET status='rejected', reviewed_by=?, "
            "reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (reviewer_id, document_id),
        )
    if not updated.rowcount:
        raise ValueError("Reddedilebilecek bekleyen belge bulunamadı.")


def list_documents(db):
    return [
        {"id": row[0], "title": row[1], "filename": row[2], "version": row[3],
         "status": row[4], "uploaded_at": row[5], "reviewed_at": row[6],
         "text": row[7], "has_active_version": bool(row[8]),
         "active_text_preview": row[9][:500] if row[9] else None}
        for row in db.execute(
            "SELECT d.id,d.title,d.filename,d.version,d.status,d.uploaded_at,d.reviewed_at,d.text, "
            "EXISTS(SELECT 1 FROM company_documents a WHERE a.title=d.title COLLATE NOCASE "
            "AND a.status='active'), (SELECT a.text FROM company_documents a "
            "WHERE a.title=d.title COLLATE NOCASE AND a.status='active' LIMIT 1) "
            "FROM company_documents d "
            "WHERE d.status IN ('pending','active','archived') ORDER BY d.title COLLATE NOCASE, d.id DESC"
        )
    ]


def active_chunks(db):
    chunks = []
    rows = db.execute(
        "SELECT d.id,d.version,d.uploaded_at,d.filename,d.title,c.ordinal,c.heading,c.body "
        "FROM company_documents d JOIN company_document_chunks c ON c.document_id=d.id "
        "WHERE d.status='active' ORDER BY d.id,c.ordinal"
    )
    for doc_id, version, date, filename, title, ordinal, heading, body in rows:
        chunks.append(Chunk(f"DOC-{doc_id}#{ordinal}", f"{version}", date, filename,
                            f"{title} · {heading}", body))
    return chunks


def retrieve(db, run_id, model, query, top_k=5):
    chunks = active_chunks(db)
    if not chunks:
        raise ValueError("Yönetici tarafından onaylanmış şirket belgesi bulunmuyor. Taslak oluşturulmadı.")
    from rag import embed, embed_documents, rank
    vectors = embed_documents(db, run_id, model, chunks)
    query_vector, = embed(db, run_id, model, [query], "embed_query")
    hits = rank(query_vector, chunks, vectors)[:top_k]
    if not hits or hits[0][1] < 0.18:
        raise ValueError("Onaylı belgelerde bu talebi yanıtlayacak bilgi bulunamadı. Taslak oluşturulmadı.")
    threshold = max(0.18, hits[0][1] - 0.18)
    return [chunk for chunk, score in hits if score >= threshold]
