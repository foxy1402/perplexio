import io
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pypdf import PdfReader

from app.models import Citation
from app.settings import (
    BACKUP_DIR,
    BACKUP_RETENTION_COUNT,
    DB_PATH,
    DATA_DIR,
    FILE_CONTEXT_FILE_COUNT,
    MAX_FILE_CONTEXT_CHARS,
    MAX_UPLOAD_SIZE_MB,
    UPLOAD_DIR,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                path TEXT NOT NULL,
                extracted_text TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER,
                created_at TEXT NOT NULL,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                citations_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_files (
                thread_id INTEGER NOT NULL,
                file_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (thread_id, file_id),
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            )
            """
        )
        cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(chats)").fetchall()}
        if "thread_id" not in cols:
            conn.execute("ALTER TABLE chats ADD COLUMN thread_id INTEGER")
        conn.execute("UPDATE chats SET thread_id = id WHERE thread_id IS NULL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chats_thread_id_id ON chats(thread_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_thread_files_thread_id ON thread_files(thread_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_chunks_file_id ON file_chunks(file_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.commit()


def extract_text_from_file(content: bytes, mime_type: str) -> str:
    def decode_text() -> str:
        return content.decode("utf-8", errors="ignore").strip()

    if mime_type in ("application/json", "application/ld+json"):
        raw = decode_text()
        try:
            obj = json.loads(raw)
            pretty = json.dumps(obj, ensure_ascii=True, indent=2)
            return "[JSON]\n" + pretty[:200000]
        except Exception:
            return raw
    if mime_type in ("text/csv", "application/csv"):
        raw = decode_text()
        rows = raw.splitlines()
        preview = "\n".join(rows[:60])
        return "[CSV]\n" + preview
    if mime_type in ("text/markdown", "text/x-markdown"):
        raw = decode_text()
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        headings = [ln.strip() for ln in lines if ln.lstrip().startswith("#")][:30]
        return "[MARKDOWN]\n" + ("\n".join(headings) + "\n\n" + raw[:200000]).strip()
    if mime_type.startswith("text/"):
        return decode_text()
    if mime_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        except Exception:
            return ""
    return ""


def normalize_file_ids(file_ids: list[int] | None) -> list[int]:
    if not file_ids:
        return []
    cleaned: list[int] = []
    seen: set[int] = set()
    for raw in file_ids:
        try:
            fid = int(raw)
        except (TypeError, ValueError):
            continue
        if fid <= 0 or fid in seen:
            continue
        seen.add(fid)
        cleaned.append(fid)
    return cleaned


def existing_file_ids(file_ids: list[int]) -> set[int]:
    if not file_ids:
        return set()
    placeholders = ",".join("?" for _ in file_ids)
    query = f"SELECT id FROM files WHERE id IN ({placeholders})"
    with get_db() as conn:
        rows = conn.execute(query, tuple(file_ids)).fetchall()
    return {int(r["id"]) for r in rows}


def validate_file_ids(file_ids: list[int] | None) -> list[int]:
    cleaned = normalize_file_ids(file_ids)
    found = existing_file_ids(cleaned)
    missing = [fid for fid in cleaned if fid not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown file ids: {missing}")
    return cleaned


def get_thread_file_ids(thread_id: int) -> list[int]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT file_id FROM thread_files WHERE thread_id = ? ORDER BY file_id ASC
            """,
            (thread_id,),
        ).fetchall()
    return [int(r["file_id"]) for r in rows]


def set_thread_file_ids(thread_id: int, file_ids: list[int]) -> list[int]:
    cleaned = validate_file_ids(file_ids)
    with get_db() as conn:
        conn.execute("DELETE FROM thread_files WHERE thread_id = ?", (thread_id,))
        for fid in cleaned:
            conn.execute(
                """
                INSERT INTO thread_files (thread_id, file_id, created_at) VALUES (?, ?, ?)
                """,
                (thread_id, fid, utc_now_iso()),
            )
        conn.commit()
    return cleaned


def build_file_context(file_ids: list[int] | None = None) -> tuple[str, list[Citation]]:
    selected = normalize_file_ids(file_ids) if file_ids is not None else None
    with get_db() as conn:
        if selected is None:
            rows = conn.execute(
                """
                SELECT id, original_name, extracted_text
                FROM files
                WHERE LENGTH(extracted_text) > 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (FILE_CONTEXT_FILE_COUNT,),
            ).fetchall()
        elif not selected:
            rows = []
        else:
            placeholders = ",".join("?" for _ in selected)
            rows = conn.execute(
                f"""
                SELECT id, original_name, extracted_text
                FROM files
                WHERE LENGTH(extracted_text) > 0
                  AND id IN ({placeholders})
                """,
                tuple(selected),
            ).fetchall()
            by_id = {int(r["id"]): r for r in rows}
            rows = [by_id[fid] for fid in selected if fid in by_id]

    chunks: list[str] = []
    citations: list[Citation] = []
    for idx, row in enumerate(rows, start=1):
        extracted = str(row["extracted_text"])
        trimmed = extracted[:MAX_FILE_CONTEXT_CHARS]
        file_id = int(row["id"])
        name = str(row["original_name"])
        citations.append(
            Citation(
                title=f"Uploaded file: {name}",
                url=f"/api/files/{file_id}/download",
                snippet=trimmed[:400],
            )
        )
        chunks.append(f"[F{idx}] File: {name}\nContent:\n{trimmed}")
    return "\n\n".join(chunks), citations


def save_chat(
    query: str, answer: str, citations: list[Citation], thread_id: int | None
) -> tuple[int, int]:
    citations_json = json.dumps([c.model_dump() for c in citations], ensure_ascii=True)
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO chats (thread_id, created_at, query, answer, citations_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (thread_id, utc_now_iso(), query, answer, citations_json),
        )
        chat_id = int(cur.lastrowid)
        resolved_thread_id = thread_id or chat_id
        if thread_id is None:
            conn.execute(
                "UPDATE chats SET thread_id = ? WHERE id = ?",
                (resolved_thread_id, chat_id),
            )
        conn.commit()
        return chat_id, resolved_thread_id


def get_thread_history(thread_id: int, turns: int) -> list[dict[str, str]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT query, answer FROM chats WHERE thread_id = ? ORDER BY id DESC LIMIT ?
            """,
            (thread_id, max(1, turns)),
        ).fetchall()
    ordered = list(reversed(rows))
    return [{"query": str(r["query"]), "answer": str(r["answer"])} for r in ordered]


def save_uploaded_file(filename: str, mime_type: str, content: bytes) -> dict:
    size_bytes = len(content)
    max_size_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if size_bytes > max_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds MAX_UPLOAD_SIZE_MB={MAX_UPLOAD_SIZE_MB}.",
        )

    original_name = filename or "upload.bin"
    safe_name = Path(original_name).name
    stored_name = f"{uuid4().hex}_{safe_name}"
    file_path = UPLOAD_DIR / stored_name
    file_path.write_bytes(content)

    extracted_text = extract_text_from_file(content, mime_type)
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO files (
              created_at, original_name, stored_name, mime_type, size_bytes, path, extracted_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                original_name,
                stored_name,
                mime_type,
                size_bytes,
                str(file_path),
                extracted_text,
            ),
        )
        conn.commit()
        file_id = int(cur.lastrowid)
    return {
        "file_id": file_id,
        "filename": original_name,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "extracted_text_chars": len(extracted_text),
    }


def get_file_text(file_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, original_name, extracted_text
            FROM files
            WHERE id = ?
            """,
            (file_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "original_name": str(row["original_name"]),
        "extracted_text": str(row["extracted_text"]),
    }


def replace_file_chunks(file_id: int, chunks: list[dict]) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM file_chunks WHERE file_id = ?", (file_id,))
        for idx, chunk in enumerate(chunks):
            conn.execute(
                """
                INSERT INTO file_chunks (file_id, chunk_index, content, embedding_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    idx,
                    str(chunk["content"]),
                    json.dumps(chunk["embedding"], ensure_ascii=True),
                    utc_now_iso(),
                ),
            )
        conn.commit()


def list_file_chunks(file_ids: list[int] | None, limit: int) -> list[dict]:
    safe_limit = max(1, int(limit))
    with get_db() as conn:
        if file_ids is None:
            rows = conn.execute(
                """
                SELECT c.file_id, c.chunk_index, c.content, c.embedding_json, f.original_name
                FROM file_chunks c
                JOIN files f ON f.id = c.file_id
                ORDER BY c.id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        elif not file_ids:
            rows = []
        else:
            placeholders = ",".join("?" for _ in file_ids)
            rows = conn.execute(
                f"""
                SELECT c.file_id, c.chunk_index, c.content, c.embedding_json, f.original_name
                FROM file_chunks c
                JOIN files f ON f.id = c.file_id
                WHERE c.file_id IN ({placeholders})
                ORDER BY c.id DESC
                LIMIT ?
                """,
                (*file_ids, safe_limit),
            ).fetchall()
    return [
        {
            "file_id": int(r["file_id"]),
            "chunk_index": int(r["chunk_index"]),
            "content": str(r["content"]),
            "embedding_json": str(r["embedding_json"]),
            "original_name": str(r["original_name"]),
        }
        for r in rows
    ]


def update_file_extracted_text(file_id: int, extracted_text: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE files
            SET extracted_text = ?
            WHERE id = ?
            """,
            (extracted_text, file_id),
        )
        conn.commit()


def get_file_record(file_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, original_name, mime_type, path, extracted_text
            FROM files
            WHERE id = ?
            """,
            (file_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "original_name": str(row["original_name"]),
        "mime_type": str(row["mime_type"]),
        "path": str(row["path"]),
        "extracted_text": str(row["extracted_text"]),
    }


def list_file_ids(limit: int | None = None) -> list[int]:
    with get_db() as conn:
        if limit is None:
            rows = conn.execute("SELECT id FROM files ORDER BY id ASC").fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM files ORDER BY id ASC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
    return [int(r["id"]) for r in rows]


def purge_all_data() -> dict:
    deleted_upload_count = 0
    with get_db() as conn:
        file_rows = conn.execute("SELECT path FROM files").fetchall()
        deleted_chat_count = int(
            conn.execute("SELECT COUNT(1) AS c FROM chats").fetchone()["c"]
        )
        deleted_file_count = int(
            conn.execute("SELECT COUNT(1) AS c FROM files").fetchone()["c"]
        )
        deleted_chunk_count = int(
            conn.execute("SELECT COUNT(1) AS c FROM file_chunks").fetchone()["c"]
        )

        conn.execute("DELETE FROM thread_files")
        conn.execute("DELETE FROM chats")
        conn.execute("DELETE FROM file_chunks")
        conn.execute("DELETE FROM files")
        conn.commit()

    for row in file_rows:
        p = Path(str(row["path"]))
        try:
            if p.exists():
                p.unlink()
                deleted_upload_count += 1
        except Exception:
            continue

    return {
        "deleted_chat_count": deleted_chat_count,
        "deleted_file_count": deleted_file_count,
        "deleted_chunk_count": deleted_chunk_count,
        "deleted_upload_count": deleted_upload_count,
    }


def create_job(job_type: str, payload: dict | None = None, total: int = 0) -> int:
    now = utc_now_iso()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (created_at, updated_at, job_type, status, progress, total, completed, message, payload_json)
            VALUES (?, ?, ?, 'queued', 0, ?, 0, '', ?)
            """,
            (now, now, job_type, max(0, int(total)), json.dumps(payload or {}, ensure_ascii=True)),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_job(
    job_id: int,
    *,
    status: str | None = None,
    progress: float | None = None,
    total: int | None = None,
    completed: int | None = None,
    message: str | None = None,
) -> None:
    sets = ["updated_at = ?"]
    vals: list = [utc_now_iso()]
    if status is not None:
        sets.append("status = ?")
        vals.append(status)
    if progress is not None:
        sets.append("progress = ?")
        vals.append(float(max(0.0, min(1.0, progress))))
    if total is not None:
        sets.append("total = ?")
        vals.append(int(max(0, total)))
    if completed is not None:
        sets.append("completed = ?")
        vals.append(int(max(0, completed)))
    if message is not None:
        sets.append("message = ?")
        vals.append(message)
    vals.append(job_id)
    with get_db() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", tuple(vals))
        conn.commit()


def get_job(job_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def list_jobs(limit: int = 50) -> list[dict]:
    safe = min(max(1, int(limit)), 500)
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (safe,)).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def create_sqlite_backup_file() -> str:
    fd, out_path = tempfile.mkstemp(prefix="perplexio-backup-", suffix=".db")
    os.close(fd)
    Path(out_path).unlink(missing_ok=True)
    source = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(out_path)
    try:
        source.backup(dest)
        dest.commit()
    finally:
        source.close()
        dest.close()
    return out_path


def _sanitize_backup_name(name: str) -> str:
    base = os.path.basename(name.strip())
    if not base.endswith(".db"):
        raise HTTPException(status_code=400, detail="Backup file must end with .db")
    if not re_match_backup_name(base):
        raise HTTPException(status_code=400, detail="Invalid backup file name")
    return base


def re_match_backup_name(name: str) -> bool:
    import re

    return bool(re.match(r"^perplexio-backup-\d{8}-\d{6}\.db$", name))


def create_persistent_backup() -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = BACKUP_DIR / f"perplexio-backup-{stamp}.db"
    source = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(str(out_path))
    try:
        source.backup(dest)
        dest.commit()
    finally:
        source.close()
        dest.close()
    prune_old_backups(BACKUP_RETENTION_COUNT)
    stat = out_path.stat()
    return {
        "name": out_path.name,
        "path": str(out_path),
        "size_bytes": int(stat.st_size),
        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in BACKUP_DIR.glob("perplexio-backup-*.db"):
        try:
            stat = p.stat()
        except Exception:
            continue
        items.append(
            {
                "name": p.name,
                "path": str(p),
                "size_bytes": int(stat.st_size),
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


def prune_old_backups(retention_count: int) -> int:
    keep = max(1, int(retention_count))
    items = list_backups()
    removed = 0
    for item in items[keep:]:
        try:
            Path(str(item["path"])).unlink(missing_ok=True)
            removed += 1
        except Exception:
            continue
    return removed


def restore_backup(name: str) -> dict:
    safe = _sanitize_backup_name(name)
    src = BACKUP_DIR / safe
    if not src.exists():
        raise HTTPException(status_code=404, detail="Backup not found.")
    # Take a safety snapshot before restore.
    pre = create_persistent_backup()
    # Use sqlite3.backup() for atomic restore — safe even with active connections.
    source = sqlite3.connect(str(src))
    dest = sqlite3.connect(DB_PATH)
    try:
        source.backup(dest)
        dest.commit()
    finally:
        source.close()
        dest.close()
    # Reinitialize storage to pick up any schema changes in the restored DB.
    init_storage()
    return {"restored_from": safe, "pre_restore_backup": pre.get("name", "")}


def get_backup_path(name: str) -> str:
    safe = _sanitize_backup_name(name)
    path = BACKUP_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found.")
    return str(path)
