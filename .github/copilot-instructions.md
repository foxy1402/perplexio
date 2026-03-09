# Copilot Instructions

## What This Project Is

Perplexio is a Perplexity-like search-grounded assistant backend. A user submits a query; the app rewrites it into multiple search variants, fans out to a self-hosted SearxNG instance, reranks and fuses results using embeddings, retrieves relevant uploaded-file chunks from SQLite via cosine similarity, then calls an OpenAI-compatible LLM to produce a cited answer. The final answer is post-processed for citation alignment and confidence scoring before being persisted and returned.

## Running the App

```bash
# Preferred: Docker Compose
cp .env.example .env   # fill in at minimum OPENAI_* and SEARXNG_BASE_URL
docker compose up --build

# Direct (requires Python 3.12+, ffmpeg, tesseract)
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Tests

```bash
# Full suite
pytest tests/

# Single test
pytest tests/test_api_core.py::test_health

# Evaluation harness (requires a running instance)
python scripts/eval_harness.py --base-url http://localhost:8000 --password "<AUTH_PASSWORD>"
```

The test fixture (`tests/conftest.py`) reloads all `app.*` modules per test, monkeypatches `DATA_DIR` to a temp directory, and stubs out file-indexing to a no-op. Tests get a `(TestClient, main_module)` tuple from the `client` fixture.

## Architecture

```
main.py          FastAPI routes, auth middleware, request metrics, SSE streaming, response cache
services.py      All business logic: search, query rewriting, LLM calls, embeddings,
                 multi-search fusion, reranking, citation alignment, confidence scoring,
                 OCR/transcription dispatch, thread summarization
storage.py       sqlite3 (no ORM): schema, CRUD, vector search, file text extraction, backups
models.py        Pydantic request/response schemas (API contract)
settings.py      Single source of truth for all env-var config via os.getenv()
auth.py          Session-based password auth; HMAC cookie validation
```

**Request flow for `POST /api/ask`:**
`main.py` → `services.prepare_ask()` (search + rerank + file context) → `services.ask_model()` (LLM) → `services.align_answer_citations()` → `services.compute_answer_confidence()` → `storage.save_chat()` → background `compress_thread_summary()`

The streaming variant (`POST /api/ask/stream`) yields SSE events: `meta` (citations), `token` (incremental text), `done` (IDs), `final` (post-processed answer), `error`.

## Key Conventions

**Async/sync boundary:** Routes and services are fully `async`/`await`. The storage layer uses the synchronous `sqlite3` module, wrapped in `with get_db()` context managers inside otherwise-async functions. Do not introduce async DB libraries without refactoring storage.

**HTTP clients:** All outbound HTTP (LLM, embeddings, SearxNG) uses `httpx.AsyncClient`. Calls go through `_retry_post()` which retries on 429/5xx with exponential backoff (configurable via `LLM_RETRY_*` env vars). Do not use `requests`.

**Settings:** All configuration lives in `settings.py` as module-level constants read from `os.getenv()`. No dotenv library is used. Add new env vars there; never read `os.getenv()` directly in other modules.

**Pydantic models:** Use Python 3.10+ union syntax (`str | None`, `list[int] | None`). Validation constraints go in `Field(...)`. Keep request and response schemas in `models.py`.

**Private helpers:** Prefix internal functions and module-level state with `_` (e.g., `_llm_client`, `_parse_json_array`, `_CACHE_LOCK`).

**Global shared state:** The embedding model client (`_embedding_client`) and LLM client (`_llm_client`) are module-level singletons initialized at startup. The response cache uses `asyncio.Lock` (`_CACHE_LOCK`). Background tasks are dispatched via FastAPI's `BackgroundTasks`.

**Error handling:** Raise `HTTPException` with an explicit `status_code` and human-readable `detail`. Log with `logger.error(...)` before re-raising unexpected exceptions. The logger name is `"perplexio"`.

**Database schema:** 7 tables — `files`, `chats`, `file_chunks` (stores embeddings as JSON), `thread_files`, `thread_summaries`, `jobs`, `app_settings`. Foreign keys use `ON DELETE CASCADE`. Schema is auto-created and migrated on startup in `storage.py`.

**File storage layout:**
```
$DATA_DIR/
  perplexio.db       SQLite database
  uploads/           Raw uploaded files
  backups/           Named SQLite backup snapshots
```
