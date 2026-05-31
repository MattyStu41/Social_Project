# JACK Social Scheduler — Code Audit

Date: 2026-05-23 (D16 multi-account refactor added 2026-05-31)
Scope: every Python module, the SPA, deployment configs, and every
endpoint surfaced by the FastAPI app. Strict-mode reading: nothing is
assumed safe until it is verified to be safe.

This audit is the **prerequisite** for Deliverables 2 through 15. Every
finding below is referenced by a stable ID (`F-NN`) used in test names
and commit messages.

---

## 0. Repository hygiene

| ID | Finding | Severity | Notes |
|---|---|---|---|
| F-00 | Project directory is **not a git repository** (`Is a git repository: false`). No history, no diffs, no rollback. | Noted | **Operator decision: no git.** Folder edits only. This audit therefore documents changes inline rather than relying on diffs. The reduced safety net is accepted by the operator; risky edits will be called out individually. |
| F-01 | No `tests/` directory exists. | **Critical** | All deliverables require tests under `tests/` per project rules. |
| F-02 | No migration system (no `alembic/`, no `migrations/`, no Yoyo). `database.py` calls `Base.metadata.create_all` on boot — applies only to *new* tables, never to schema changes on existing tables. | **Critical** | Deliverables that add columns (analytics, retention, drafts) cannot ship without Alembic or equivalent. |
| F-03 | No `pyproject.toml` / `setup.cfg`. `ruff`, `mypy`, `pytest` are not configured, not installed, not in `requirements.txt`. | High | Project rules demand `ruff check .`, `mypy .`, `pytest -q` after every deliverable. |
| F-04 | Five stale root-level modules duplicate the live code under `routers/` and `services/`: `auth.py`, `meta.py`, `tiktok.py`, `posts.py`, `platforms.py`. They ship in the container, contain bugs (see F-05), and are not imported anywhere by the active app. | High | Confirmed not imported via `grep "^from (auth\|meta\|tiktok\|posts\|platforms) "`. Should be deleted; only retained until the user explicitly approves removal, per the "no removal of working code" rule (these are not working code, but the rule deserves explicit confirmation). |
| F-05 | Stale `posts.py:101` imports `check_and_publish_posts` from `scheduler` — **a function that does not exist**. If ever loaded by accident, the publish-now endpoint would 500. | Medium | Reinforces F-04: delete the dupes. |
| F-06 | Stale `auth.py` performs OAuth callbacks **without verifying the state parameter** (no CSRF protection). Hard-codes `BASE_URL` differently from the active router. If accidentally mounted, would be a security regression. | Medium | Same fix as F-04. |
| F-07 | Stale `meta.py` hard-codes Graph API `v19.0`, ignoring `settings.meta_graph_version`. | Low | Same fix as F-04. |
| F-08 | Stale `tiktok.py` posts to `post/publish/video/init/` (direct-post endpoint) instead of `post/publish/inbox/video/init/` (the audited-app-only call). Would fail for unaudited apps. | Low | Same fix as F-04. |

---

## 1. TODO / FIXME / HACK

`grep -rn -E "(TODO\|FIXME\|XXX\|HACK)"` across all source returns **zero
matches**. The codebase carries no in-line tech-debt markers.

---

## 2. Silently-swallowed exceptions

| ID | Location | Behaviour | Risk |
|---|---|---|---|
| F-10 | `scheduler.refresh_expiring_tokens` (`scheduler.py:185-187`) | Catches `Exception`, logs, continues. No alert, no dashboard surface, no retry escalation. | If a Meta or TikTok refresh fails repeatedly, the operator finds out only when a publish fails — possibly days later. Deliverable 3 must fix. |
| F-11 | `services/meta.py:_extract_error` (`meta.py:45-48`) | Catches `Exception` while parsing the error body. Falls back to `{"raw": resp.text[:500]}`. Acceptable, but the body is then attached to `MetaApiError.body` and never logged. | Operator loses context on truly weird Meta failures. |
| F-12 | `routers/posts.py:publish_now` (`routers/posts.py:122-141`) | Calls `scheduler_publish_now(post_id)` which itself uses an atomic claim. If the periodic scheduler tick has already grabbed the row, `scheduler.publish_now` returns silently and the endpoint returns the row **without surfacing that nothing happened**. | UI shows "Publish triggered" but the publish was done by the tick (probably fine) or, in a corner case, the row was claimed but the tick crashed mid-flight (post stuck in `processing`, see F-30). |
| F-13 | `static/app.js:boot` (`static/app.js:54-60`, `60-66`) | `try { … } catch (e) { console.error(e); }` for the public config and session checks. UI proceeds as if auth is not required when network fails. | Worst case: user sees the dashboard, then every authenticated request 401s. Confusing but not dangerous. |
| F-14 | `static/app.js:disconnectPlatform` and other callers — exceptions toast their message. No silent drops. | OK | — |

No other `except Exception` clauses in the live codebase silently drop.

---

## 3. Platform-API failure paths

| ID | Location | Behaviour | Risk |
|---|---|---|---|
| F-20 | `scheduler._publish_one` (`scheduler.py:88-160`) | On per-platform failure, records `platform_results[p] = {"success": False, "error": str(exc)}` and sets `all_success=False`. Post moves to `failed` state. **No retry policy. No backoff. No circuit breaker.** | A platform outage burns through every queued post one by one, each one marked failed. Operator must retry manually. Deliverable 2 fixes. |
| F-21 | `scheduler._publish_one` partial-success bug | If Threads succeeds but Instagram fails, `platform_results = {"threads": {"success": True, "post_id": "…"}, "instagram": {"success": False, …}}`. Post status is `failed`. **On retry (or PATCH-driven re-queue), the scheduler walks `post.platforms` again, re-publishes Threads, and creates a duplicate Threads post.** | ★ **Real double-publish bug.** Deliverable 2 must skip platforms whose existing `platform_results[p].success == True`. |
| F-22 | `services/meta.py:publish_to_threads` | Polls for FINISHED for `20 * 3s = 60s` for media. Times out with a single error. No partial recovery. | Acceptable. |
| F-23 | `services/meta.py:publish_to_instagram` | Polls for `30 * 5s = 150s`. If IG video processing takes longer (Reels routinely take 60-180s), this can timeout falsely. | Bump tunables or use exponential backoff. Worth adding as a tunable env var. |
| F-24 | `services/tiktok.py:upload_to_tiktok_inbox` | Sends video to TikTok inbox; `caption` parameter is silently discarded (per docstring — TikTok inbox API does not accept it). UI shows the caption but it never reaches TikTok. | The user has been told this in the docstring but **not in the UI**. Deliverable 4 (compose view) must surface this clearly per-platform. |
| F-25 | `services/tiktok.py:_raise_for_error` | Treats `body.error.code != "ok"` as failure but only when `code` is truthy. If TikTok ever returns no `error` block but a non-2xx status, the first half of the OR catches it. Reading: appears robust. | OK. |
| F-26 | `services/tiktok.py:ensure_fresh_tiktok_token` | Raises `TikTokApiError` if `refresh_token` is missing. Scheduler catches and marks the post failed for `tiktok`. Operator sees a `"TikTok refresh_token missing. Reconnect TikTok in the UI."` error in `last_error`. | OK, but should also disable scheduling for tiktok until reconnected (dashboard warning). |
| F-27 | `routers/auth.py:_upsert_token` (`routers/auth.py:227-258`) | `expires_at` is set only if `expires_in > 0`. If the platform omits `expires_in`, `expires_at = None`, and the refresh job's `if token.expires_at is None: continue` (`scheduler.py:175-176`) **silently skips that token forever**. | Real risk. Deliverable 3 must default a conservative expiry (e.g. 30 days for Meta long-lived, 24h for TikTok) or surface a warning. |
| F-28 | `services/meta.py:refresh_long_lived_token` | Does not validate that the new token actually works (no `get_threads_user` round-trip). A refresh that returns a token bound to a different account would not be caught until publish. | Low risk in practice (Meta does not change subject mid-refresh). |

---

## 4. Scheduler edge cases (double-publish / skip)

| ID | Edge case | Current behaviour | Outcome |
|---|---|---|---|
| F-30 | **Crash mid-publish.** `scheduler.run_due_posts` claims rows → status flips to `processing` → commit → walks platforms one by one. If the process dies before the final `db.commit()` (or `_publish_one` completes), rows are stuck in `processing` forever. The drain `WHERE status == pending` never picks them up. | **Posts disappear from the queue.** No alert. | Deliverable 9 fixes (boot-time reconcile). |
| F-31 | **Already-published platforms re-published on retry.** See F-21. | Double-post. | Deliverable 2 fixes. |
| F-32 | **`publish-now` race with periodic tick.** `routers/posts.py` sets `scheduled_at = now` and commits. The next periodic tick fires within `SCHEDULER_INTERVAL_SECONDS` (default 30s) and may claim the row before the explicit `await scheduler_publish_now(post_id)` runs. `scheduler.publish_now` then no-ops because the row is no longer `pending`. **Endpoint returns 200 with the post in its current state.** | UI toast "Publish triggered" can be a lie. Net effect: publish still happens via the tick, so functionally OK. | Minor; document or wait for `processing → published` transition. |
| F-33 | **Concurrent ticks within one process.** `AsyncIOScheduler` is configured with `max_instances=1, coalesce=True`. The previous tick must finish before the next fires. **No real concurrent-tick risk in-process.** Cross-replica concurrency exists only if Replit/Railway runs >1 replica — currently `numReplicas = 1` in `railway.toml`. | Safe in single-replica deployment. | Deliverable 2 still adds a concurrency test (per spec). |
| F-34 | **Atomic claim correctness.** `UPDATE scheduled_posts SET status='processing' WHERE status='pending' AND scheduled_at <= now RETURNING id` under Postgres default isolation (READ COMMITTED) acquires a row lock during evaluation; a second concurrent UPDATE blocks, then re-reads the row, sees `status='processing'`, and returns zero rows. | **Provably safe** at the database level. Test must prove. | Deliverable 2 writes that test. |
| F-35 | **Scheduler `_publish_one` is sequential per post.** A batch of 5 posts × 3 platforms with IG video polling at 150s each = up to ~37 minutes inside one tick. The next tick is held by `max_instances=1`. New `pending` posts wait. | Throughput limit, not correctness. For a single operator at 1-50 posts/day this is fine. | Document. |
| F-36 | **Cancelled status never set anywhere.** `PostStatus.cancelled` is declared in `models.py` but no code path transitions to it. | Cosmetic. Either wire up a cancel action or drop the enum value. | Low priority. |
| F-37 | **Timezone confusion.** `scheduler.run_due_posts` compares with `datetime.now(timezone.utc)`. `ScheduledPost.scheduled_at` is `DateTime(timezone=True)`. Pydantic `_coerce_utc_required` forces incoming timestamps to be tz-aware and converts to UTC. **Consistent.** | OK. | — |
| F-38 | **Misfire grace 60s.** APScheduler's `misfire_grace_time=60` means if the scheduler is paused/sleeping for longer than 60s past the trigger time, the missed tick is dropped. This is **fine for an interval trigger** (next tick still fires), but worth noting for Replit free-tier sleep behaviour. | Document in DEPLOY_REPLIT.md. | — |

---

## 5. Endpoints missing auth

Verified by reading every router. The full set of unauthenticated paths:

| Path | Status | Notes |
|---|---|---|
| `GET /healthz` | OK | Returns `{status, version, auth}` — version + auth flag, no secrets. |
| `GET /api/admin/config` | OK | Returns `auth_required`, `platforms_available` (booleans only, no secrets), `base_url`, `version`. Acceptable — `base_url` is public on a public deployment anyway. |
| `POST /api/auth/login` | OK | The auth surface itself. **F-40: no rate limiting** — a single attacker can grind bcrypt at ~ 4 attempts/sec/core. For a single-operator self-hosted app the risk is low but non-zero. Recommend a simple per-IP in-memory limiter (e.g. 5 attempts / minute). |
| `POST /api/auth/logout` | OK | Idempotent cookie clear. |
| `GET /api/auth/session` | OK | Returns `{authenticated, auth_required}`. |
| `GET /api/auth/threads/callback` | OK | Verifies HMAC-signed `state` (`security.verify_oauth_state`). |
| `GET /api/auth/instagram/callback` | OK | Same. |
| `GET /api/auth/tiktok/callback` | OK | Same. |
| `GET /` and `/*` static | OK | Static SPA. No data. |

All other routes inherit `Depends(require_admin)` via router-level dependency. **No unprotected mutating endpoint.** ✓

### Auth nits

| ID | Location | Issue |
|---|---|---|
| F-40 | `routers/auth.py:login` | No rate limiting. See above. |
| F-41 | `routers/auth.py:logout` | `response.delete_cookie(SESSION_COOKIE, path="/")` omits `samesite` / `secure`. Some browsers refuse to delete a cookie if attributes don't match. Should mirror `set_cookie`. |
| F-42 | `routers/auth.py:session_status` | Imports `_validate_session_token` (leading underscore = private). Promote to a public helper. |
| F-43 | `security.mint_oauth_state` | `settings.session_secret.encode("utf-8") or b"unconfigured"` is a no-op (any non-empty string encodes to truthy bytes). The fallback only fires when secret is `""`, which would already have made `_serializer()` raise 503 elsewhere. Cosmetic; should be removed for clarity. |

---

## 6. Configuration without env validation

| ID | Setting | Default | Issue |
|---|---|---|---|
| F-50 | `BASE_URL` | `"http://localhost:8000"` | No trailing-slash strip. `f"{base_url}/api/auth/..."` produces double-slashes if operator sets `BASE_URL=https://x.com/`. Meta usually accepts, TikTok rejects. Add validator. |
| F-51 | `BASE_URL` | — | Not validated against an https-or-http scheme. A typo (e.g. `https//x.com`) would silently corrupt every redirect URI. Add validator. |
| F-52 | `META_APP_ID`, `META_APP_SECRET`, `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` | `""` | No required-on-platform-connect validation in config itself. `routers/auth.py` raises 503 at connect time which is acceptable. |
| F-53 | `SESSION_SECRET` | `""` | Same — auth disabled silently if absent, but `require_admin` then 503s on every protected endpoint. Document boot-time visibility. |
| F-54 | `ADMIN_PASSWORD_HASH` | `""` | Same as above. |
| F-55 | `SCHEDULER_INTERVAL_SECONDS` | 30, range [10, 300] | Validated. ✓ |
| F-56 | `DATABASE_URL` | `""` | `database.py` raises `RuntimeError` at import if empty. Fast boot failure. ✓ |
| F-57 | `CORS_ORIGINS` | `""` | No validation that entries are URLs. Trivial values get passed straight through. Low risk. |
| F-58 | `LOG_LEVEL` | `"INFO"` | No validation; an invalid value would crash `setLevel`. Add allowlist. |
| F-59 | `SUBSTACK_FEEDS` | not present | Will be added by Deliverable 5. Must validate as comma-separated URL list. |
| F-60 | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` / `ALERT_EMAIL` | not present | Will be added by Deliverable 3. Must be all-or-nothing. |
| F-61 | `OLLAMA_BASE_URL` | not present | Will be added by Deliverable 6. Optional, default disabled. |
| F-62 | `RETENTION_DEFAULT_DAYS` | not present | Will be added by Deliverable 13. Default 365. |

---

## 7. Data model & migration gaps

| ID | Finding | Impact |
|---|---|---|
| F-70 | `Base.metadata.create_all` only creates tables that don't exist. New columns or new tables added by Deliverables 4, 5, 8, 13 won't be applied on existing Neon DBs without Alembic. | Must add Alembic before any schema delta. |
| F-71 | `PostStatus` enum uses Postgres native `SAEnum(PostStatus, name="post_status")`. Adding values requires `ALTER TYPE … ADD VALUE`. Must be in a migration. | OK once Alembic is in. |
| F-72 | `ScheduledPost.platform_results` is `JSON`. Cannot index efficiently. For per-platform analytics this is fine; for queries like "find all posts where Instagram failed" we will need either a JSONB GIN index or a normalised analytics table (Deliverable 8 takes the latter approach). | Plan covered. |
| F-73 | No `platforms_overrides` column to support per-platform caption/media overrides (Deliverable 4 requires). | Must add via migration. |
| F-74 | No `source` column to track Substack-fan-out provenance (Deliverable 5). | Must add via migration. |
| F-75 | No `analytics` table for impressions/engagement (Deliverable 8). | Must create via migration. |
| F-76 | No `audit_log` table for retention/purge actions (Deliverable 13). | Must create via migration. |
| F-77 | No `user_settings` (or `app_settings`) table for operator timezone, retention default, etc. (Deliverable 7, 13). Currently all settings live in env. | A single-row settings table is the cleanest fit. |
| F-78 | No `substack_seen` table for de-duping RSS items (Deliverable 5). | Must create via migration. |
| F-79 | No idempotency key on publish calls. Meta endpoints don't accept Idempotency-Keys for create-media; TikTok publish_id is the dedupe key. Crash recovery (Deliverable 9) relies on **storing the in-flight container/publish IDs in `platform_results`** before the second commit so re-runs can resume rather than restart. Currently we only store IDs *after* the entire publish completes. | Deliverable 9 must update `platform_results` after each platform step, not just at the end. |

---

## 8. Front-end

| ID | Finding | Severity |
|---|---|---|
| F-80 | `static/app.js` polls every 15s. For a single operator: fine. For browser-paused tabs: respects `document.hidden`. ✓ | OK |
| F-81 | UI does not surface that **TikTok captions are silently discarded** by the inbox flow. (F-24) | Medium — fix in Deliverable 4. |
| F-82 | UI has no visibility into circuit-breaker state (Deliverable 2 will add). | Pending Deliverable 2. |
| F-83 | UI has no visibility into token-expiry warnings (Deliverable 3 will add). | Pending Deliverable 3. |
| F-84 | UI has no calendar view; only a list. (Deliverable 7) | Pending. |
| F-85 | UI has no studio view (Deliverable 4) or repurpose view (Deliverable 6). | Pending. |
| F-86 | UI is a single static HTML file. Adding `/studio`, `/calendar`, `/repurpose`, `/analytics`, `/settings` as separate routes means either: (a) full SPA router, (b) separate static files served from FastAPI. **Recommendation: (b)** — keeps the vanilla-JS stack, no build step, no new deps. Each page is its own `static/<name>.html` + `static/<name>.js`, sharing `static/common.js` for the API helper + toast + auth. | Decision pending operator approval. |

---

## 9. Deployment

| ID | Finding | Severity |
|---|---|---|
| F-90 | README and `railway.toml` target Railway, not Replit. Operator now wants Replit. | Pending Deliverable 10/14. |
| F-91 | No `.replit` or `replit.nix`. | Pending Deliverable 10. |
| F-92 | `Dockerfile` exists and is clean (slim base, non-root user, healthcheck). Could keep for portability. | OK to retain. |
| F-93 | `Procfile` exists, matches Railway/Heroku conventions. | OK to retain. |
| F-94 | `nixpacks.toml` won't help Replit (Replit uses its own Nix dialect via `replit.nix`). | OK to retain for Railway fallback. |
| F-95 | No Alembic config — see F-02 / F-70. | Critical for Deliverable 10. |
| F-96 | Replit free-tier sleeps. APScheduler is in-process. **Posts will not publish while the Repl is asleep.** Must document; operator chooses Reserved VM. | Deliverable 11. |

---

## 10. Dependencies

`requirements.txt` is minimal and pinned. Current set, all current as of May 2026:

| Package | Status |
|---|---|
| `fastapi==0.115.6` | OK |
| `uvicorn[standard]==0.32.1` | OK |
| `sqlalchemy[asyncio]==2.0.36` | OK |
| `asyncpg==0.30.0` | OK |
| `httpx==0.27.2` | OK |
| `apscheduler==3.10.4` | OK |
| `pydantic==2.10.3` | OK |
| `pydantic-settings==2.6.1` | OK |
| `python-dotenv==1.0.1` | OK |
| `bcrypt==4.2.1` | OK |
| `itsdangerous==2.2.0` | OK |

**Missing:** `alembic`, `feedparser` (Deliverable 5 — Substack RSS),
`aiosmtplib` (Deliverable 3 — async SMTP), `pytest`, `pytest-asyncio`,
`pytest-httpx` (mocking), `aiosqlite` (fast in-memory tests),
`ruff`, `mypy`. All free, all open-source — no paid services.

---

## 11. Findings summary by deliverable

| Deliverable | Findings it must close |
|---|---|
| 2 Scheduler safety | F-20, F-21, F-30 (partially), F-34 (test), F-82 |
| 3 Token expiry | F-10, F-27, F-28, F-83 |
| 4 /studio | F-24, F-73, F-81, F-85 |
| 5 Substack fan-out | F-59, F-74, F-78 |
| 6 /repurpose | F-61, F-85 |
| 7 Calendar | F-77, F-84 |
| 8 Analytics | F-75 |
| 9 Crash recovery | F-30, F-79 |
| 10 Replit deploy | F-90, F-91, F-95 |
| 11 Always-On docs | F-96 |
| 12 Backup/restore | (new — no findings) |
| 13 Retention | F-76, F-77 (shared table) |
| 14 README rewrite | F-90 |
| 15 Production readiness | (rolls up all of the above) |
| Cross-cutting (pre-flight) | F-00, F-01, F-02, F-03, F-04 + F-05/06/07/08, F-50, F-51, F-58, F-70, F-71 |

The **cross-cutting** row must land before Deliverables 2-15: git init,
test scaffolding, Alembic, config validators, deletion of stale modules.

---

## 12. What this audit deliberately does *not* claim

- This audit did not run the application end-to-end against Neon, Meta,
  or TikTok. The verification of the atomic claim (F-34) is documented
  but not yet executed — Deliverable 2 owns the test that proves it.
- This audit did not benchmark Meta/TikTok publish latency under load;
  F-35 is qualitative.
- Several findings (F-40, F-41) are low-risk for a single-operator
  self-hosted deployment but would be blockers for SaaS use. Per the
  operator's brief this app is explicitly **not SaaS**, so they are
  noted but deferred.

— end of audit
