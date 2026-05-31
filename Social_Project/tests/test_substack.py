"""Substack RSS fan-out tests (Deliverable 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from models import Draft, ScheduledPost, SubstackSeen
from services.substack import (
    _strip_html,
    extract_pull_quotes,
    import_new_substack_drafts,
    parse_feed,
)

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Operator Substack</title>
  <link>https://operator.substack.com</link>
  <item>
    <guid isPermaLink="false">post-1</guid>
    <title>The first post</title>
    <link>https://operator.substack.com/p/the-first-post</link>
    <content:encoded><![CDATA[<p>This is the introduction paragraph that is rich with context and detail because it needs to clear the eighty character threshold the extractor uses.</p>
<p>A second sentence which is also sufficiently long that the pull quote extractor will happily include it as a candidate option for the operator to review.</p>
<p>Short.</p>
<p>And a third candidate that is again long enough to clear the eighty character threshold the extractor cares about.</p>]]></content:encoded>
  </item>
  <item>
    <guid isPermaLink="false">post-2</guid>
    <title>The second post</title>
    <link>https://operator.substack.com/p/the-second-post</link>
    <content:encoded><![CDATA[<p>Another long lead paragraph that should serve as a perfectly good pull-quote candidate for the operator to consider when promoting this post to social.</p>]]></content:encoded>
  </item>
</channel>
</rss>"""


def test_strip_html_removes_tags_and_collapses_whitespace():
    raw = "<p>hello   <b>world</b></p>"
    assert _strip_html(raw) == "hello world"


def test_extract_pull_quotes_returns_at_most_n():
    body = (
        "First long sentence that crosses the threshold for sure because it has a lot of words. "
        "Second long sentence which also is more than eighty characters and very informative. "
        "Third long sentence to test that we cap at three even though there is more content."
    )
    out = extract_pull_quotes(body, n=3)
    assert len(out) == 3
    assert all(80 <= len(q) <= 220 for q in out)


def test_extract_pull_quotes_skips_too_short():
    body = "Tiny. Also tiny. Short. " + "A long sentence that is comfortably above the eighty character cutoff for inclusion."
    out = extract_pull_quotes(body, n=3)
    assert len(out) == 1


def test_parse_feed_yields_entries():
    items = parse_feed(SAMPLE_RSS, feed_url="https://example.com/feed")
    assert len(items) == 2
    assert items[0]["guid"] == "post-1"
    assert items[0]["title"] == "The first post"
    assert items[0]["url"] == "https://operator.substack.com/p/the-first-post"
    assert "introduction paragraph" in items[0]["body_text"]


@pytest.mark.asyncio
async def test_import_new_substack_drafts_creates_drafts_and_dedups(db_session):
    feed_url = "https://operator.substack.com/feed"

    n1 = await import_new_substack_drafts(db_session, feed_url=feed_url, feed_bytes=SAMPLE_RSS)
    assert n1 == 2

    drafts = (await db_session.execute(select(Draft))).scalars().all()
    assert len(drafts) == 2
    assert all(d.source_kind == "substack" for d in drafts)
    assert all(d.pull_quotes for d in drafts)

    # Second run should add nothing — guids are seen.
    n2 = await import_new_substack_drafts(db_session, feed_url=feed_url, feed_bytes=SAMPLE_RSS)
    assert n2 == 0

    seen = (await db_session.execute(select(SubstackSeen))).scalars().all()
    assert {s.guid for s in seen} == {"post-1", "post-2"}


@pytest.mark.asyncio
async def test_drafts_api_list_and_promote(engine, db_session, session_factory):
    """End-to-end: import → list via API → promote into a ScheduledPost."""
    from fastapi.testclient import TestClient

    from main import app

    await import_new_substack_drafts(
        db_session, feed_url="https://x/feed", feed_bytes=SAMPLE_RSS
    )
    drafts = (await db_session.execute(select(Draft))).scalars().all()
    draft_id = str(drafts[0].id)
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})

        r = c.get("/api/drafts")
        assert r.status_code == 200
        assert any(d["id"] == draft_id for d in r.json())

        promote_payload = {
            "caption": "Pulled quote text",
            "platforms": ["threads"],
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        r = c.post(f"/api/drafts/{draft_id}/promote", json=promote_payload)
        assert r.status_code == 200, r.text
        result = r.json()
        post_id = result["post_id"]
        assert result["draft_id"] == draft_id

    # The draft now carries promoted_post_id; the new ScheduledPost exists.
    async with session_factory() as fresh:
        d = (await fresh.execute(select(Draft).where(Draft.id == drafts[0].id))).scalar_one()
        assert str(d.promoted_post_id) == post_id
        p = (
            await fresh.execute(select(ScheduledPost).where(ScheduledPost.id == d.promoted_post_id))
        ).scalar_one()
        assert p.caption == "Pulled quote text"
        assert p.platforms == ["threads"]


@pytest.mark.asyncio
async def test_promote_rejects_caption_too_long_for_threads(engine, db_session, session_factory):
    from fastapi.testclient import TestClient

    from main import app

    await import_new_substack_drafts(
        db_session, feed_url="https://x/feed", feed_bytes=SAMPLE_RSS
    )
    drafts = (await db_session.execute(select(Draft))).scalars().all()
    draft_id = str(drafts[0].id)
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        payload = {
            "caption": "x" * 501,
            "platforms": ["threads"],
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        r = c.post(f"/api/drafts/{draft_id}/promote", json=payload)
        assert r.status_code == 422
        assert "threads caption" in r.text.lower()
