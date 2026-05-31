"""Long-form → short-form repurposer (Deliverable 6).

Deterministic by design — sentence-boundary splits + greedy packing into
chunks that fit a platform's caption limit. No invented facts. No paid AI
service. If `OLLAMA_BASE_URL` is configured, the optional `ollama_rewrite`
function asks a locally-running Ollama instance to *rewrite* one chunk; the
operator approves each rewrite manually in the UI before it is used.

If Ollama is unreachable the function returns the input unchanged — this is
the "degrade silently" behaviour the spec calls for.
"""

from __future__ import annotations

import logging
import re

import httpx

from config import settings
from schemas import PLATFORM_CAPTION_LIMITS, PLATFORM_HASHTAG_LIMITS

log = logging.getLogger("jack.repurpose")

_SENTENCE_RE = re.compile(r"(?<=[\.!?])\s+(?=[A-Z\"'\(\d])")
_HASHTAG_RE = re.compile(r"(?<![\w&])#([A-Za-z][\w]{0,99})")


def split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = _SENTENCE_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def pack_into_chunks(
    sentences: list[str],
    *,
    char_limit: int,
    target_chunks: int | None = None,
) -> list[str]:
    """Greedy packer: accumulate sentences until the next one would exceed
    `char_limit`, then start a new chunk. If `target_chunks` is set we still
    obey the char limit but stop once we have produced that many.

    The greedy strategy is intentional: it produces stable, deterministic
    output the operator can rely on. No silent truncation; if a single
    sentence is longer than `char_limit` we put it in its own chunk anyway
    (the validator on the schedule endpoint will reject it, surfacing the
    real problem to the operator instead of hiding it).
    """
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    join_sep_len = 1  # single space when concatenating sentences

    def _flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(" ".join(current).strip())
            current = []
            current_len = 0

    for s in sentences:
        # +1 for the separating space if not the first sentence in the chunk
        sep = 0 if not current else join_sep_len
        if current_len + sep + len(s) > char_limit:
            _flush()
            if target_chunks is not None and len(chunks) >= target_chunks:
                break
        current.append(s)
        current_len += sep + len(s)

    _flush()
    if target_chunks is not None:
        chunks = chunks[:target_chunks]
    return chunks


def extract_existing_hashtags(text: str) -> list[str]:
    """Hashtags the operator already wrote in the body — never invented."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _HASHTAG_RE.finditer(text or ""):
        tag = m.group(1)
        if tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append("#" + tag)
    return out


def repurpose_text(
    body: str,
    *,
    platform: str,
    candidates_per_platform: int = 4,
) -> list[dict]:
    """Return up to `candidates_per_platform` chunks for `platform`.

    Each chunk dict has:
      - text: the candidate caption
      - char_count
      - hashtag_count
      - fits: bool (whether it fits the platform's caption + hashtag limits)
      - hashtag_suggestions: existing hashtags from the source body
    """
    if platform not in PLATFORM_CAPTION_LIMITS:
        raise ValueError(f"Unsupported platform: {platform}")
    char_limit = PLATFORM_CAPTION_LIMITS[platform]
    tag_limit = PLATFORM_HASHTAG_LIMITS[platform]
    suggestions = extract_existing_hashtags(body)
    sentences = split_sentences(body)
    chunks = pack_into_chunks(sentences, char_limit=char_limit, target_chunks=candidates_per_platform)
    out = []
    for c in chunks:
        chars = len(c)
        tags = len(_HASHTAG_RE.findall(c))
        out.append(
            {
                "text": c,
                "char_count": chars,
                "hashtag_count": tags,
                "fits": chars <= char_limit and tags <= tag_limit,
                "hashtag_suggestions": suggestions,
            }
        )
    return out


# ---------- Optional Ollama rewrite ----------


async def ollama_rewrite(text: str, *, instruction: str | None = None) -> str:
    """Call a locally-running Ollama instance to rewrite `text`. Degrades
    silently if `OLLAMA_BASE_URL` is unset or unreachable — returns `text`
    unchanged in that case.

    The operator must approve each rewrite in the UI before it is used.
    """
    if not settings.ollama_enabled:
        return text
    prompt = instruction or (
        "Rewrite the following text to make it punchier and more shareable "
        "while preserving every fact. Output only the rewritten text — no "
        "preamble, no explanation, no commentary.\n\n"
        f"{text}"
    )
    body = {
        "model": settings.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            r = await client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate", json=body
            )
            r.raise_for_status()
            data = r.json()
            return (data.get("response") or text).strip() or text
    except Exception:
        log.warning("Ollama unreachable; returning input unchanged.", exc_info=True)
        return text
