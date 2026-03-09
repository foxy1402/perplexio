import asyncio
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException

from app.models import Citation
from app.settings import (
    CITATION_ALIGN_MIN_SCORE,
    CROSS_ENCODER_MODEL,
    CROSS_ENCODER_TOP_K,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    EMBEDDING_TIMEOUT_SECONDS,
    FILE_CHUNK_OVERLAP_CHARS,
    FILE_CHUNK_SIZE_CHARS,
    MAX_FILE_CONTEXT_CHARS,
    OCR_ENABLED,
    OCR_LANGUAGE,
    FILE_VECTOR_CANDIDATE_LIMIT,
    FILE_VECTOR_TOP_K,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_TIMEOUT_SECONDS,
    CONFIDENCE_ABSTAIN_THRESHOLD,
    QUERY_REWRITE_COUNT,
    RERANK_BLEND_ALPHA,
    RERANK_USE_CROSS_ENCODER,
    SEARXNG_BASE_URL,
    SEARXNG_LANGUAGE,
    SEARXNG_SAFESEARCH,
    SEARXNG_TIMEOUT_SECONDS,
    SEARCH_FOLLOWUP_QUERIES,
    SEARCH_MAX_HOPS,
    SEARCH_DEFAULT_MODE,
    SOURCE_QUALITY_MIN,
    TRUST_BLOCKED_DOMAINS,
    TRUST_PREFERRED_DOMAINS,
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
    WEB_FUSION_FETCH_PER_QUERY,
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
_CROSS_ENCODER = None
_CROSS_ENCODER_MODEL_NAME = ""
RRF_K = 60

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


_searxng_client: httpx.AsyncClient | None = None


def _get_searxng_client() -> httpx.AsyncClient:
    global _searxng_client
    if _searxng_client is None or _searxng_client.is_closed:
        _searxng_client = httpx.AsyncClient(timeout=httpx.Timeout(SEARXNG_TIMEOUT_SECONDS))
    return _searxng_client


async def search_web(query: str, top_k: int, search_mode: str = "all") -> list[dict[str, Any]]:
    search_url = f"{SEARXNG_BASE_URL.rstrip('/')}/search"
    mode = (search_mode or SEARCH_DEFAULT_MODE or "all").strip().lower()
    params = {
        "q": query,
        "format": "json",
        "language": SEARXNG_LANGUAGE,
        "safesearch": SEARXNG_SAFESEARCH,
    }
    if mode == "web":
        params["categories"] = "general"
    elif mode == "social":
        params["categories"] = "social media"
    try:
        client = _get_searxng_client()
        resp = await client.get(search_url, params=params)
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results", [])[:top_k]
        if not results:
            logger.warning("SearxNG returned 0 results for query: %s", query)
        return results
    except Exception as exc:
        logger.error("SearxNG request failed (%s): %s", search_url, exc)
        raise


def _normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url.strip()
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((scheme, netloc, path, "", "", ""))


def source_quality_score(url: str, title: str, snippet: str) -> float:
    score = 0.0
    u = url.strip().lower()
    t = title.strip()
    s = snippet.strip()
    if u.startswith("https://"):
        score += 0.12
    if len(t) >= 8:
        score += 0.12
    if len(s) >= 80:
        score += 0.18
    low_quality_tokens = ["pinterest.", "tiktok.", "/shorts", "reddit.com/r/"]
    if any(tok in u for tok in low_quality_tokens):
        score -= 0.12
    high_quality_tokens = [
        ".gov",
        ".edu",
        "wikipedia.org",
        "arxiv.org",
        "nature.com",
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "who.int",
        "nih.gov",
    ]
    if any(tok in u for tok in high_quality_tokens):
        score += 0.2
    domain = ""
    try:
        domain = urlparse(u).netloc
    except Exception:
        pass
    if domain.count(".") >= 1:
        score += 0.1
    if any(b in domain for b in TRUST_BLOCKED_DOMAINS):
        score -= 0.5
    if any(p in domain for p in TRUST_PREFERRED_DOMAINS):
        score += 0.25
    return max(0.0, min(1.0, score))


def _parse_published_datetime(item: dict[str, Any]) -> datetime | None:
    candidates = [
        item.get("publishedDate"),
        item.get("published_date"),
        item.get("date"),
    ]
    for raw in candidates:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            norm = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(norm)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _token_set(text: str) -> set[str]:
    parts = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    return {p for p in parts if not p.isdigit()}


def source_relevance_boost(query: str, item: dict[str, Any]) -> float:
    query_tokens = _token_set(query)
    if not query_tokens:
        return 0.0
    text = f"{item.get('title','')} {item.get('content','')}"
    src_tokens = _token_set(text)
    if not src_tokens:
        return 0.0
    overlap = len(query_tokens.intersection(src_tokens)) / max(1, len(query_tokens))
    return min(0.3, overlap * 0.5)


def source_recency_boost(item: dict[str, Any]) -> float:
    dt = _parse_published_datetime(item)
    if dt is None:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    if age_days <= 2:
        return 0.2
    if age_days <= 7:
        return 0.14
    if age_days <= 30:
        return 0.08
    if age_days <= 180:
        return 0.03
    return 0.0


async def rewrite_queries(query: str) -> list[str]:
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    prompt = (
        "Rewrite the user query into short web-search queries. "
        "Return JSON array only, 1 to 3 items, no explanation."
    )
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": query},
        ],
        "temperature": 0.1,
    }
    try:
        client = _get_llm_client()
        resp = await _retry_post(client, endpoint, headers=headers, json=body)
        payload = resp.json()
        raw = str(payload.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        data = _parse_json_array(raw, fallback=[query])
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in data:
            q = str(item).strip()
            if not q:
                continue
            key = q.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(q)
            if len(cleaned) >= max(1, QUERY_REWRITE_COUNT):
                break
        return cleaned or [query]
    except Exception:
        return [query]


async def generate_followup_queries(
    query: str, results: list[dict[str, Any]], max_queries: int
) -> list[str]:
    if not results or max_queries <= 0:
        return []
    excerpts: list[str] = []
    for item in results[:6]:
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("content", "")).strip()
        if title or snippet:
            excerpts.append(f"- {title}: {snippet[:220]}")
    if not excerpts:
        return []
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Generate targeted follow-up web queries. Return JSON array only.",
            },
            {
                "role": "user",
                "content": f"Original query: {query}\n\nEvidence:\n" + "\n".join(excerpts),
            },
        ],
        "temperature": 0.2,
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
            if len(out) >= max_queries:
                break
        return out
    except Exception:
        return []


async def _fuse_search_queries(
    queries: list[str], per_query_top: int, search_mode: str
) -> list[dict[str, Any]]:
    searches = [search_web(q, per_query_top, search_mode=search_mode) for q in queries]
    results_by_query = await asyncio.gather(*searches, return_exceptions=True)
    fused: dict[str, dict[str, Any]] = {}
    for q_idx, result_set in enumerate(results_by_query):
        if isinstance(result_set, Exception):
            continue
        for rank, item in enumerate(result_set):
            url = str(item.get("url", "")).strip()
            if not url:
                continue
            nurl = _normalize_url(url)
            score = 1.0 / (RRF_K + rank + 1 + (q_idx * 0.1))
            existing = fused.get(nurl)
            if existing is None:
                copy = dict(item)
                copy["_fusion_score"] = score
                copy["_norm_url"] = nurl
                quality = source_quality_score(
                    str(copy.get("url", "")),
                    str(copy.get("title", "")),
                    str(copy.get("content", "")),
                )
                # Blend baseline trust with query match and recency.
                quality += source_relevance_boost(queries[0] if queries else "", copy)
                quality += source_recency_boost(copy)
                copy["_quality_score"] = max(0.0, min(1.0, quality))
                fused[nurl] = copy
            else:
                existing["_fusion_score"] = float(existing.get("_fusion_score", 0.0)) + score
                if len(str(item.get("content", ""))) > len(str(existing.get("content", ""))):
                    existing["content"] = item.get("content", "")
                    existing["title"] = item.get("title", existing.get("title", ""))
                # Keep the higher confidence quality estimate.
                q_new = source_quality_score(
                    str(item.get("url", "")),
                    str(item.get("title", "")),
                    str(item.get("content", "")),
                )
                q_new += source_relevance_boost(queries[0] if queries else "", item)
                q_new += source_recency_boost(item)
                existing["_quality_score"] = max(
                    float(existing.get("_quality_score", 0.0)),
                    max(0.0, min(1.0, q_new)),
                )
    return list(fused.values())


async def multi_search_fusion(
    query: str, top_k: int, search_mode: str = "all"
) -> list[dict[str, Any]]:
    rewrites = await rewrite_queries(query)
    if query.lower() not in [q.lower() for q in rewrites]:
        rewrites = [query] + rewrites
    queries = rewrites[: max(1, QUERY_REWRITE_COUNT)]
    ranked = await _fuse_search_queries(queries, WEB_FUSION_FETCH_PER_QUERY, search_mode)

    if SEARCH_MAX_HOPS > 1:
        followups = await generate_followup_queries(
            query=query,
            results=sorted(
                ranked, key=lambda x: float(x.get("_fusion_score", 0.0)), reverse=True
            )[:top_k],
            max_queries=max(0, SEARCH_FOLLOWUP_QUERIES),
        )
        if followups:
            hop = await _fuse_search_queries(
                followups, WEB_FUSION_FETCH_PER_QUERY, search_mode
            )
            merged = {str(x.get("_norm_url", "")): x for x in ranked}
            for item in hop:
                key = str(item.get("_norm_url", ""))
                if not key:
                    continue
                if key in merged:
                    merged[key]["_fusion_score"] = float(merged[key].get("_fusion_score", 0.0)) + float(
                        item.get("_fusion_score", 0.0)
                    )
                    if len(str(item.get("content", ""))) > len(str(merged[key].get("content", ""))):
                        merged[key]["content"] = item.get("content", "")
                        merged[key]["title"] = item.get("title", merged[key].get("title", ""))
                else:
                    merged[key] = item
            ranked = list(merged.values())

    filtered = [x for x in ranked if float(x.get("_quality_score", 0.0)) >= SOURCE_QUALITY_MIN]
    if filtered:
        ranked = filtered
    # Penalize domain over-concentration for better source diversity.
    domain_counts: dict[str, int] = {}
    for item in ranked:
        try:
            domain = urlparse(str(item.get("url", ""))).netloc.lower()
        except Exception:
            domain = ""
        if not domain:
            continue
        cnt = domain_counts.get(domain, 0)
        if cnt > 0:
            item["_fusion_score"] = float(item.get("_fusion_score", 0.0)) * (0.92 ** cnt)
        domain_counts[domain] = cnt + 1
    ranked.sort(key=lambda x: float(x.get("_fusion_score", 0.0)), reverse=True)
    reranked = await rerank_web_results(query, ranked, top_k=max(top_k, WEB_FUSION_FETCH_PER_QUERY))
    return reranked[:top_k]


async def rerank_web_results(
    query: str, results: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    if not results:
        return []
    if RERANK_USE_CROSS_ENCODER:
        ce = _load_cross_encoder()
        if ce is not None:
            limited = results[: max(top_k, CROSS_ENCODER_TOP_K)]
            pairs = []
            for item in limited:
                title = str(item.get("title", "")).strip()
                content = str(item.get("content", "")).strip()
                pairs.append((query, (title + "\n" + content).strip()[:1400]))
            try:
                ce_scores = ce.predict(pairs)
                alpha = min(max(RERANK_BLEND_ALPHA, 0.0), 1.0)
                scored: list[tuple[float, dict[str, Any]]] = []
                for idx, item in enumerate(limited):
                    ce_score = float(ce_scores[idx])
                    fusion = float(item.get("_fusion_score", 0.0))
                    score = (alpha * ce_score) + ((1.0 - alpha) * fusion)
                    enriched = dict(item)
                    enriched["_rank_score"] = score
                    scored.append((score, enriched))
                scored.sort(key=lambda x: x[0], reverse=True)
                return [x[1] for x in scored[:top_k]]
            except Exception:
                pass
    texts = []
    for item in results:
        title = str(item.get("title", "")).strip()
        content = str(item.get("content", "")).strip()
        texts.append((title + "\n" + content).strip()[:1200])
    try:
        vectors = await embed_texts([query] + texts)
        if len(vectors) != len(texts) + 1:
            return results[:top_k]
        qv = vectors[0]
        scored: list[tuple[float, dict[str, Any]]] = []
        for idx, item in enumerate(results):
            sim = cosine_similarity(qv, vectors[idx + 1])
            fusion = float(item.get("_fusion_score", 0.0))
            alpha = min(max(RERANK_BLEND_ALPHA, 0.0), 1.0)
            score = (alpha * sim) + ((1.0 - alpha) * fusion)
            enriched = dict(item)
            enriched["_rank_score"] = score
            scored.append((score, enriched))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored[:top_k]]
    except Exception:
        return results[:top_k]


def _load_cross_encoder():
    global _CROSS_ENCODER, _CROSS_ENCODER_MODEL_NAME
    if _CROSS_ENCODER is not None and _CROSS_ENCODER_MODEL_NAME == CROSS_ENCODER_MODEL:
        return _CROSS_ENCODER
    try:
        from sentence_transformers import CrossEncoder
    except Exception:
        return None
    try:
        _CROSS_ENCODER = CrossEncoder(CROSS_ENCODER_MODEL)
        _CROSS_ENCODER_MODEL_NAME = CROSS_ENCODER_MODEL
        return _CROSS_ENCODER
    except Exception:
        _CROSS_ENCODER = None
        _CROSS_ENCODER_MODEL_NAME = ""
        return None


def build_context(search_results: list[dict[str, Any]]) -> tuple[str, list[Citation]]:
    citations: list[Citation] = []
    chunks: list[str] = []
    for idx, item in enumerate(search_results, start=1):
        title = str(item.get("title", "")).strip() or f"Result {idx}"
        url = str(item.get("url", "")).strip()
        snippet = str(item.get("content", "")).strip()
        if not url:
            continue
        citation = Citation(title=title, url=url, snippet=snippet)
        citations.append(citation)
        chunks.append(
            f"[{idx}] Title: {citation.title}\nURL: {citation.url}\nSnippet: {citation.snippet}"
        )
    return "\n\n".join(chunks), citations


_FILE_SYSTEM_PROMPT = (
    "You are a document-grounded assistant. Use only the uploaded file content provided. "
    "If information is missing from the files, say so clearly."
)


def build_llm_messages(
    query: str,
    context: str,
    thread_history: list[dict[str, str]],
    thread_summary: str | None = None,
    files_only: bool = False,
) -> list[dict[str, str]]:
    system = _FILE_SYSTEM_PROMPT if files_only else SYSTEM_PROMPT
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
    source_label = "Uploaded file content" if files_only else "Context"
    user_message = (
        f"Use the {source_label.lower()} below to answer. If uncertain, say so.\n\n"
        f"{source_label}:\n"
        f"{context}\n\n"
        "Question:\n"
        f"{query}\n\n"
        "Return a concise answer and cite sources like [1], [2]."
    )
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


async def ask_model(messages: list[dict[str, str]]) -> str:
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    body = {"model": OPENAI_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": LLM_MAX_TOKENS}

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
    return answer


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


async def ask_model_stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions"
    headers = _llm_headers()
    body = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": LLM_MAX_TOKENS,
        "stream": True,
    }

    client = _get_llm_client()
    # Probe with a retryable non-streaming call first to handle 429/5xx before
    # opening the SSE stream (streaming responses can't be retried mid-flight).
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
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = payload.get("choices", [])
                    if not choices:
                        continue
                    token = choices[0].get("delta", {}).get("content")
                    if token:
                        yield str(token)
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
) -> tuple[list[dict[str, str]], list[Citation]]:
    # Resolve effective file IDs once so retrieval doesn't repeat the DB lookup.
    effective_file_ids: list[int] | None
    if file_ids is not None:
        effective_file_ids = normalize_file_ids(file_ids)
    elif thread_id is not None:
        effective_file_ids = get_thread_file_ids(thread_id)
    else:
        effective_file_ids = None

    # "auto" triggers files-only mode when the caller explicitly passes file_ids in
    # THIS request. Thread-attached files are included as supplementary context
    # (via include_files) but do not suppress web search on their own — the user
    # may still be asking general questions while files are attached to the thread.
    # Note: use effective_file_ids (post-normalize) so that file_ids=[] is treated
    # the same as file_ids=None — an empty list means no files were actually attached.
    effective_mode = search_mode
    if search_mode == "auto":
        effective_mode = "files" if (file_ids is not None and bool(effective_file_ids)) else "all"

    web_context = ""
    web_citations: list[Citation] = []
    if effective_mode != "files":
        search_results = await multi_search_fusion(query, top_k=top_k, search_mode=effective_mode)
        web_context, web_citations = build_context(search_results)

    file_context = ""
    file_citations: list[Citation] = []
    if include_files or effective_mode == "files":
        file_context, file_citations = await retrieve_file_context(
            query=query, file_ids=effective_file_ids
        )
        if not file_context:
            file_context, file_citations = build_file_context(effective_file_ids)

    context_parts = []
    if web_context:
        context_parts.append("Web snippets:\n" + web_context)
    if file_context:
        context_parts.append("Uploaded files:\n" + file_context)
    context = "\n\n".join(context_parts)
    if not context:
        if effective_mode == "files":
            raise HTTPException(
                status_code=404,
                detail=(
                    "No content found in the attached files. "
                    "Ensure the files are fully indexed before asking questions about them."
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=(
                f"No search results found. SearxNG endpoint ({SEARXNG_BASE_URL}) may be unreachable "
                "or returned empty results. Check SEARXNG_BASE_URL env var and container network."
            ),
        )

    history: list[dict[str, str]] = []
    summary_text: str | None = None
    if thread_id is not None:
        if THREAD_SUMMARY_ENABLED:
            existing = get_thread_summary(thread_id)
            summary_text = existing["summary"] if existing else None
            # Get only recent turns (post-summary)
            last_id = existing["summarized_up_to_chat_id"] if existing else 0
            recent = get_thread_turns_after(thread_id, last_id)
            history = [{"query": t["query"], "answer": t["answer"]} for t in recent[-THREAD_RECENT_TURNS:]]
        else:
            history = get_thread_history(thread_id, THREAD_HISTORY_TURNS)
    messages = build_llm_messages(
        query=query,
        context=context,
        thread_history=history,
        thread_summary=summary_text,
        files_only=(effective_mode == "files"),
    )
    return messages, web_citations + file_citations


async def align_answer_citations(answer: str, citations: list[Citation]) -> str:
    text = (answer or "").strip()
    if not text or not citations:
        return text
    lines = text.splitlines()
    candidate_idxs: list[int] = []
    candidate_texts: list[str] = []
    marker_re = re.compile(r"\[\d+\]")
    sentence_split_re = re.compile(r"(?<=[\.\!\?])\s+")
    for idx, line in enumerate(lines):
        s = line.strip()
        if not s or marker_re.search(s):
            continue
        claims = [
            p.strip()
            for p in sentence_split_re.split(s)
            if len(p.strip()) >= 25 and any(ch.isalpha() for ch in p)
        ]
        if not claims:
            continue
        for claim in claims:
            candidate_idxs.append(idx)
            candidate_texts.append(claim[:500])

    if not candidate_texts:
        return text

    cite_texts = []
    for i, c in enumerate(citations, start=1):
        cite_texts.append(f"[{i}] {c.title}\n{c.snippet}".strip()[:600])

    try:
        vectors = await embed_texts(candidate_texts + cite_texts, input_type="passage")
    except Exception:
        return text
    split = len(candidate_texts)
    if len(vectors) != len(candidate_texts) + len(cite_texts):
        return text

    cand_vecs = vectors[:split]
    cite_vecs = vectors[split:]
    line_to_refs: dict[int, list[int]] = {}
    for local_idx, line_idx in enumerate(candidate_idxs):
        ranked_refs: list[tuple[float, int]] = []
        for ci, cvec in enumerate(cite_vecs, start=1):
            score = cosine_similarity(cand_vecs[local_idx], cvec)
            ranked_refs.append((score, ci))
        ranked_refs.sort(key=lambda x: x[0], reverse=True)
        chosen = [ref for score, ref in ranked_refs if score >= CITATION_ALIGN_MIN_SCORE][:2]
        if not chosen and ranked_refs:
            chosen = [ranked_refs[0][1]]
        current = line_to_refs.setdefault(line_idx, [])
        for ref in chosen:
            if ref not in current:
                current.append(ref)
    for line_idx, refs in line_to_refs.items():
        refs_sorted = sorted(refs)
        lines[line_idx] = lines[line_idx].rstrip() + " " + " ".join(
            f"[{r}]" for r in refs_sorted
        )
    return "\n".join(lines)


def _detect_citation_conflict(citations: list[Citation]) -> bool:
    blobs = [f"{c.title} {c.snippet}".lower() for c in citations]
    if len(blobs) < 2:
        return False
    neg = ["not ", "no ", "never ", "false", "incorrect", "denied"]
    pos = ["is ", "are ", "confirmed", "true", "approved", "yes"]
    neg_hits = sum(1 for b in blobs if any(k in b for k in neg))
    pos_hits = sum(1 for b in blobs if any(k in b for k in pos))
    return neg_hits > 0 and pos_hits > 0


def compute_answer_confidence(answer: str, citations: list[Citation]) -> tuple[float, bool]:
    length_score = min(1.0, max(0.0, len(answer.strip()) / 700.0))
    cite_score = min(1.0, len(citations) / 5.0)
    snippet_score = min(
        1.0,
        (sum(len(c.snippet or "") for c in citations) / max(1, len(citations))) / 220.0
        if citations
        else 0.0,
    )
    conflict_penalty = 0.25 if _detect_citation_conflict(citations) else 0.0
    confidence = (0.35 * length_score) + (0.4 * cite_score) + (0.25 * snippet_score)
    confidence = max(0.0, min(1.0, confidence - conflict_penalty))
    return confidence, confidence < CONFIDENCE_ABSTAIN_THRESHOLD


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


async def transcribe_image_with_vision(file_path: str, mime_type: str) -> str:
    if not OCR_ENABLED:
        return ""
    p = Path(file_path)
    if not p.exists():
        return ""
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
    # Keep a lightweight modality marker for retrieval context.
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


async def enrich_file_text(file_id: int) -> str:
    row = get_file_record(file_id)
    if not row:
        return ""
    existing = str(row["extracted_text"]).strip()
    if existing:
        return existing
    mime_type = str(row["mime_type"])
    file_path = str(row["path"])

    extracted = ""
    if mime_type.startswith("image/"):
        extracted = await transcribe_image_with_vision(file_path, mime_type)
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
