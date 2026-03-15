# Perplexio (SearxNG + OpenAI-compatible)

![Perplexio Banner](media/perplexio_banner.png)

A minimal Perplexity-like backend you can run for free with:
- your own SearxNG instance for web search (self-hosted, no rate limits)
- any OpenAI-compatible LLM endpoint (`OPENAI_BASE_URL`)
- full runtime config via environment variables
- persistent storage under `/data` for uploads + chat history

## 1. Local run with Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Open: `http://localhost:8000`

## 1.5 Public Access Port

For public access, expose:
- `TCP 8000` to the app container (`perplexio`)

Do not expose:
- `UDP` (not used)

Recommended production setup:
- Expose `HTTPS 443` at your ingress/reverse proxy
- Proxy to internal app `TCP 8000`

## 2. Minimal Env Vars (Recommended)

You only need a small set of env vars to run the app.

```env
# Required for LLM
OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
OPENAI_MODEL=openai/gpt-oss-120b
OPENAI_API_KEY=your-api-key

# Required for web search (your own SearxNG instance)
SEARXNG_BASE_URL=http://your-searxng-instance:8080

# Recommended for file vector search (uses same API key)
EMBEDDING_BASE_URL=https://integrate.api.nvidia.com/v1
EMBEDDING_MODEL=nvidia/nv-embedcode-7b-v1
EMBEDDING_API_KEY=your-api-key

# Optional but recommended for personal deployment
AUTH_PASSWORD=change_me
DATA_DIR=/data
```

### SearxNG Setup

Deploy your own SearxNG instance for unlimited, rate-limit-free search:

```bash
docker run -d -p 8080:8080 searxng/searxng:latest
```

Then set `SEARXNG_BASE_URL` to your instance URL.

### Public SearxNG Instances (Fallback)

> **Warning:** Public instances rate-limit API requests. Self-hosted is recommended.

| Instance | URL |
|----------|-----|
| Sapti.me | `https://search.sapti.me` |
| Searx.be | `https://searx.be` |
| Ononoki | `https://search.ononoki.org` |
| Tiekoetter | `https://searx.tiekoetter.com` |

### Embedding Setup

Embeddings power smart file search — uploaded files are chunked, vectorized, and searched by semantic similarity instead of keyword matching.

- If `EMBEDDING_BASE_URL` is not set, it defaults to `OPENAI_BASE_URL`
- If your LLM provider doesn't support `/embeddings` (e.g. NVIDIA chat-only models), set a separate embedding endpoint
- Without embeddings, file upload still works — it just falls back to raw text inclusion
- NVIDIA provides `nvidia/nv-embedcode-7b-v1` for free-tier API access

## 2.5 Advanced Env Vars (Optional)

See `.env.example` for full tuning options:

| Category | Variables |
|----------|-----------|
| **Search / retrieval** | `SEARXNG_RESULT_COUNT`, `SEARCH_DEFAULT_MODE`, `SEARCH_MAX_HOPS`, `SEARCH_FOLLOWUP_QUERIES` |
| **Rerank / citations** | `RERANK_BLEND_ALPHA`, `RERANK_USE_CROSS_ENCODER`, `CROSS_ENCODER_MODEL`, `CITATION_ALIGN_MIN_SCORE`, `SOURCE_QUALITY_MIN`, `CONFIDENCE_ABSTAIN_THRESHOLD` |
| **File retrieval** | `FILE_CHUNK_SIZE_CHARS`, `FILE_CHUNK_OVERLAP_CHARS`, `FILE_VECTOR_TOP_K`, `FILE_CONTEXT_FILE_COUNT`, `MAX_FILE_CONTEXT_CHARS` |
| **Thread memory** | `THREAD_HISTORY_TURNS`, `THREAD_SUMMARY_ENABLED`, `THREAD_SUMMARY_INTERVAL`, `THREAD_RECENT_TURNS`, `THREAD_SUMMARY_MAX_TOKENS` |
| **OCR / Vision** | `OCR_ENABLED`, `OCR_LANGUAGE`, `OCR_VISION_ENABLED`, `VISION_MODEL`, `VISION_BASE_URL`, `VISION_API_KEY` |
| **Transcription** | `TRANSCRIPTION_ENABLED`, `TRANSCRIPTION_ENGINE`, `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_LANGUAGE`, `WHISPER_CPP_BIN`, `WHISPER_CPP_MODEL` |
| **Embeddings** | `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_TIMEOUT_SECONDS` |
| **Auth / session** | `AUTH_COOKIE_NAME`, `AUTH_SESSION_MAX_AGE_SECONDS`, `AUTH_SESSION_SECRET`, `AUTH_COOKIE_SECURE` |
| **LLM** | `LLM_MAX_TOKENS`, `OPENAI_TIMEOUT_SECONDS` |
| **Retry** | `LLM_RETRY_MAX_ATTEMPTS`, `LLM_RETRY_BASE_DELAY`, `LLM_RETRY_BACKOFF_FACTOR` |
| **Ops** | `BACKUP_RETENTION_COUNT`, `ASK_CACHE_TTL_SECONDS`, `ASK_CACHE_MAX_ITEMS` |

### Thread Summary Compression

Long conversations are automatically compressed to save tokens. Older turns get summarized by the LLM into a concise summary (~200-300 tokens), while the most recent turns are kept in full. This gives the LLM long-term memory without high token costs.

```env
THREAD_SUMMARY_ENABLED=1       # 1 = on (default), 0 = off
THREAD_SUMMARY_INTERVAL=3      # Compress every 3 new turns
THREAD_RECENT_TURNS=3           # Keep last 3 turns as raw Q&A
THREAD_SUMMARY_MAX_TOKENS=300   # Max summary length
```

### API Retry

All LLM and embedding API calls automatically retry on rate-limit (429) and server errors (500/502/503/504) with exponential backoff. This prevents intermittent failures from breaking your queries.

```env
LLM_RETRY_MAX_ATTEMPTS=3       # max retries (default: 3)
LLM_RETRY_BASE_DELAY=2.0       # initial wait in seconds (default: 2)
LLM_RETRY_BACKOFF_FACTOR=2.0   # multiplier per retry (2s → 4s → 8s)
```

## 3. API

### Health
`GET /health`

### Ask
`POST /api/ask`

```json
{
  "query": "What happened in AI this week?",
  "top_k": 6,
  "thread_id": 12,
  "include_files": true,
  "file_ids": [1, 3, 9]
}
```

### Ask (streaming)
`POST /api/ask/stream`  
Server-Sent Events with:
- `event: meta` (citations)
- `event: token` (incremental text)
- `event: done` (`chat_id`, `thread_id`)
- `event: final` (aligned final answer, if adjusted)
- `event: error`

### Jobs
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`

### Admin reindex uploaded files
`POST /api/admin/reindex`
```json
{
  "file_ids": [1, 2, 3],
  "limit": 100
}
```

### Admin reindex (async job)
`POST /api/admin/reindex/start`

### Admin purge chats + files
`POST /api/admin/purge`
```json
{
  "confirm": true
}
```

### Admin metrics
`GET /api/admin/metrics`

### Admin backup
`GET /api/admin/backup` (sqlite backup file)

### Persistent backups
- `GET /api/admin/backups`
- `POST /api/admin/backups/create`
- `GET /api/admin/backups/{name}/download`
- `POST /api/admin/backups/{name}/restore`

Response:
```json
{
  "answer": "...",
  "chat_id": 101,
  "thread_id": 12,
  "citations": [
    {"title":"...","url":"...","snippet":"..."}
  ]
}
```

### Upload file
`POST /api/files/upload` (multipart form field name: `file`)

### List uploaded files
`GET /api/files`

### Download uploaded file
`GET /api/files/{file_id}/download`

### List chat history
`GET /api/chats`

### Get one chat (with citations)
`GET /api/chats/{chat_id}`

### Get full thread
`GET /api/threads/{thread_id}`

### Get thread file attachments
`GET /api/threads/{thread_id}/files`

### Update thread file attachments
`PUT /api/threads/{thread_id}/files`
```json
{
  "file_ids": [1, 3, 9]
}
```

### Auth endpoints
- `GET /auth/me`
- `POST /auth/login`
- `POST /auth/logout`

## 4. Persistent Storage

The app stores runtime data in `DATA_DIR`:
- SQLite DB: `/data/perplexio.db`
- Uploads: `/data/uploads`

For Kubernetes, mount a persistent volume to `/data`.

Example pod spec fragment:
```yaml
containers:
  - name: perplexio
    image: ghcr.io/<owner>/<repo>:latest
    env:
      - name: DATA_DIR
        value: /data
    volumeMounts:
      - name: perplexio-data
        mountPath: /data
volumes:
  - name: perplexio-data
    persistentVolumeClaim:
      claimName: perplexio-pvc
```

## 5. File Type Support

Upload accepts any file type and stores it persistently.

Text extraction into LLM context currently supports:
- text files (`text/*`)
- PDF (`application/pdf`) — digital PDFs via `pypdf`; scanned PDFs via Vision LLM or Tesseract OCR
- images (`image/*`) — via Vision LLM (`OCR_VISION_ENABLED=1`) or Tesseract (`OCR_ENABLED=1`)
- audio/video (`audio/*`, `video/*`) via transcription when `TRANSCRIPTION_ENABLED=1`

## 6. Vector Retrieval For Uploaded Files

- On upload, extracted file text is chunked and embedded through your OpenAI-compatible embeddings endpoint.
- Embeddings are stored in SQLite (`file_chunks` table in `/data/perplexio.db`).
- During ask, the app embeds the query, ranks file chunks by cosine similarity, and sends top chunks to the LLM as file context.
- If embedding retrieval is unavailable, it falls back to plain file-context inclusion.

## 6.5 Web Retrieval Upgrades

- Query rewrite: app generates multiple search rewrites for broader coverage.
- Multi-search fusion: combines results from multiple rewritten queries with URL dedupe + fusion scoring.
- Iterative hops: optional follow-up query generation and second-hop retrieval.
- Reranker: embedding rerank with optional cross-encoder reranker.
- Citation alignment: final answer is post-processed to align claim-level citations.
- Trust/confidence guardrails: source quality thresholding + confidence scoring + low-confidence uncertainty notice.
- Advanced trust model includes: domain reputation heuristics, query-term relevance boost, recency boost, and domain diversity penalty.

## 7. OCR / Transcription Pipeline

### Image OCR

Two paths — Vision LLM (recommended) and Tesseract (local fallback):

**Vision LLM OCR** — works for any language, any font, no language packs needed:
```env
OCR_VISION_ENABLED=1
VISION_MODEL=google/gemma-3-27b-it   # any vision-capable model on your provider
# VISION_BASE_URL defaults to OPENAI_BASE_URL (no change needed for NVIDIA users)
# VISION_API_KEY  defaults to OPENAI_API_KEY  (no change needed for NVIDIA users)
```

**Tesseract fallback** — used when `OCR_VISION_ENABLED=0` or vision returns empty:
```env
OCR_ENABLED=1
OCR_LANGUAGE=eng        # single: "eng", "vie", "rus" — multi: "eng+vie"
```

### PDF files

- **Digital PDFs** (text layer present): text extracted automatically by `pypdf` — no OCR needed.
- **Scanned PDFs** (image-only): pages are converted to images, then OCR'd via the same pipeline above. Requires one of: `pdf2image` Python package, `pdftoppm` (poppler-utils), or `mutool` (mupdf-tools).

### Audio transcription

- `audio/*` files: transcribed via `faster-whisper` (default), `whisper` CLI, or `whisper.cpp`.
- `TRANSCRIPTION_LANGUAGE=auto` (default) — Whisper auto-detects the spoken language (supports 99 languages). Override with a specific code (`vi`, `ru`, `tr`, `en`, etc.) to skip detection.

```env
TRANSCRIPTION_ENABLED=1
TRANSCRIPTION_LANGUAGE=auto    # auto-detect language (default)
TRANSCRIPTION_MODEL=base       # Whisper model size: tiny / base / small / medium / large
TRANSCRIPTION_ENGINE=auto      # auto / faster_whisper / whisper_cli / whisper_cpp
```

### Video transcription

- `video/*` files: audio is extracted with `ffmpeg`, then transcribed using the audio pipeline above.

### Reindex

- Reindex endpoint can backfill text + embeddings for already-uploaded files: `POST /api/admin/reindex`
- Structured text extraction includes special handling for JSON, CSV, and Markdown inputs.

## 7.5 Async Processing

- Upload indexing can run asynchronously (`/api/files/upload?async_index=true`), returning `job_id`.
- Reindex can run as background job (`/api/admin/reindex/start`).
- Poll job progress via `/api/jobs/{job_id}`.

Required system binaries (only for local/fallback paths):
- `ffmpeg` — video audio extraction (always needed for video files)
- `tesseract` — local OCR fallback (optional if `OCR_VISION_ENABLED=1`)
- `pdftoppm` / `mutool` — scanned PDF page conversion (optional, one of these needed for scanned PDFs)
- `whisper` CLI or `whisper.cpp` binary — optional alternatives to `faster-whisper`

## 8. Password Auth

- Set `AUTH_PASSWORD` to enable protection.
- UI prompts for password before API access.
- Session is cookie-based and persistent (`AUTH_SESSION_MAX_AGE_SECONDS`).
- With one deployed backend + shared `/data`, multiple devices see the same synced data after login.

## 8.5 UX Extras

- Follow-up suggestions: `GET /api/chats/{chat_id}/followups`
- Thread export: `GET /api/threads/{thread_id}/export?format=markdown|json`
- Richer source cards in UI: domain display + expandable snippets
- Citation markers in answers are clickable and jump to matching source cards.
- Jobs panel in UI for async indexing/reindex progress.
- Backups panel in UI for create/download/restore flows.
- PWA install support (`/manifest.webmanifest`, `/sw.js`) for Android "Add to Home screen".
- Mobile layout tuned for phone screens (chat/composer first, controls below).

## 9.5 Evaluation Harness

- Cases file: `eval/cases.json`
- Runner: `scripts/eval_harness.py`

Example:
```bash
python scripts/eval_harness.py --base-url http://localhost:8000 --password "<your_auth_password>"
```

Output:
- JSON report at `eval/report.json`
- process exit code `0` if all cases pass, `1` if any case fails

## 9. Build image locally

```bash
docker build -t perplexio:local .
docker run --rm -p 8000:8000 --env-file .env -v ./data:/data perplexio:local
```

## 10. Build + push GHCR from GitHub Actions

Workflow file: `.github/workflows/ghcr.yml`

It publishes to:
- `ghcr.io/<owner>/<repo>:latest`
- branch/tag/sha tags
- multi-arch image manifests for `linux/amd64` and `linux/arm64`

Requirements:
1. Push this repo to GitHub.
2. Ensure Actions are enabled.
3. On push to `main`/tags (or manually via `workflow_dispatch`), image is pushed using `GITHUB_TOKEN`.

Pull example:
```bash
docker pull ghcr.io/<owner>/<repo>:latest
```
