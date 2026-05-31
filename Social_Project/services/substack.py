"""Substack RSS fan-out worker (Deliverable 5).

Polls the operator's Substack feed(s), turns each new post into a draft, and
extracts three pull-quote candidates from the body for the operator to pick
from in the UI. Never auto-publishes; never invents facts.

Pull-quote extraction is deterministic — sentence-boundary splits filtered
by length. No paid AI service is involved.
"""

from __future__ import annotations

import asyncio
import logging
import re
from html import unescape

import feedparser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Draft, SubstackSeen

log = logging.getLogger("jack.substack")

# Pull-quote length window. Too short → unmemorable; too long → unwieldy.
_QUOTE_MIN_CHARS = 80
_QUOTE_MAX_CHARS = 220
# Strip tags + boilerplate from Substack HTML body.
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[\.!?])\s+(?=[A-Z\"'])")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html or "")
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def extract_pull_quotes(body_text: str, *, n: int = 3) -> list[str]:
    """Return up to `n` candidate pull-quotes from a body of plain text.

    Strategy: split by sentence boundaries, keep sentences inside the length
    window, prefer those that lead with a capital letter and end with sentence
    punctuation, return the first `n` in document order. Deterministic.
    """
    if not body_text:
        return []
    sentences = _SENTENCE_RE.split(body_text.strip())
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in sentences:
        s = raw.strip().rstrip(",")
        if not s or len(s) < _QUOTE_MIN_CHARS or len(s) > _QUOTE_MAX_CHARS:
            continue
        if s[0].islower():
            continue
        if s[-1] not in ".!?":
            s = s + "."
        # Avoid near-duplicates.
        key = s[:60].lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(s)
        if len(candidates) >= n:
            break
    return candidates


def parse_feed(xml_bytes: bytes, *, feed_url: str) -> list[dict]:
    """Parse a feedparser-compatible bytes blob into a list of draft inputs.

    Returns one dict per entry with keys: guid, title, url, body_text.
    """
    parsed = feedparser.parse(xml_bytes)
    out: list[dict] = []
    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("guid") or entry.get("link") or ""
        if not guid:
            continue
        # Substack feeds expose the full post body under content:encoded.
        body_html = ""
        if "content" in entry and entry.content:
            body_html = entry.content[0].get("value") or ""
        if not body_html:
            body_html = entry.get("summary", "") or ""
        body_text = _strip_html(body_html)
        out.append(
            {
                "guid": str(guid),
                "title": entry.get("title", "").strip() or None,
                "url": entry.get("link", "").strip() or None,
                "body_text": body_text,
            }
        )
    return out


async def fetch_feed_bytes(feed_url: str) -> bytes:
    """Fetch a feed. Uses httpx so we share the project's transport layer."""
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        r = await client.get(feed_url, headers={"User-Agent": "jack-scheduler/1.0"})
        r.raise_for_status()
        return r.content


async def import_new_substack_drafts(
    db: AsyncSession,
    *,
    feed_url: str,
    feed_bytes: bytes | None = None,
) -> int:
    """Read a feed, create a Draft per new entry, return the number created.

    `feed_bytes` is injectable so tests can drive this without network IO.
    """
    if feed_bytes is None:
        feed_bytes = await fetch_feed_bytes(feed_url)

    entries = parse_feed(feed_bytes, feed_url=feed_url)
    if not entries:
        return 0

    # Existing guids for this feed.
    seen_rows = (
        await db.execute(
            select(SubstackSeen.guid).where(SubstackSeen.feed_url == feed_url)
        )
    ).scalars().all()
    seen_guids = set(seen_rows)

    created = 0
    for entry in entries:
        if entry["guid"] in seen_guids:
            continue
        quotes = extract_pull_quotes(entry["body_text"])
        suggested = quotes[0] if quotes else (entry["title"] or "")
        if entry["url"]:
            suggested = f"{suggested}\n\n{entry['url']}".strip()
        draft = Draft(
            source_kind="substack",
            source_url=entry["url"],
            source_guid=entry["guid"],
            title=entry["title"],
            body=entry["body_text"][:8000],  # keep storage modest
            pull_quotes=quotes,
            suggested_caption=suggested[:2200] if suggested else None,
        )
        db.add(draft)
        db.add(SubstackSeen(feed_url=feed_url, guid=entry["guid"]))
        created += 1

    await db.commit()
    return created


async def poll_all_feeds() -> int:
    """Scheduler entry point. Iterates every feed in SUBSTACK_FEEDS and reports
    the total number of drafts created."""
    from database import AsyncSessionLocal

    feeds = settings.substack_feed_list
    if not feeds:
        return 0

    total = 0
    async with AsyncSessionLocal() as db:
        for url in feeds:
            try:
                n = await import_new_substack_drafts(db, feed_url=url)
                if n:
                    log.info("Substack: imported %d new draft(s) from %s", n, url)
                total += n
            except Exception:
                log.exception("Substack: failed to import from %s", url)
    return total


# Helper so tests can run synchronously in a thread.
def _poll_all_feeds_sync():
    return asyncio.run(poll_all_feeds())
