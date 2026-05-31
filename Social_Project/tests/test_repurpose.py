"""Repurpose engine tests (Deliverable 6)."""

from __future__ import annotations

import pytest

from services.repurpose import (
    extract_existing_hashtags,
    ollama_rewrite,
    pack_into_chunks,
    repurpose_text,
    split_sentences,
)


def test_split_sentences_handles_basic_punctuation():
    text = "First sentence. Second sentence! Third sentence? Done."
    out = split_sentences(text)
    assert len(out) == 4


def test_pack_into_chunks_respects_char_limit():
    sentences = [
        "Aaaaaaaaaa.",  # 11
        "Bbbbbbbbbb.",  # 11
        "Cccccccccc.",  # 11
        "Dddddddddd.",  # 11
    ]
    chunks = pack_into_chunks(sentences, char_limit=25)
    # 11 + 1 + 11 = 23 fits, +1+11 = 35 does not → first chunk gets 2 sentences.
    assert len(chunks) == 2
    assert all(len(c) <= 25 for c in chunks)


def test_pack_into_chunks_target_chunks_limits_output():
    sentences = ["X." for _ in range(20)]
    chunks = pack_into_chunks(sentences, char_limit=10, target_chunks=2)
    assert len(chunks) == 2


def test_extract_existing_hashtags_dedups_and_preserves_order():
    text = "Hello #SocialFan! Some more text. Then #SocialFan and #Newsletter."
    out = extract_existing_hashtags(text)
    assert out == ["#SocialFan", "#Newsletter"]


def test_repurpose_text_for_threads_marks_oversize_as_does_not_fit():
    # A single sentence longer than threads's 500-char limit.
    long = "A" * 600 + "."
    result = repurpose_text(long, platform="threads")
    assert len(result) == 1
    assert result[0]["fits"] is False
    assert result[0]["char_count"] == 601


def test_repurpose_text_under_limit_fits():
    body = "First short sentence. Second short sentence. Third sentence here."
    result = repurpose_text(body, platform="threads")
    assert all(c["fits"] for c in result)


def test_repurpose_text_unsupported_platform_raises():
    with pytest.raises(ValueError):
        repurpose_text("x.", platform="linkedin")


@pytest.mark.asyncio
async def test_ollama_returns_input_unchanged_when_disabled():
    # In the test env, OLLAMA_BASE_URL is unset → ollama_enabled is False.
    out = await ollama_rewrite("hello world")
    assert out == "hello world"


@pytest.mark.asyncio
async def test_repurpose_split_endpoint(engine):
    """End-to-end: POST /api/repurpose/split returns per-platform candidates."""
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        body = " ".join([f"Sentence number {i} that has real substance to it." for i in range(40)])
        r = c.post(
            "/api/repurpose/split",
            json={"body": body, "platforms": ["threads", "instagram"], "candidates_per_platform": 3},
        )
        assert r.status_code == 200, r.text
        out = r.json()["platforms"]
        assert set(out.keys()) == {"threads", "instagram"}
        assert len(out["threads"]) <= 3
        for c_ in out["threads"]:
            assert c_["char_count"] <= 500 or c_["fits"] is False


@pytest.mark.asyncio
async def test_repurpose_rewrite_endpoint_passthrough_when_no_ollama(engine):
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        r = c.post("/api/repurpose/rewrite", json={"text": "hi"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ollama_enabled"] is False
        assert body["unchanged"] is True
        assert body["rewritten"] == "hi"
