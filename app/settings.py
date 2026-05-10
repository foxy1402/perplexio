import os
from hashlib import sha256
from pathlib import Path


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer for {name}: {raw}") from exc


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float for {name}: {raw}") from exc


def env_list(name: str, default: str = "") -> list[str]:
    raw = env_str(name, default)
    if not raw:
        return []
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------------------
# LLM — used for non-search tasks (thread titles, summaries, OCR, followups)
# ---------------------------------------------------------------------------
OPENAI_BASE_URL = env_str("OPENAI_BASE_URL", "https://gen.pollinations.ai")
OPENAI_API_KEY = env_str("OPENAI_API_KEY", "")
OPENAI_MODEL = env_str("OPENAI_MODEL", "openai")
OPENAI_TIMEOUT_SECONDS = env_float("OPENAI_TIMEOUT_SECONDS", 60.0)
SYSTEM_PROMPT = env_str(
    "SYSTEM_PROMPT",
    (
        "You are a helpful, accurate assistant. "
        "Answer from the provided context and your training knowledge. "
        "Never invent URLs, statistics, quotes, or facts you are unsure about."
    ),
)

# ---------------------------------------------------------------------------
# Perplexity Sonar via Pollinations.ai — handles web search natively
# Base URL: https://gen.pollinations.ai  (OpenAI-compatible)
# Models: perplexity-fast (Sonar) | perplexity-reasoning (Sonar Reasoning)
# ---------------------------------------------------------------------------
SONAR_MODEL = env_str("SONAR_MODEL", "perplexity-fast")
# web | academic | sec  — Perplexity search index to use
SONAR_SEARCH_MODE = env_str("SONAR_SEARCH_MODE", "web")
# hour | day | week | month | year | "" (no filter)
SONAR_SEARCH_RECENCY = env_str("SONAR_SEARCH_RECENCY", "")
# Comma-separated domain allowlist, e.g. "github.com,wikipedia.org" — "" means no filter
SONAR_SEARCH_DOMAIN_FILTER = env_list("SONAR_SEARCH_DOMAIN_FILTER", "")

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
DATA_DIR = Path(env_str("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "perplexio.db"
BACKUP_DIR = DATA_DIR / "backups"

MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 20)
FILE_CONTEXT_FILE_COUNT = env_int("FILE_CONTEXT_FILE_COUNT", 3)
MAX_FILE_CONTEXT_CHARS = env_int("MAX_FILE_CONTEXT_CHARS", 12000)

# ---------------------------------------------------------------------------
# Thread memory
# ---------------------------------------------------------------------------
THREAD_HISTORY_TURNS = env_int("THREAD_HISTORY_TURNS", 6)
THREAD_SUMMARY_ENABLED = env_int("THREAD_SUMMARY_ENABLED", 1) == 1
THREAD_SUMMARY_INTERVAL = env_int("THREAD_SUMMARY_INTERVAL", 3)
THREAD_RECENT_TURNS = env_int("THREAD_RECENT_TURNS", 3)
THREAD_SUMMARY_MAX_TOKENS = env_int("THREAD_SUMMARY_MAX_TOKENS", 300)

# ---------------------------------------------------------------------------
# Embeddings — still needed for file RAG
# ---------------------------------------------------------------------------
EMBEDDING_BASE_URL = env_str("EMBEDDING_BASE_URL", OPENAI_BASE_URL)
EMBEDDING_API_KEY = env_str("EMBEDDING_API_KEY", OPENAI_API_KEY)
EMBEDDING_MODEL = env_str("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_TIMEOUT_SECONDS = env_float("EMBEDDING_TIMEOUT_SECONDS", 60.0)

# ---------------------------------------------------------------------------
# File chunking & retrieval
# ---------------------------------------------------------------------------
FILE_CHUNK_SIZE_CHARS = env_int("FILE_CHUNK_SIZE_CHARS", 1200)
FILE_CHUNK_OVERLAP_CHARS = env_int("FILE_CHUNK_OVERLAP_CHARS", 200)
FILE_VECTOR_TOP_K = env_int("FILE_VECTOR_TOP_K", 10)
FILE_VECTOR_CANDIDATE_LIMIT = env_int("FILE_VECTOR_CANDIDATE_LIMIT", 200)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_PASSWORD = env_str("AUTH_PASSWORD", "")
AUTH_COOKIE_NAME = env_str("AUTH_COOKIE_NAME", "perplexio_session")
AUTH_SESSION_MAX_AGE_SECONDS = env_int("AUTH_SESSION_MAX_AGE_SECONDS", 315360000)
AUTH_SESSION_SECRET = env_str(
    "AUTH_SESSION_SECRET",
    sha256((AUTH_PASSWORD or "perplexio").encode("utf-8")).hexdigest(),
)
AUTH_COOKIE_SECURE = env_int("AUTH_COOKIE_SECURE", 0) == 1

# ---------------------------------------------------------------------------
# OCR / Vision
# ---------------------------------------------------------------------------
OCR_ENABLED = env_int("OCR_ENABLED", 1) == 1
OCR_LANGUAGE = env_str("OCR_LANGUAGE", "eng")
OCR_VISION_ENABLED = env_int("OCR_VISION_ENABLED", 0) == 1
VISION_MODEL = env_str("VISION_MODEL", "")        # empty → inherits OPENAI_MODEL at runtime
VISION_BASE_URL = env_str("VISION_BASE_URL", "")  # empty → inherits OPENAI_BASE_URL at runtime
VISION_API_KEY = env_str("VISION_API_KEY", "")    # empty → inherits OPENAI_API_KEY at runtime

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------
TRANSCRIPTION_ENABLED = env_int("TRANSCRIPTION_ENABLED", 1) == 1
TRANSCRIPTION_ENGINE = env_str("TRANSCRIPTION_ENGINE", "auto")
TRANSCRIPTION_MODEL = env_str("TRANSCRIPTION_MODEL", "base")
TRANSCRIPTION_LANGUAGE = env_str("TRANSCRIPTION_LANGUAGE", "auto")
WHISPER_CPP_BIN = env_str("WHISPER_CPP_BIN", "")
WHISPER_CPP_MODEL = env_str("WHISPER_CPP_MODEL", "")

# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
BACKUP_RETENTION_COUNT = env_int("BACKUP_RETENTION_COUNT", 10)
ASK_CACHE_TTL_SECONDS = env_int("ASK_CACHE_TTL_SECONDS", 300)
ASK_CACHE_MAX_ITEMS = env_int("ASK_CACHE_MAX_ITEMS", 300)

LLM_RETRY_MAX_ATTEMPTS = env_int("LLM_RETRY_MAX_ATTEMPTS", 3)
LLM_RETRY_BASE_DELAY = env_float("LLM_RETRY_BASE_DELAY", 2.0)
LLM_RETRY_BACKOFF_FACTOR = env_float("LLM_RETRY_BACKOFF_FACTOR", 2.0)
LLM_MAX_TOKENS = env_int("LLM_MAX_TOKENS", 2048)
