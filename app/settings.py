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


SEARXNG_BASE_URL = env_str("SEARXNG_BASE_URL", "http://searxng:8080")
SEARXNG_LANGUAGE = env_str("SEARXNG_LANGUAGE", "en-US")
SEARXNG_SAFESEARCH = env_int("SEARXNG_SAFESEARCH", 0)
SEARXNG_RESULT_COUNT = env_int("SEARXNG_RESULT_COUNT", 6)
SEARXNG_TIMEOUT_SECONDS = env_float("SEARXNG_TIMEOUT_SECONDS", 20.0)

OPENAI_BASE_URL = env_str("OPENAI_BASE_URL", "http://localhost:11434/v1")
OPENAI_API_KEY = env_str("OPENAI_API_KEY", "")
OPENAI_MODEL = env_str("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT_SECONDS = env_float("OPENAI_TIMEOUT_SECONDS", 60.0)
SYSTEM_PROMPT = env_str(
    "SYSTEM_PROMPT",
    (
        "You are a search-grounded assistant. Use only provided web snippets. "
        "If information is missing, state uncertainty clearly."
    ),
)

DATA_DIR = Path(env_str("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "perplexio.db"
BACKUP_DIR = DATA_DIR / "backups"

MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 20)
FILE_CONTEXT_FILE_COUNT = env_int("FILE_CONTEXT_FILE_COUNT", 3)
MAX_FILE_CONTEXT_CHARS = env_int("MAX_FILE_CONTEXT_CHARS", 12000)
THREAD_HISTORY_TURNS = env_int("THREAD_HISTORY_TURNS", 6)
THREAD_SUMMARY_ENABLED = env_int("THREAD_SUMMARY_ENABLED", 1) == 1
THREAD_SUMMARY_INTERVAL = env_int("THREAD_SUMMARY_INTERVAL", 3)
THREAD_RECENT_TURNS = env_int("THREAD_RECENT_TURNS", 3)
THREAD_SUMMARY_MAX_TOKENS = env_int("THREAD_SUMMARY_MAX_TOKENS", 300)

EMBEDDING_BASE_URL = env_str("EMBEDDING_BASE_URL", OPENAI_BASE_URL)
EMBEDDING_API_KEY = env_str("EMBEDDING_API_KEY", OPENAI_API_KEY)
EMBEDDING_MODEL = env_str("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_TIMEOUT_SECONDS = env_float("EMBEDDING_TIMEOUT_SECONDS", 60.0)
FILE_CHUNK_SIZE_CHARS = env_int("FILE_CHUNK_SIZE_CHARS", 1200)
FILE_CHUNK_OVERLAP_CHARS = env_int("FILE_CHUNK_OVERLAP_CHARS", 200)
FILE_VECTOR_TOP_K = env_int("FILE_VECTOR_TOP_K", 6)
FILE_VECTOR_CANDIDATE_LIMIT = env_int("FILE_VECTOR_CANDIDATE_LIMIT", 200)
QUERY_REWRITE_COUNT = env_int("QUERY_REWRITE_COUNT", 3)
WEB_FUSION_FETCH_PER_QUERY = env_int("WEB_FUSION_FETCH_PER_QUERY", 8)
RERANK_BLEND_ALPHA = env_float("RERANK_BLEND_ALPHA", 0.75)
RERANK_USE_CROSS_ENCODER = env_int("RERANK_USE_CROSS_ENCODER", 0) == 1
CROSS_ENCODER_MODEL = env_str("CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
CROSS_ENCODER_TOP_K = env_int("CROSS_ENCODER_TOP_K", 20)
CITATION_ALIGN_MIN_SCORE = env_float("CITATION_ALIGN_MIN_SCORE", 0.22)

AUTH_PASSWORD = env_str("AUTH_PASSWORD", "")
AUTH_COOKIE_NAME = env_str("AUTH_COOKIE_NAME", "perplexio_session")
AUTH_SESSION_MAX_AGE_SECONDS = env_int("AUTH_SESSION_MAX_AGE_SECONDS", 315360000)
AUTH_SESSION_SECRET = env_str(
    "AUTH_SESSION_SECRET",
    sha256((AUTH_PASSWORD or "perplexio").encode("utf-8")).hexdigest(),
)
AUTH_COOKIE_SECURE = env_int("AUTH_COOKIE_SECURE", 0) == 1

OCR_ENABLED = env_int("OCR_ENABLED", 1) == 1
OCR_LANGUAGE = env_str("OCR_LANGUAGE", "eng")
TRANSCRIPTION_ENABLED = env_int("TRANSCRIPTION_ENABLED", 1) == 1
TRANSCRIPTION_ENGINE = env_str("TRANSCRIPTION_ENGINE", "auto")
TRANSCRIPTION_MODEL = env_str("TRANSCRIPTION_MODEL", "base")
TRANSCRIPTION_LANGUAGE = env_str("TRANSCRIPTION_LANGUAGE", "en")
WHISPER_CPP_BIN = env_str("WHISPER_CPP_BIN", "")
WHISPER_CPP_MODEL = env_str("WHISPER_CPP_MODEL", "")

SEARCH_MAX_HOPS = env_int("SEARCH_MAX_HOPS", 2)
SEARCH_FOLLOWUP_QUERIES = env_int("SEARCH_FOLLOWUP_QUERIES", 2)
SOURCE_QUALITY_MIN = env_float("SOURCE_QUALITY_MIN", 0.35)
CONFIDENCE_ABSTAIN_THRESHOLD = env_float("CONFIDENCE_ABSTAIN_THRESHOLD", 0.42)
TRUST_PREFERRED_DOMAINS = env_list("TRUST_PREFERRED_DOMAINS", "")
TRUST_BLOCKED_DOMAINS = env_list("TRUST_BLOCKED_DOMAINS", "")
SEARCH_DEFAULT_MODE = env_str("SEARCH_DEFAULT_MODE", "all").lower()

BACKUP_RETENTION_COUNT = env_int("BACKUP_RETENTION_COUNT", 10)
ASK_CACHE_TTL_SECONDS = env_int("ASK_CACHE_TTL_SECONDS", 300)
ASK_CACHE_MAX_ITEMS = env_int("ASK_CACHE_MAX_ITEMS", 300)

LLM_RETRY_MAX_ATTEMPTS = env_int("LLM_RETRY_MAX_ATTEMPTS", 3)
LLM_RETRY_BASE_DELAY = env_float("LLM_RETRY_BASE_DELAY", 2.0)
LLM_RETRY_BACKOFF_FACTOR = env_float("LLM_RETRY_BACKOFF_FACTOR", 2.0)
LLM_MAX_TOKENS = env_int("LLM_MAX_TOKENS", 1024)
