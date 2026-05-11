import asyncio
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException

from app.models import Citation
from app.settings import (
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    EMBEDDING_TIMEOUT_SECONDS,
    FILE_CHUNK_OVERLAP_CHARS,
    FILE_CHUNK_SIZE_CHARS,
    MAX_FILE_CONTEXT_CHARS,
    OCR_ENABLED,
    OCR_LANGUAGE,
    OCR_VISION_ENABLED,
    VISION_MODEL,
    VISION_BASE_URL,
    VISION_API_KEY,
    FILE_VECTOR_CANDIDATE_LIMIT,
    FILE_VECTOR_TOP_K,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_TIMEOUT_SECONDS,
    SONAR_MODEL,
    SONAR_SEARCH_MODE,
    SONAR_SEARCH_RECENCY,
    SONAR_SEARCH_DOMAIN_FILTER,
    SYSTEM_PROMPT,
    THREAD_HISTORY_TURNS,
    THREAD_RECENT_TURNS,
    THREAD_SUMMARY_ENABLED,
    THREAD_SUMMARY_INTERVAL,
    THREAD_SUMMARY_MAX_TOKENS,
    LLM_MAX_TOKENS,
    LLM_RETRY_MAX_ATTEMPTS,
    LLM_RETRY_BASE_DELAY,
    LLM_RETRY_BACKOFF_FACTOR,
    TRANSCRIPTION_ENABLED,
    TRANSCRIPTION_ENGINE,
    TRANSCRIPTION_LANGUAGE,
    TRANSCRIPTION_MODEL,
    WHISPER_CPP_BIN,
    WHISPER_CPP_MODEL,
)
from app.storage import (
    build_file_context,
    create_job,
    get_file_text,
    get_file_record,
    get_thread_file_ids,
    get_thread_history,
    get_thread_summary,
    get_thread_turn_count,
    get_thread_turns_after,
    get_job,
    list_file_chunks,
    list_file_ids,
    list_jobs,
    normalize_file_ids,
    replace_file_chunks,
    save_thread_summary,
    update_job,
    update_file_extracted_text,
)


_FW_MODEL = None
_FW_MODEL_NAME = ""

# Shared httpx clients for connection reuse.
_llm_client: httpx.AsyncClient | None = None
_embedding_client: httpx.AsyncClient | None = None


def _get_llm_client() -> httpx.AsyncClient:
    global _llm_client
    if _llm_client is None or _llm_client.is_closed:
        _llm_client = httpx.AsyncClient(timeout=httpx.Timeout(OPENAI_TIMEOUT_SECONDS))
    return _llm_client


def _get_embedding_client() -> httpx.AsyncClient:
    global _embedding_client
    if _embedding_client is None or _embedding_client.is_closed:
        _embedding_client = httpx.AsyncClient(timeout=httpx.Timeout(EMBEDDING_TIMEOUT_SECONDS))
    return _embedding_client


def _llm_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OPENAI_API_KEY:
        headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
    return headers


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


async def _retry_post(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    stream: bool = False,
) -> httpx.Response:
    """POST with exponential backoff retry for rate-limit / server errors."""
    attempts = max(1, LLM_RETRY_MAX_ATTEMPTS)
    delay = max(0.1, LLM_RETRY_BASE_DELAY)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if stream:
                resp = await client.send(
                    client.build_request("POST", url, headers=headers, json=json),
                    stream=True,
                )
            else:
                resp = await client.post(url, headers=headers, json=json)
            if resp.status_code not in _RETRYABLE_STATUS_CODES or attempt == attempts:
                resp.raise_for_status()
                return resp
            # Retryable status — wait and retry.
            logger.warning(
                "Retryable HTTP %d from %s (attempt %d/%d), retrying in %.1fs...",
                resp.status_code, url, attempt, attempts, delay,
            )
            await asyncio.sleep(delay)
            delay *= max(1.0, LLM_RETRY_BACKOFF_FACTOR)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
                logger.warning(
                    "Retryable HTTP %d from %s (attempt %d/%d), retrying in %.1fs...",
                    exc.response.status_code, url, attempt, attempts, delay,
                )
                await asyncio.sleep(delay)
                delay *= max(1.0, LLM_RETRY_BACKOFF_FACTOR)
                last_exc = exc
            else:
                raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            if attempt < attempts:
                logger.warning(
                    "Connection error to %s (attempt %d/%d): %s, retrying in %.1fs...",
                    url, attempt, attempts, exc, delay,
                )
                await asyncio.sleep(delay)
                delay *= max(1.0, LLM_RETRY_BACKOFF_FACTOR)
                last_exc = exc
            else:
                raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Retry loop exited unexpectedly")



def _parse_json_array(raw: str, fallback: list | None = None) -> list:
    """Parse a JSON array from LLM output, with fallback bracket extraction."""
    text = raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    # Fallback: find the JSON array within surrounding text.
    l = text.find("[")
    r = text.rfind("]")
    if l >= 0 and r > l:
        try:
            data = json.loads(text[l : r + 1])
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return fallback if fallback is not None else []


def _normalize_tesseract_language(lang: str) -> str | None:
    raw = (lang or "").strip()
    if not raw or raw.lower() == "auto":
        return None
    # Accept "eng+ind", "eng,ind", or "eng ind" and normalize to Tesseract format.
    parts = re.split(r"[,\s\+]+", raw)
    cleaned = [p.strip() for p in parts if p.strip()]
    return "+".join(cleaned) if cleaned else None


def _normalize_transcription_language(lang: str) -> str | None:
    raw = (lang or "").strip()
    if not raw or raw.lower() == "auto":
        return None
    return raw


logger = logging.getLogger("perplexio")







_FILE_SYSTEM_PROMPT = (
    "You are a helpful assistant. The uploaded file content is your primary source — "
    "answer from it whenever it is relevant. For questions the files do not fully address "
    "(such as estimating costs, general facts, or background knowledge), draw on your own "
    "training knowledge to give a complete, useful answer. Never refuse to answer just "
    "because the file lacks certain details. "
    "Do not invent specific figures, quotes, or facts — when estimating, label them clearly "
    "as estimates (e.g. 'approximately', 'typically around')."
)

_DIRECT_SYSTEM_PROMPT = (
    "You are a helpful, honest assistant answering from your training knowledge. "
    "Be accurate and acknowledge uncertainty when relevant. "
    "For time-sensitive topics (current prices, recent events, today's date), "
    "note that your knowledge has a training cutoff and recommend the user verify current data. "
    "Never fabricate specific statistics, URLs, quotes, or proper nouns you are unsure about. "
    "When estimating, label estimates clearly (e.g. 'approximately', 'typically', 'as of my training')."
)


def build_llm_messages(
    query: str,
    context: str,
    thread_history: list[dict[str, str]],
    thread_summary: str | None = None,
    files_only: bool = False,
) -> list[dict[str, str]]:
    if not context:
        system = _DIRECT_SYSTEM_PROMPT
    elif files_only:
        system = _FILE_SYSTEM_PROMPT
    else:
        system = SYSTEM_PROMPT
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if thread_summary:
        messages.append(
            {
                "role": "system",
                "content": f"Summary of earlier conversation:\n{thread_summary}",
            }
        )
    for turn in thread_history:
        messages.append({"role": "user", "content": turn["query"]})
        messages.append({"role": "assistant", "content": turn["answer"]})
    if context:
        if files_only:
            source_label = "Uploaded file content"
            preamble = (
                "The following uploaded file content is your primary source. "
                "Use it whenever it is relevant, and supplement with your own knowledge "
                "where the files are silent. Each chunk is numbered ([1], [2], ...) — "
                "cite using those numbers."
            )
            cite_hint = (
                "Return a concise answer. When you reference the uploaded files, "
                "cite them inline like [1], [2] matching the chunk numbers above."
            )
        else:
            source_label = "Background reference from your uploaded files"
            preamble = (
                "Background reference from the user's uploaded files is included below. "
                "It is optional context — use it when it helps, otherwise rely on web search "
                "and your own knowledge."
            )
            cite_hint = (
                "Return a concise answer and cite web sources you used inline like [1], [2]."
            )
        user_message = (
            f"{preamble}\n\n"
            f"{source_label}:\n"
            f"{context}\n\n"
            "Question:\n"
            f"{query}\n\n"
            f"{cite_hint}"
        )
    else:
        user_message = f"Question:\n{query}"
    messages.append({"role": "user", "content": user_message})
    return messages


async def compress_thread_summary(thread_id: int) -> None:
    """Compress older turns in a thread into a rolling summary.

    Called after a new turn is saved. Uses the LLM to summarize
    all turns that aren't covered by THREAD_RECENT_TURNS.
    """
    if not THREAD_SUMMARY_ENABLED:
        return
    turn_count = get_thread_turn_count(thread_id)
    if turn_count < THREAD_SUMMARY_INTERVAL + THREAD_RECENT_TURNS:
        return  # Not enough turns to compress yet.
    existing = get_thread_summary(thread_id)
    last_summarized_id = existing["summarized_up_to_chat_id"] if existing else 0
    # Get all turns since the last summary that are older than RECENT_TURNS.
    all_turns = get_thread_turns_after(thread_id, last_summarized_id)
    if len(all_turns) <= THREAD_RECENT_TURNS:
        return  # Nothing new to compress.
    # Turns to compress = all except the most recent N.
    to_compress = all_turns[: -THREAD_RECENT_TURNS]
    if not to_compress:
        return
    # Build the text to summarize.
    existing_summary = existing["summary"] if existing else ""
    parts: list[str] = []
    if existing_summary:
        parts.append(f"Previous summary:\n{existing_summary}")
    parts.append("New conversation turns to incorporate:")
    for t in to_compress:
        parts.append(f"User: {t['query']}\nAssistant: {t['answer']}")
    text_to_summarize = "\n\n".join(parts)
    prompt = (
        f"Compress the following conversation into a concise summary "
        f"(max {THREAD_SUMMARY_MAX_TOKENS} tokens). "
        f"Preserve all key facts, decisions, names, dates, and user preferences. "
        f"Do NOT include greetings or filler. Return ONLY the summary text."
    )
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text_to_summarize},
        ],
        "temperature": 0.1,
        "max_tokens": THREAD_SUMMARY_MAX_TOKENS,
    }
    try:
        client = _get_llm_client()
        resp = await _retry_post(client, endpoint, headers=headers, json=body)
        payload = resp.json()
        summary = str(
            payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        ).strip()
        if summary:
            new_up_to = int(to_compress[-1]["id"])
            save_thread_summary(
                thread_id=thread_id,
                summary=summary,
                summarized_up_to_chat_id=new_up_to,
                turn_count=turn_count,
            )
    except Exception:
        pass  # Summarization is best-effort; don't block the main flow.


def parse_sonar_citations(citations_urls: list[str]) -> list[Citation]:
    """Convert Perplexity Sonar citations URL list to Citation objects.

    Citation markers in answer text are 1-based ([1], [2]…);
    citations_urls is 0-based. Title is derived from the domain.
    """
    result: list[Citation] = []
    for url in citations_urls:
        try:
            domain = urlparse(url).netloc.removeprefix("www.")
        except Exception:
            domain = url[:40]
        result.append(Citation(title=domain or url[:40], url=url, snippet=""))
    return result


def _model_for_mode(search_mode: str) -> str:
    """Select Sonar model: 'research' uses Sonar Reasoning, everything else uses SONAR_MODEL."""
    if search_mode == "research":
        return "perplexity-reasoning"
    return SONAR_MODEL


def _sonar_extra_params(search_mode: str) -> dict:
    """Build Perplexity Sonar-specific params to merge into the request body.

    search_mode "files"    → disable_search=True (RAG-only, no web search; no other
                              search filters because they would be ignored anyway).
    search_mode "research" → force web search (no classifier — research always searches).
    other modes ("auto")   → enable_search_classifier=True (Sonar decides automatically).
    """
    if search_mode == "files":
        return {"disable_search": True}

    params: dict = {}
    if SONAR_SEARCH_MODE:
        params["search_mode"] = SONAR_SEARCH_MODE
    if SONAR_SEARCH_RECENCY:
        params["search_recency_filter"] = SONAR_SEARCH_RECENCY
    if SONAR_SEARCH_DOMAIN_FILTER:
        params["search_domain_filter"] = list(SONAR_SEARCH_DOMAIN_FILTER)
    # research → always search; auto → let Sonar's classifier decide.
    if search_mode != "research":
        params["enable_search_classifier"] = True
    return params


async def ask_model(
    messages: list[dict[str, str]], search_mode: str = "auto"
) -> tuple[str, list[Citation]]:
    """Call Perplexity Sonar and return (answer_text, citations)."""
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    body = {
        "model": _model_for_mode(search_mode),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": LLM_MAX_TOKENS,
        **_sonar_extra_params(search_mode),
    }

    client = _get_llm_client()
    resp = await _retry_post(client, endpoint, headers=headers, json=body)
    payload = resp.json()

    choices = payload.get("choices", [])
    if not choices:
        raise HTTPException(status_code=502, detail="LLM returned no choices.")
    message = choices[0].get("message", {})
    answer = str(message.get("content", "")).strip()
    if not answer:
        raise HTTPException(status_code=502, detail="LLM returned empty content.")
    citations = parse_sonar_citations(payload.get("citations", []))
    return answer, citations


async def suggest_followups(
    question: str, answer: str, citations: list[Any], max_items: int = 4
) -> list[str]:
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    source_lines: list[str] = []
    for i, c in enumerate(citations, start=1):
        if isinstance(c, Citation):
            title = c.title
            snippet = c.snippet
        else:
            title = str(getattr(c, "title", "") or c.get("title", ""))
            snippet = str(getattr(c, "snippet", "") or c.get("snippet", ""))
        source_lines.append(f"- [{i}] {title}: {snippet[:180]}")
    source_blob = "\n".join(source_lines)
    prompt = (
        "Generate concise follow-up questions based on the Q/A and sources. "
        "Return a JSON array with 3-6 strings, no markdown."
    )
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\nAnswer:\n{answer}\n\nSources:\n{source_blob}"
                ),
            },
        ],
        "temperature": 0.4,
    }
    try:
        client = _get_llm_client()
        resp = await _retry_post(client, endpoint, headers=headers, json=body)
        payload = resp.json()
        raw = str(payload.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        data = _parse_json_array(raw)
        out: list[str] = []
        seen: set[str] = set()
        for item in data:
            s = str(item).strip()
            if not s:
                continue
            k = s.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if len(out) >= max(1, max_items):
                break
        return out
    except Exception:
        return []


async def generate_thread_title(query: str, answer: str) -> str:
    """Generate a short 4-6 word title for a thread based on the first Q&A."""
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Generate a concise 4-6 word title for this conversation. "
                    "Return only the title text, no quotes or trailing punctuation."
                ),
            },
            {
                "role": "user",
                "content": f"Question: {query[:300]}\n\nAnswer excerpt: {answer[:400]}",
            },
        ],
        "temperature": 0.3,
        "max_tokens": 20,
    }
    try:
        client = _get_llm_client()
        resp = await _retry_post(client, endpoint, headers=headers, json=body)
        payload = resp.json()
        raw = payload.get("choices", [{}])[0].get("message", {}).get("content")
        # Guard: str(None) == "None" which is truthy but wrong.
        title = str(raw).strip().strip("\"'") if raw else ""
        return title[:80] if title else query[:60]
    except Exception:
        return query[:60]




async def ask_model_stream(
    messages: list[dict[str, str]], search_mode: str = "auto"
) -> AsyncIterator[str | list]:
    """Stream tokens from Perplexity Sonar.

    Yields str tokens during streaming. At the end, yields one list value
    containing Citation objects parsed from Perplexity's citations array
    (delivered in the final SSE chunk before [DONE]).
    Callers detect the citation sentinel via `isinstance(token, list)`.
    """
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    body = {
        "model": _model_for_mode(search_mode),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": LLM_MAX_TOKENS,
        "stream": True,
        **_sonar_extra_params(search_mode),
    }

    client = _get_llm_client()
    attempts = max(1, LLM_RETRY_MAX_ATTEMPTS)
    delay = max(0.1, LLM_RETRY_BASE_DELAY)
    for attempt in range(1, attempts + 1):
        try:
            async with client.stream("POST", endpoint, headers=headers, json=body) as resp:
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
                    logger.warning(
                        "Retryable HTTP %d from stream endpoint (attempt %d/%d), retrying in %.1fs...",
                        resp.status_code, attempt, attempts, delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= max(1.0, LLM_RETRY_BACKOFF_FACTOR)
                    continue
                resp.raise_for_status()
                citations_sentinel: list[Citation] | None = None
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Capture Perplexity citations array from any chunk that has it.
                    raw_cites = payload.get("citations")
                    if raw_cites and isinstance(raw_cites, list):
                        citations_sentinel = parse_sonar_citations(raw_cites)
                    choices = payload.get("choices", [])
                    if not choices:
                        continue
                    token = choices[0].get("delta", {}).get("content")
                    if token:
                        yield str(token)
                # Emit citations sentinel after all tokens (empty list if Sonar
                # didn't return any, e.g. when routed through a proxy that strips them).
                yield citations_sentinel if citations_sentinel is not None else []
                return
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            if attempt < attempts:
                logger.warning(
                    "Stream connection error (attempt %d/%d): %s, retrying in %.1fs...",
                    attempt, attempts, exc, delay,
                )
                await asyncio.sleep(delay)
                delay *= max(1.0, LLM_RETRY_BACKOFF_FACTOR)
            else:
                raise


async def prepare_ask(
    query: str,
    top_k: int,
    include_files: bool,
    thread_id: int | None,
    file_ids: list[int] | None,
    search_mode: str = "auto",
) -> tuple[list[dict[str, str]], list[Citation], str]:
    """Load thread history, retrieve file context if needed, and build LLM messages.

    Returns (messages, file_citations, effective_search_mode).
    - effective_search_mode "files" → Sonar will have disable_search=True.
    - effective_search_mode "auto"  → Sonar's enable_search_classifier decides.
    - effective_search_mode "research" → callers use SONAR_MODEL=perplexity-reasoning.
    Web citations come from Sonar's response at call time, not here.
    """
    # Resolve file IDs.
    effective_file_ids: list[int] | None
    if file_ids is not None:
        effective_file_ids = normalize_file_ids(file_ids)
    elif thread_id is not None:
        effective_file_ids = get_thread_file_ids(thread_id)
    else:
        effective_file_ids = None

    # Load thread history and rolling summary.
    history: list[dict[str, str]] = []
    summary_text: str | None = None
    if thread_id is not None:
        if THREAD_SUMMARY_ENABLED:
            existing = get_thread_summary(thread_id)
            summary_text = existing["summary"] if existing else None
            last_id = existing["summarized_up_to_chat_id"] if existing else 0
            recent = get_thread_turns_after(thread_id, last_id)
            history = [{"query": t["query"], "answer": t["answer"]} for t in recent[-THREAD_RECENT_TURNS:]]
        else:
            history = get_thread_history(thread_id, THREAD_HISTORY_TURNS)

    has_files = bool(effective_file_ids)
    files_only = (search_mode == "files" and has_files)

    # Retrieve file context when files are attached and the mode permits it.
    file_context = ""
    file_citations: list[Citation] = []
    if has_files and (include_files or search_mode in ("auto", "files")):
        file_context, file_citations = await retrieve_file_context(
            query=query, file_ids=effective_file_ids
        )
        if not file_context:
            file_context, file_citations = build_file_context(effective_file_ids)

    # Guard: if user explicitly picked "files" mode but nothing was found, error early.
    if search_mode == "files" and not file_context:
        raise HTTPException(
            status_code=404,
            detail=(
                "No content found in the attached files. "
                "Ensure the files are fully indexed before asking questions about them."
            ),
        )

    # Reconcile file-chunk citation markers with how the frontend renders them.
    # retrieve_file_context / build_file_context use "[F1] ..." labels to keep
    # them visually distinct from web citations. We rewrite those labels so the
    # final answer's [N] markers always index correctly:
    #   files_only      → [F1] becomes [1] so Sonar can cite file refs as [1], [2]
    #   auto/research   → [F1] is stripped; Sonar should cite only its web sources
    if file_context:
        if files_only:
            file_context = re.sub(r"\[F(\d+)\]", r"[\1]", file_context)
        else:
            file_context = re.sub(r"\[F\d+\]\s*", "", file_context)

    effective_mode = search_mode

    messages = build_llm_messages(
        query=query,
        context=file_context,
        thread_history=history,
        thread_summary=summary_text,
        files_only=files_only,
    )
    return messages, file_citations, effective_mode



def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    sections: list[str] = []
    current: list[str] = []
    for line in clean.splitlines():
        ln = line.rstrip()
        if ln.strip().startswith("#") and current:
            sections.append("\n".join(current).strip())
            current = [ln]
            continue
        if re.match(r"^\s*[A-Z][A-Z0-9\s\-\:]{3,}\s*$", ln) and current:
            sections.append("\n".join(current).strip())
            current = [ln]
            continue
        current.append(ln)
    if current:
        sections.append("\n".join(current).strip())
    if not sections:
        sections = [clean]

    chunk_size = max(300, size)
    ov = max(0, min(overlap, chunk_size - 1))
    step = max(1, chunk_size - ov)
    chunks: list[str] = []
    for section in sections:
        if len(section) <= chunk_size:
            chunks.append(section)
            continue
        i = 0
        while i < len(section):
            piece = section[i : i + chunk_size].strip()
            if piece:
                chunks.append(piece)
            i += step
    return chunks


_EMBED_BATCH_SIZE = 96


async def embed_texts(texts: list[str], input_type: str = "query") -> list[list[float]]:
    """Embed texts via the OpenAI-compatible /embeddings endpoint.

    Args:
        texts: List of strings to embed.
        input_type: 'query' for search queries, 'passage' for document chunks.
                    Required by NVIDIA embedding models; ignored by others.
    """
    if not texts:
        return []
    endpoint = f"{EMBEDDING_BASE_URL.rstrip('/')}/embeddings"
    headers = {"Content-Type": "application/json"}
    if EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDING_API_KEY}"

    all_vectors: list[list[float]] = []
    client = _get_embedding_client()
    for batch_start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = texts[batch_start : batch_start + _EMBED_BATCH_SIZE]
        body: dict[str, Any] = {
            "model": EMBEDDING_MODEL,
            "input": batch,
            "encoding_format": "float",
            "input_type": input_type,
            "truncate": "NONE",
        }
        resp = await _retry_post(client, endpoint, headers=headers, json=body)
        payload = resp.json()
        data = payload.get("data", [])
        for item in data:
            emb = item.get("embedding", [])
            all_vectors.append([float(x) for x in emb])
    return all_vectors


try:
    import numpy as _np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    if _NUMPY_AVAILABLE:
        av = _np.array(a, dtype=_np.float32)
        bv = _np.array(b, dtype=_np.float32)
        denom = float(_np.linalg.norm(av) * _np.linalg.norm(bv))
        return float(_np.dot(av, bv) / denom) if denom > 0.0 else -1.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for av, bv in zip(a, b):
        dot += av * bv
        norm_a += av * av
        norm_b += bv * bv
    if norm_a <= 0.0 or norm_b <= 0.0:
        return -1.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


async def index_file_chunks(file_id: int) -> int:
    file_row = get_file_text(file_id)
    if not file_row:
        return 0
    text = str(file_row["extracted_text"]).strip()
    chunks = chunk_text(text, FILE_CHUNK_SIZE_CHARS, FILE_CHUNK_OVERLAP_CHARS)
    if not chunks:
        logger.warning("File %s: no text chunks produced (empty or unextractable content)", file_id)
        replace_file_chunks(file_id, [])
        return 0
    vectors = await embed_texts(chunks, input_type="passage")
    if len(vectors) != len(chunks):
        raise RuntimeError("Embedding response size mismatch for file chunks.")
    payload = [{"content": c, "embedding": v} for c, v in zip(chunks, vectors)]
    replace_file_chunks(file_id, payload)
    return len(payload)


async def retrieve_file_context(
    query: str, file_ids: list[int] | None
) -> tuple[str, list[Citation]]:
    rows = list_file_chunks(file_ids=file_ids, limit=FILE_VECTOR_CANDIDATE_LIMIT)
    if not rows:
        return "", []
    q_vectors = await embed_texts([query], input_type="query")
    if not q_vectors:
        return "", []
    qv = q_vectors[0]
    scored: list[tuple[float, dict]] = []
    for row in rows:
        try:
            vec = [float(x) for x in json.loads(row["embedding_json"])]
        except Exception:
            logger.warning(
                "Skipping corrupt embedding for file %s chunk %s",
                row["file_id"], row["chunk_index"],
            )
            continue
        score = cosine_similarity(qv, vec)
        scored.append((score, row))
    if not scored:
        return "", []
    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[: max(1, FILE_VECTOR_TOP_K)]

    chunks: list[str] = []
    citations: list[Citation] = []
    for idx, (_score, row) in enumerate(top, start=1):
        snippet = str(row["content"])[: min(400, MAX_FILE_CONTEXT_CHARS)]
        file_id = int(row["file_id"])
        title = f"Uploaded file: {row['original_name']} (chunk {int(row['chunk_index'])})"
        citations.append(
            Citation(
                title=title,
                url=f"/api/files/{file_id}/download",
                snippet=snippet,
            )
        )
        trimmed = str(row["content"])[:MAX_FILE_CONTEXT_CHARS]
        chunks.append(f"[F{idx}] File chunk: {title}\nContent:\n{trimmed}")
    return "\n\n".join(chunks), citations


async def _ocr_with_vision_llm(file_path: str, mime_type: str) -> str:
    """Extract text from an image using the vision-capable LLM.

    Works for any language, font, or layout — no language pack required.
    Returns raw extracted text (without a [IMAGE OCR] prefix).
    """
    import base64

    p = Path(file_path)
    try:
        image_bytes = p.read_bytes()
    except Exception:
        return ""

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Normalise MIME to one the OpenAI vision API accepts.
    _MIME_ALIASES: dict[str, str] = {
        "image/jpg": "image/jpeg",
        "image/tif": "image/png",
        "image/tiff": "image/png",
        "image/bmp": "image/png",
    }
    safe_mime = _MIME_ALIASES.get(mime_type, mime_type)
    if safe_mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        ext_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
        }
        safe_mime = ext_map.get(p.suffix.lower(), "image/jpeg")

    model = VISION_MODEL or OPENAI_MODEL
    base_url = (VISION_BASE_URL or OPENAI_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    api_key = VISION_API_KEY or OPENAI_API_KEY
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract ALL text from this image exactly as it appears. "
                            "Preserve the original language and formatting. "
                            "Output only the extracted text, no commentary."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{safe_mime};base64,{image_b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.0,
    }
    try:
        client = _get_llm_client()
        resp = await _retry_post(client, endpoint, headers=headers, json=body)
        payload = resp.json()
        text = str(
            payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        ).strip()
        return text
    except Exception as exc:
        logger.warning("Vision LLM OCR failed for %s: %s", file_path, exc)
        return ""


async def transcribe_image_with_vision(file_path: str, mime_type: str) -> str:
    if not OCR_ENABLED:
        return ""
    p = Path(file_path)
    if not p.exists():
        return ""

    # Vision LLM path — language-agnostic, handles any font or script.
    if OCR_VISION_ENABLED:
        text = await _ocr_with_vision_llm(file_path, mime_type)
        if text:
            return f"[IMAGE OCR]\n{text}"
        logger.warning("Vision LLM OCR returned empty for %s; falling back to Tesseract", file_path)

    # Tesseract fallback — set OCR_LANGUAGE env var for non-English scripts
    # (e.g. "vie" for Vietnamese, "rus" for Russian, "vie+eng" for mixed).
    if shutil.which("tesseract") is None:
        return ""
    cmd = ["tesseract", str(p), "stdout"]
    ocr_lang = _normalize_tesseract_language(OCR_LANGUAGE)
    if ocr_lang:
        cmd.extend(["-l", ocr_lang])
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        return ""
    text = (completed.stdout or "").strip()
    if not text:
        return ""
    return f"[IMAGE OCR]\n{text}"


async def transcribe_audio_file(file_path: str) -> str:
    if not TRANSCRIPTION_ENABLED:
        return ""
    p = Path(file_path)
    if not p.exists():
        return ""
    engine = TRANSCRIPTION_ENGINE.lower().strip() or "auto"
    tx_lang = _normalize_transcription_language(TRANSCRIPTION_LANGUAGE)

    def run_faster_whisper() -> str:
        global _FW_MODEL, _FW_MODEL_NAME
        try:
            from faster_whisper import WhisperModel
        except Exception:
            return ""
        try:
            if _FW_MODEL is None or _FW_MODEL_NAME != TRANSCRIPTION_MODEL:
                _FW_MODEL = WhisperModel(
                    TRANSCRIPTION_MODEL,
                    device="cpu",
                    compute_type="int8",
                )
                _FW_MODEL_NAME = TRANSCRIPTION_MODEL
            language = tx_lang
            segments, _info = _FW_MODEL.transcribe(
                str(p),
                language=language,
                vad_filter=True,
            )
            text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
            return " ".join(text_parts).strip()
        except Exception:
            return ""

    def run_whisper_cli() -> str:
        if shutil.which("whisper") is None:
            return ""
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [
                "whisper",
                str(p),
                "--model",
                TRANSCRIPTION_MODEL,
                "--output_format",
                "txt",
                "--output_dir",
                tmp,
                "--fp16",
                "False",
            ]
            if tx_lang:
                cmd.extend(["--language", tx_lang])
            completed = subprocess.run(cmd, capture_output=True, text=True)
            if completed.returncode != 0:
                return ""
            txt_files = list(Path(tmp).glob("*.txt"))
            if not txt_files:
                return ""
            return txt_files[0].read_text(encoding="utf-8", errors="ignore").strip()

    def run_whisper_cpp() -> str:
        if not WHISPER_CPP_BIN or not WHISPER_CPP_MODEL:
            return ""
        bin_path = Path(WHISPER_CPP_BIN)
        model_path = Path(WHISPER_CPP_MODEL)
        if (not bin_path.exists()) or (not model_path.exists()):
            return ""
        with tempfile.TemporaryDirectory() as tmp:
            out_base = str(Path(tmp) / "transcript")
            cmd = [
                str(bin_path),
                "-m",
                str(model_path),
                "-f",
                str(p),
                "-otxt",
                "-of",
                out_base,
            ]
            if tx_lang:
                cmd.extend(["-l", tx_lang])
            completed = subprocess.run(cmd, capture_output=True, text=True)
            if completed.returncode != 0:
                return ""
            out_txt = Path(out_base + ".txt")
            if not out_txt.exists():
                return ""
            return out_txt.read_text(encoding="utf-8", errors="ignore").strip()

    text = ""
    if engine == "faster_whisper":
        text = run_faster_whisper()
    elif engine == "whisper_cli":
        text = run_whisper_cli()
    elif engine == "whisper_cpp":
        text = run_whisper_cpp()
    else:
        text = run_faster_whisper() or run_whisper_cli() or run_whisper_cpp()
    if not text:
        return ""
    return f"[AUDIO TRANSCRIPT]\n{text}"


def extract_audio_from_video(video_path: str) -> str | None:
    if shutil.which("ffmpeg") is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "mp3",
        "-ar",
        "16000",
        out_path,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass
        return None
    return out_path


async def _ocr_scanned_pdf(file_path: str) -> str:
    """Convert a scanned (image-based) PDF to page images and OCR each page.

    Tries three converters in order: pdf2image (Python), pdftoppm (poppler-utils),
    mutool (mupdf-tools). Falls back gracefully if none is available.
    """
    p = Path(file_path)
    page_texts: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        page_images: list[Path] = []

        # Option 1: pdf2image Python package (pip install pdf2image; needs poppler)
        try:
            from pdf2image import convert_from_path  # type: ignore
            images = convert_from_path(str(p), dpi=200, fmt="png")
            for i, img in enumerate(images):
                img_path = tmp_p / f"page_{i:03d}.png"
                img.save(str(img_path), "PNG")
                page_images.append(img_path)
        except Exception:
            pass

        # Option 2: pdftoppm system binary (poppler-utils)
        if not page_images and shutil.which("pdftoppm"):
            result = subprocess.run(
                ["pdftoppm", "-png", "-r", "200", str(p), str(tmp_p / "page")],
                capture_output=True,
            )
            if result.returncode == 0:
                page_images = sorted(tmp_p.glob("page-*.png"))

        # Option 3: mutool system binary (mupdf-tools)
        if not page_images and shutil.which("mutool"):
            result = subprocess.run(
                ["mutool", "draw", "-o", str(tmp_p / "page_%d.png"), str(p)],
                capture_output=True,
            )
            if result.returncode == 0:
                page_images = sorted(tmp_p.glob("page_*.png"))

        if not page_images:
            logger.warning(
                "File %s looks like a scanned PDF but no PDF-to-image tool is available. "
                "Install one of: `pip install pdf2image` (needs poppler), poppler-utils, or mupdf-tools.",
                file_path,
            )
            return ""

        for img_path in page_images:
            if OCR_VISION_ENABLED:
                text = await _ocr_with_vision_llm(str(img_path), "image/png")
            else:
                raw = await transcribe_image_with_vision(str(img_path), "image/png")
                prefix = "[IMAGE OCR]\n"
                text = raw[len(prefix):].strip() if raw.startswith(prefix) else raw
            if text:
                page_texts.append(text)

    if not page_texts:
        return ""
    return "[SCANNED PDF OCR]\n" + "\n\n--- Page Break ---\n\n".join(page_texts)


async def enrich_file_text(file_id: int) -> str:
    row = get_file_record(file_id)
    if not row:
        return ""
    existing = str(row["extracted_text"]).strip()
    mime_type = str(row["mime_type"])
    file_path = str(row["path"])

    # For PDFs: also try OCR enrichment when the existing text is suspiciously sparse
    # (< 200 chars usually means a scanned/image-based PDF where pypdf found nothing).
    _is_sparse_pdf = mime_type == "application/pdf" and len(existing) < 200
    if existing and not _is_sparse_pdf:
        return existing

    extracted = ""
    if mime_type.startswith("image/"):
        extracted = await transcribe_image_with_vision(file_path, mime_type)
    elif mime_type == "application/pdf":
        extracted = await _ocr_scanned_pdf(file_path)
    elif mime_type.startswith("audio/"):
        extracted = await transcribe_audio_file(file_path)
    elif mime_type.startswith("video/"):
        audio_path = extract_audio_from_video(file_path)
        if audio_path:
            try:
                extracted = await transcribe_audio_file(audio_path)
            finally:
                try:
                    Path(audio_path).unlink(missing_ok=True)
                except Exception:
                    pass

    if extracted:
        update_file_extracted_text(file_id, extracted)
    return extracted


async def index_file_for_retrieval(file_id: int) -> int:
    await enrich_file_text(file_id)
    return await index_file_chunks(file_id)


async def admin_reindex_files(
    file_ids: list[int] | None = None, limit: int | None = None
) -> dict:
    target_ids = normalize_file_ids(file_ids) if file_ids is not None else list_file_ids(limit)
    if limit is not None and file_ids is not None:
        target_ids = target_ids[: max(1, int(limit))]
    indexed = 0
    failed: list[dict] = []
    for fid in target_ids:
        try:
            chunks = await index_file_for_retrieval(fid)
            indexed += 1 if chunks >= 0 else 0
        except Exception as exc:
            failed.append({"file_id": fid, "error": str(exc)})
    return {
        "requested": len(target_ids),
        "indexed": indexed,
        "failed": failed,
    }


async def run_reindex_job(job_id: int, file_ids: list[int] | None, limit: int | None) -> None:
    update_job(job_id, status="running", message="Starting reindex job")
    target_ids = normalize_file_ids(file_ids) if file_ids is not None else list_file_ids(limit)
    if limit is not None and file_ids is not None:
        target_ids = target_ids[: max(1, int(limit))]
    total = len(target_ids)
    update_job(job_id, total=total, completed=0, progress=0.0)
    failed: list[dict] = []
    done = 0
    for fid in target_ids:
        try:
            await index_file_for_retrieval(fid)
        except Exception as exc:
            failed.append({"file_id": fid, "error": str(exc)})
        done += 1
        progress = (done / total) if total else 1.0
        update_job(job_id, completed=done, progress=progress, message=f"Processed {done}/{total}")
    status = "completed" if not failed else "completed_with_errors"
    msg = json.dumps({"failed": failed}, ensure_ascii=True) if failed else "Done"
    update_job(job_id, status=status, progress=1.0, completed=done, message=msg)


async def run_file_index_job(job_id: int, file_id: int) -> None:
    update_job(job_id, status="running", total=1, completed=0, progress=0.0, message="Indexing file")
    try:
        await index_file_for_retrieval(file_id)
        update_job(job_id, status="completed", completed=1, progress=1.0, message="Done")
    except Exception as exc:
        update_job(job_id, status="failed", completed=1, progress=1.0, message=str(exc))


def start_reindex_job(file_ids: list[int] | None, limit: int | None) -> int:
    payload = {"file_ids": file_ids, "limit": limit}
    return create_job("reindex", payload=payload)


def start_file_index_job(file_id: int) -> int:
    return create_job("file_index", payload={"file_id": file_id}, total=1)


def get_job_item(job_id: int) -> dict | None:
    return get_job(job_id)


def list_job_items(limit: int = 50) -> list[dict]:
    return list_jobs(limit=limit)
