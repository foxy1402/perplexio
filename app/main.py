import json
import html
import hashlib
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.background import BackgroundTask

from app.auth import (
    auth_enabled,
    is_authenticated_request,
    login_success_response,
    logout_response,
    unauthorized_response,
    verify_password,
)
from app.models import (
    AskRequest,
    AskResponse,
    BackupItem,
    BackupRestoreRequest,
    BackupRestoreResponse,
    ChatDetail,
    ChatItem,
    FileItem,
    FollowupsResponse,
    LoginRequest,
    LoginResponse,
    JobItem,
    PurgeRequest,
    PurgeResponse,
    ReindexRequest,
    ReindexStartResponse,
    ReindexResponse,
    ThreadDetail,
    ThreadFilesRequest,
    ThreadFilesResponse,
    UploadResponse,
)
from app.pwa_assets import manifest_json, service_worker_js
from app.services import (
    admin_reindex_files,
    align_answer_citations,
    compute_answer_confidence,
    ask_model,
    ask_model_stream,
    get_job_item,
    index_file_for_retrieval,
    list_job_items,
    prepare_ask,
    run_file_index_job,
    run_reindex_job,
    start_file_index_job,
    start_reindex_job,
    suggest_followups,
)
from app.settings import SEARXNG_RESULT_COUNT
from app.settings import ASK_CACHE_MAX_ITEMS, ASK_CACHE_TTL_SECONDS
from app.storage import (
    create_persistent_backup,
    get_backup_path,
    list_backups,
    restore_backup,
    create_sqlite_backup_file,
    get_db,
    get_thread_file_ids,
    init_storage,
    purge_all_data,
    save_chat,
    save_uploaded_file,
    set_thread_file_ids,
    validate_file_ids,
)
from app.ui_html import INDEX_HTML


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_storage()
    yield


app = FastAPI(
    title="Perplexio",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
PWA_ICON_PATH = Path(__file__).resolve().parent.parent / "media" / "perplexio.png"
METRICS = {
    "started_at_unix": int(time.time()),
    "requests_total": 0,
    "requests_by_path": {},
    "errors_5xx": 0,
    "latency_ms_sum": 0.0,
}
ASK_CACHE: dict[str, tuple[float, dict]] = {}


def _safe_unlink(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _cache_key(payload: AskRequest) -> str | None:
    # Avoid caching thread-aware prompts to reduce stale context risks.
    if payload.thread_id is not None:
        return None
    key_obj = {
        "query": payload.query.strip(),
        "top_k": payload.top_k,
        "include_files": payload.include_files,
        "file_ids": payload.file_ids or [],
        "search_mode": payload.search_mode,
    }
    raw = json.dumps(key_obj, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict | None:
    now = time.time()
    item = ASK_CACHE.get(key)
    if item is None:
        return None
    ts, value = item
    if (now - ts) > max(1, ASK_CACHE_TTL_SECONDS):
        ASK_CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: dict) -> None:
    ASK_CACHE[key] = (time.time(), value)
    if len(ASK_CACHE) > max(1, ASK_CACHE_MAX_ITEMS):
        # Evict oldest entries.
        oldest = sorted(ASK_CACHE.items(), key=lambda kv: kv[1][0])[: max(1, len(ASK_CACHE) - ASK_CACHE_MAX_ITEMS)]
        for k, _v in oldest:
            ASK_CACHE.pop(k, None)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    started = time.perf_counter()
    METRICS["requests_total"] = int(METRICS["requests_total"]) + 1
    path = request.url.path or "/"
    by_path = METRICS["requests_by_path"]
    by_path[path] = int(by_path.get(path, 0)) + 1
    if not auth_enabled():
        response = await call_next(request)
    else:
        if path.startswith("/auth/") or path == "/health" or path == "/":
            response = await call_next(request)
        elif path.startswith("/api/") and not is_authenticated_request(request):
            response = unauthorized_response()
        else:
            response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    METRICS["latency_ms_sum"] = float(METRICS["latency_ms_sum"]) + elapsed_ms
    if int(response.status_code) >= 500:
        METRICS["errors_5xx"] = int(METRICS["errors_5xx"]) + 1
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/me")
async def auth_me(request: Request) -> dict:
    return {
        "authenticated": is_authenticated_request(request),
        "auth_enabled": auth_enabled(),
    }


@app.post("/auth/login", response_model=LoginResponse)
async def auth_login(payload: LoginRequest) -> JSONResponse:
    if auth_enabled() and not verify_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    return login_success_response(auth_enabled())


@app.post("/auth/logout", response_model=LoginResponse)
async def auth_logout() -> JSONResponse:
    return logout_response()


@app.post("/api/files/upload", response_model=UploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    async_index: bool = True,
) -> UploadResponse:
    content = await file.read()
    payload = save_uploaded_file(
        filename=file.filename or "upload.bin",
        mime_type=file.content_type or "application/octet-stream",
        content=content,
    )
    if async_index:
        job_id = start_file_index_job(int(payload["file_id"]))
        background_tasks.add_task(run_file_index_job, job_id, int(payload["file_id"]))
        payload["job_id"] = job_id
    else:
        try:
            await index_file_for_retrieval(int(payload["file_id"]))
        except Exception:
            pass
    ASK_CACHE.clear()
    return UploadResponse(**payload)


@app.get("/api/files", response_model=list[FileItem])
async def list_files(limit: int = 50) -> list[FileItem]:
    safe_limit = min(max(limit, 1), 200)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, original_name, mime_type, size_bytes, LENGTH(extracted_text) AS extracted_text_chars
            FROM files
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [
        FileItem(
            id=int(r["id"]),
            created_at=str(r["created_at"]),
            original_name=str(r["original_name"]),
            mime_type=str(r["mime_type"]),
            size_bytes=int(r["size_bytes"]),
            extracted_text_chars=int(r["extracted_text_chars"]),
        )
        for r in rows
    ]


@app.get("/api/files/{file_id}/download")
async def download_file(file_id: int) -> FileResponse:
    with get_db() as conn:
        row = conn.execute(
            "SELECT original_name, mime_type, path FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    file_path = Path(str(row["path"]))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk.")
    return FileResponse(
        path=file_path,
        filename=str(row["original_name"]),
        media_type=str(row["mime_type"]),
    )


@app.get("/api/chats", response_model=list[ChatItem])
async def list_chats(limit: int = 50) -> list[ChatItem]:
    safe_limit = min(max(limit, 1), 200)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.thread_id, c.created_at, c.query, c.answer
            FROM chats c
            INNER JOIN (
                SELECT thread_id, MAX(id) AS max_id
                FROM chats
                GROUP BY thread_id
                ORDER BY max_id DESC
                LIMIT ?
            ) latest ON latest.max_id = c.id
            ORDER BY c.id DESC
            """,
            (safe_limit,),
        ).fetchall()
    return [
        ChatItem(
            id=int(r["id"]),
            thread_id=int(r["thread_id"]),
            created_at=str(r["created_at"]),
            query=str(r["query"]),
            answer=str(r["answer"]),
        )
        for r in rows
    ]


@app.get("/api/threads/{thread_id}", response_model=ThreadDetail)
async def get_thread(thread_id: int) -> ThreadDetail:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, thread_id, created_at, query, answer, citations_json
            FROM chats
            WHERE thread_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (thread_id, 200),
        ).fetchall()
    chats: list[ChatDetail] = []
    for row in rows:
        raw_citations = json.loads(str(row["citations_json"]))
        chats.append(
            ChatDetail(
                id=int(row["id"]),
                thread_id=int(row["thread_id"]),
                created_at=str(row["created_at"]),
                query=str(row["query"]),
                answer=str(row["answer"]),
                citations=raw_citations,
            )
        )
    if not chats:
        raise HTTPException(status_code=404, detail="Thread not found.")
    return ThreadDetail(
        thread_id=thread_id,
        chats=chats,
        attached_file_ids=get_thread_file_ids(thread_id),
    )


@app.get("/api/threads/{thread_id}/files", response_model=ThreadFilesResponse)
async def get_thread_files(thread_id: int) -> ThreadFilesResponse:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 AS ok FROM chats WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Thread not found.")
    return ThreadFilesResponse(thread_id=thread_id, file_ids=get_thread_file_ids(thread_id))


@app.put("/api/threads/{thread_id}/files", response_model=ThreadFilesResponse)
async def put_thread_files(thread_id: int, payload: ThreadFilesRequest) -> ThreadFilesResponse:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 AS ok FROM chats WHERE thread_id = ? LIMIT 1",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Thread not found.")
    file_ids = set_thread_file_ids(thread_id, payload.file_ids)
    return ThreadFilesResponse(thread_id=thread_id, file_ids=file_ids)


@app.get("/api/chats/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: int) -> ChatDetail:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, thread_id, created_at, query, answer, citations_json
            FROM chats
            WHERE id = ?
            """,
            (chat_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return ChatDetail(
        id=int(row["id"]),
        thread_id=int(row["thread_id"]),
        created_at=str(row["created_at"]),
        query=str(row["query"]),
        answer=str(row["answer"]),
        citations=json.loads(str(row["citations_json"])),
    )


@app.get("/api/chats/{chat_id}/followups", response_model=FollowupsResponse)
async def get_chat_followups(chat_id: int, limit: int = 4) -> FollowupsResponse:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT query, answer, citations_json
            FROM chats
            WHERE id = ?
            """,
            (chat_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Chat not found.")
    citations = json.loads(str(row["citations_json"]))
    cites = citations if isinstance(citations, list) else []
    suggestions = await suggest_followups(
        question=str(row["query"]),
        answer=str(row["answer"]),
        citations=cites,
        max_items=min(max(limit, 1), 8),
    )
    return FollowupsResponse(chat_id=chat_id, suggestions=suggestions)


@app.get("/api/threads/{thread_id}/export")
async def export_thread(thread_id: int, format: str = "markdown"):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, query, answer, citations_json
            FROM chats
            WHERE thread_id = ?
            ORDER BY id ASC
            """,
            (thread_id,),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="Thread not found.")
    fmt = format.lower().strip()
    if fmt == "json":
        return {
            "thread_id": thread_id,
            "items": [
                {
                    "chat_id": int(r["id"]),
                    "created_at": str(r["created_at"]),
                    "query": str(r["query"]),
                    "answer": str(r["answer"]),
                    "citations": json.loads(str(r["citations_json"])),
                }
                for r in rows
            ],
        }
    lines = [f"# Thread {thread_id}", ""]
    for r in rows:
        lines.append(f"## Q{int(r['id'])} - {str(r['created_at'])}")
        lines.append(str(r["query"]))
        lines.append("")
        lines.append("### Answer")
        lines.append(str(r["answer"]))
        lines.append("")
        cites = json.loads(str(r["citations_json"]))
        if isinstance(cites, list) and cites:
            lines.append("### Sources")
            for i, c in enumerate(cites, start=1):
                title = str(c.get("title", ""))
                url = str(c.get("url", ""))
                lines.append(f"{i}. [{title}]({url})")
            lines.append("")
    md = "\n".join(lines)
    return HTMLResponse(content=f"<pre>{html.escape(md)}</pre>")


@app.post("/api/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    validated_file_ids: list[int] | None = None
    if payload.file_ids is not None:
        validated_file_ids = validate_file_ids(payload.file_ids)

    cache_key = _cache_key(payload)
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return AskResponse(**cached)

    top_k = payload.top_k or SEARXNG_RESULT_COUNT
    messages, citations = await prepare_ask(
        query=payload.query,
        top_k=top_k,
        include_files=payload.include_files,
        thread_id=payload.thread_id,
        file_ids=validated_file_ids,
        search_mode=payload.search_mode,
    )
    raw_answer = await ask_model(messages)
    answer = await align_answer_citations(raw_answer, citations)
    confidence, abstain = compute_answer_confidence(answer, citations)
    if abstain:
        answer = (
            "Evidence is limited or conflicting. Treat this as uncertain and verify sources.\n\n"
            + answer
        )
    answer = answer + f"\n\n(Confidence: {confidence:.2f})"
    chat_id, thread_id = save_chat(
        payload.query, answer, citations, thread_id=payload.thread_id
    )
    if validated_file_ids is not None:
        set_thread_file_ids(thread_id, validated_file_ids)
    out = AskResponse(
        answer=answer, citations=citations, chat_id=chat_id, thread_id=thread_id
    )
    if cache_key:
        _cache_set(cache_key, out.model_dump())
    return out


@app.post("/api/ask/stream")
async def ask_stream(payload: AskRequest) -> StreamingResponse:
    validated_file_ids: list[int] | None = None
    if payload.file_ids is not None:
        validated_file_ids = validate_file_ids(payload.file_ids)

    cache_key = _cache_key(payload)
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            cached_answer = str(cached.get("answer", ""))
            cached_citations = cached.get("citations", [])
            cached_chat_id = int(cached.get("chat_id", 0))
            cached_thread_id = int(cached.get("thread_id", 0))

            async def cached_stream():
                yield f"event: meta\ndata: {json.dumps({'citations': cached_citations}, ensure_ascii=True)}\n\n".encode(
                    "utf-8"
                )
                yield f"event: token\ndata: {json.dumps({'delta': cached_answer}, ensure_ascii=True)}\n\n".encode(
                    "utf-8"
                )
                yield f"event: done\ndata: {json.dumps({'chat_id': cached_chat_id, 'thread_id': cached_thread_id}, ensure_ascii=True)}\n\n".encode(
                    "utf-8"
                )

            return StreamingResponse(
                cached_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

    top_k = payload.top_k or SEARXNG_RESULT_COUNT
    messages, citations = await prepare_ask(
        query=payload.query,
        top_k=top_k,
        include_files=payload.include_files,
        thread_id=payload.thread_id,
        file_ids=validated_file_ids,
        search_mode=payload.search_mode,
    )

    async def event_stream() -> AsyncIterator[bytes]:
        all_text: list[str] = []
        meta = {"citations": [c.model_dump() for c in citations]}
        yield f"event: meta\ndata: {json.dumps(meta, ensure_ascii=True)}\n\n".encode(
            "utf-8"
        )
        try:
            async for token in ask_model_stream(messages):
                all_text.append(token)
                payload_token = {"delta": token}
                yield (
                    f"event: token\ndata: {json.dumps(payload_token, ensure_ascii=True)}\n\n".encode(
                        "utf-8"
                    )
                )
            answer = "".join(all_text).strip()
            if not answer:
                raise RuntimeError("LLM returned empty stream content.")
            aligned_answer = await align_answer_citations(answer, citations)
            confidence, abstain = compute_answer_confidence(aligned_answer, citations)
            if abstain:
                aligned_answer = (
                    "Evidence is limited or conflicting. Treat this as uncertain and verify sources.\n\n"
                    + aligned_answer
                )
            aligned_answer = aligned_answer + f"\n\n(Confidence: {confidence:.2f})"
            chat_id, thread_id = save_chat(
                payload.query, aligned_answer, citations, thread_id=payload.thread_id
            )
            if validated_file_ids is not None:
                set_thread_file_ids(thread_id, validated_file_ids)
            if cache_key:
                _cache_set(
                    cache_key,
                    {
                        "answer": aligned_answer,
                        "citations": [c.model_dump() for c in citations],
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                    },
                )
            done = {"chat_id": chat_id, "thread_id": thread_id}
            yield f"event: done\ndata: {json.dumps(done, ensure_ascii=True)}\n\n".encode(
                "utf-8"
            )
            if aligned_answer != answer:
                final_payload = {"answer": aligned_answer}
                yield (
                    f"event: final\ndata: {json.dumps(final_payload, ensure_ascii=True)}\n\n".encode(
                        "utf-8"
                    )
                )
        except Exception as exc:
            err = {"detail": str(exc)}
            yield f"event: error\ndata: {json.dumps(err, ensure_ascii=True)}\n\n".encode(
                "utf-8"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/admin/reindex", response_model=ReindexResponse)
async def admin_reindex(payload: ReindexRequest) -> ReindexResponse:
    summary = await admin_reindex_files(file_ids=payload.file_ids, limit=payload.limit)
    return ReindexResponse(**summary)


@app.post("/api/admin/reindex/start", response_model=ReindexStartResponse)
async def admin_reindex_start(
    payload: ReindexRequest, background_tasks: BackgroundTasks
) -> ReindexStartResponse:
    job_id = start_reindex_job(payload.file_ids, payload.limit)
    background_tasks.add_task(run_reindex_job, job_id, payload.file_ids, payload.limit)
    return ReindexStartResponse(job_id=job_id, status="queued")


@app.get("/api/jobs/{job_id}", response_model=JobItem)
async def get_job_status(job_id: int) -> JobItem:
    row = get_job_item(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobItem(
        id=int(row["id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        job_type=str(row["job_type"]),
        status=str(row["status"]),
        progress=float(row["progress"]),
        total=int(row["total"]),
        completed=int(row["completed"]),
        message=str(row["message"]),
        payload_json=str(row["payload_json"]),
    )


@app.get("/api/jobs", response_model=list[JobItem])
async def list_jobs(limit: int = 50) -> list[JobItem]:
    rows = list_job_items(limit=limit)
    return [
        JobItem(
            id=int(r["id"]),
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
            job_type=str(r["job_type"]),
            status=str(r["status"]),
            progress=float(r["progress"]),
            total=int(r["total"]),
            completed=int(r["completed"]),
            message=str(r["message"]),
            payload_json=str(r["payload_json"]),
        )
        for r in rows
    ]


@app.post("/api/admin/purge", response_model=PurgeResponse)
async def admin_purge(payload: PurgeRequest) -> PurgeResponse:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to purge all data.")
    result = purge_all_data()
    ASK_CACHE.clear()
    return PurgeResponse(**result)


@app.get("/api/admin/metrics")
async def admin_metrics() -> dict:
    total = int(METRICS["requests_total"])
    avg = (float(METRICS["latency_ms_sum"]) / total) if total else 0.0
    return {
        "started_at_unix": int(METRICS["started_at_unix"]),
        "requests_total": total,
        "errors_5xx": int(METRICS["errors_5xx"]),
        "avg_latency_ms": round(avg, 2),
        "requests_by_path": METRICS["requests_by_path"],
    }


@app.get("/api/admin/backup")
async def admin_backup() -> FileResponse:
    backup_path = create_sqlite_backup_file()
    return FileResponse(
        path=backup_path,
        filename="perplexio-backup.db",
        media_type="application/octet-stream",
        background=BackgroundTask(_safe_unlink, backup_path),
    )


@app.get("/api/admin/backups", response_model=list[BackupItem])
async def admin_backups() -> list[BackupItem]:
    items = list_backups()
    return [
        BackupItem(
            name=str(i["name"]),
            created_at=str(i["created_at"]),
            size_bytes=int(i["size_bytes"]),
        )
        for i in items
    ]


@app.post("/api/admin/backups/create", response_model=BackupItem)
async def admin_backup_create() -> BackupItem:
    item = create_persistent_backup()
    return BackupItem(
        name=str(item["name"]),
        created_at=str(item["created_at"]),
        size_bytes=int(item["size_bytes"]),
    )


@app.get("/api/admin/backups/{name}/download")
async def admin_backup_download(name: str) -> FileResponse:
    path = get_backup_path(name)
    return FileResponse(path=path, filename=name, media_type="application/octet-stream")


@app.post("/api/admin/backups/{name}/restore", response_model=BackupRestoreResponse)
async def admin_backup_restore(name: str, payload: BackupRestoreRequest) -> BackupRestoreResponse:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to restore backup.")
    out = restore_backup(name)
    ASK_CACHE.clear()
    return BackupRestoreResponse(
        restored_from=str(out["restored_from"]),
        pre_restore_backup=str(out["pre_restore_backup"]),
    )


@app.get("/manifest.webmanifest")
async def pwa_manifest() -> Response:
    return Response(content=manifest_json(), media_type="application/manifest+json")


@app.get("/sw.js")
async def pwa_service_worker() -> Response:
    return Response(content=service_worker_js(), media_type="application/javascript")


@app.get("/icons/icon.png")
async def pwa_icon_any() -> FileResponse:
    if not PWA_ICON_PATH.exists():
        raise HTTPException(status_code=404, detail="Icon not found.")
    return FileResponse(path=PWA_ICON_PATH, media_type="image/png")


@app.get("/icons/maskable.png")
async def pwa_icon_maskable() -> FileResponse:
    if not PWA_ICON_PATH.exists():
        raise HTTPException(status_code=404, detail="Icon not found.")
    return FileResponse(path=PWA_ICON_PATH, media_type="image/png")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML
