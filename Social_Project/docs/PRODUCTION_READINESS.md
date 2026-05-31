# Production Readiness Report

Date: 2026-05-31 (D16 added)
Scope: every deliverable from the brief (D1–D15). Each row cross-links the
audit finding(s) it closes and the tests that prove it.

## Test pipeline summary

```
pytest -q     → 80 passed, 1 skipped (Postgres-only concurrency test, gated on JACK_TEST_PG_URL)
ruff check .  → All checks passed
mypy .        → Success: no issues found in 45 source files
uvicorn smoke → import succeeds; lifespan runs init_schema → reconcile → scheduler; /healthz returns 200
alembic chain → 7 migrations (0001..0007) upgrade head → downgrade base → upgrade head all OK
```

To run them locally:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy .
```

## Deliverable status

| # | Deliverable | Closes (audit IDs) | Proof |
|---|---|---|---|
| 1 | Audit doc with stable F-NN IDs | — | [docs/AUDIT.md](AUDIT.md) |
| 2 | Scheduler safety (concurrency, breaker, partial-success skip) | F-20, F-21, F-32 (documented), F-34 | `tests/test_circuit_breaker.py::*` (8), `tests/test_scheduler_safety.py::*` (5), `tests/test_atomic_claim.py::test_two_concurrent_ticks_publish_each_post_exactly_once`, `tests/test_atomic_claim.py::test_two_concurrent_ticks_against_real_postgres` (gated) |
| 3 | Token refresh + expiry alerting | F-10, F-27, F-83 | `tests/test_alerts.py::*` (8), `tests/test_admin_alerts_endpoint.py::*` (2) |
| 4 | `/studio` per-platform compose | F-24, F-73, F-81, F-85 | `tests/test_studio_overrides.py::*` (9) |
| 5 | Substack RSS fan-out worker | F-59, F-74, F-78 | `tests/test_substack.py::*` (7) |
| 6 | `/repurpose` engine (+ optional Ollama) | F-61, F-85 | `tests/test_repurpose.py::*` (10) |
| 7 | `/calendar` drag-and-drop + operator timezone | F-77, F-84 | `tests/test_calendar_settings.py::*` (2) |
| 8 | Analytics pull-back + `/analytics` | F-75 | `tests/test_analytics.py::*` (4) |
| 9 | Crash recovery for `processing` posts | F-30, F-79 | `tests/test_reconcile.py::*` (4), `tests/test_crash_recovery.py::*` (3) |
| 10 | Replit deployment (`.replit`, `replit.nix`, runbook) | F-90, F-91, F-95 | [docs/DEPLOY_REPLIT.md](DEPLOY_REPLIT.md), `.replit`, `replit.nix`, `scripts/replit_boot.sh` |
| 11 | Always-On guidance | F-96 | [docs/ALWAYS_ON.md](ALWAYS_ON.md) |
| 12 | Backup + restore CLI | — | `tests/test_backup_restore.py::*` (3); secrets confirmed REDACTED |
| 13 | Retention purge + audit log | F-76, F-77 (shared `app_settings`) | `tests/test_retention_purge.py::*` (2) |
| 14 | README rewrite | F-90 | [README.md](../README.md) |
| 15 | This document | rolls up everything | — |
| 16 | Multi-account-per-platform | (new — not from original audit) | `tests/test_multi_account.py::*` (10): OAuth dedup, scheduler default-vs-pinned, list+disconnect API, schema validation |

## Audit findings, closed-or-deferred

### Closed in this pass

| ID | Title | How closed |
|---|---|---|
| F-04 .. F-08 | Stale root-level dupes (`/auth.py`, `/meta.py`, `/tiktok.py`, `/posts.py`, `/platforms.py`) | Deleted with operator approval. |
| F-10 | `refresh_expiring_tokens` swallows exceptions silently | Daily `token_expiry_check` job + `/api/admin/alerts` surface failures. |
| F-20, F-21 | Per-platform retry double-publish | F-21 fix in `_publish_one` skips platforms with `success: True`. |
| F-24 | TikTok caption silently discarded | UI in `/studio` documents that captions are set in the TikTok app. |
| F-27 | Token without `expires_at` silently skipped by refresh job | `compute_alerts` emits a warning per token; refresh job logs loudly. |
| F-30 | Crash leaves posts stuck in `processing` | Boot-time `reconcile_processing_posts` resolves them per the F-79 logic. |
| F-50, F-51 | `BASE_URL` not stripped of trailing slash, not scheme-validated | `_normalise_base_url` validator. |
| F-58 | `LOG_LEVEL` not validated | `_validate_log_level` validator (allowlist). |
| F-70, F-71 | No migration system | Alembic with 6 baseline + delta migrations. |
| F-73 | No per-platform overrides column | `0002_platform_overrides.py` + `Mapped` field. |
| F-74, F-78 | No source/dedup tables for Substack | `0003_drafts_and_substack.py`. |
| F-75 | No analytics table | `0005_post_analytics.py` + idempotent upserts. |
| F-76, F-77 | No audit log / app settings tables | `0004_app_settings.py`, `0006_audit_log.py`. |
| F-79 | `platform_results` only flushed at end of `_publish_one` | Per-platform commit before + after the network call; `in_flight` marker survives crashes. |
| F-81, F-82, F-83, F-84, F-85 | UI gaps (TikTok caveat, breaker, expiry warnings, calendar, studio/repurpose) | Five dedicated pages + alerts banner. |
| F-90, F-91, F-95 | Railway-centric, no Replit configs, no Alembic | `.replit`, `replit.nix`, `scripts/replit_boot.sh`, [docs/DEPLOY_REPLIT.md](DEPLOY_REPLIT.md). |
| F-96 | Free-tier sleep not documented | [docs/ALWAYS_ON.md](ALWAYS_ON.md). |

### Deferred (with rationale)

| ID | Title | Why deferred |
|---|---|---|
| F-00 | Not a git repository | Explicit operator decision — "do not update git, only update folder on desktop". Risk acknowledged in audit. |
| F-11 | Meta error-body parse exception is broad | Low-risk; body is still captured for the operator via `last_error`. |
| F-12, F-32 | `publish-now` race with periodic tick | Cosmetic — the post still publishes via whichever path wins. Documented in audit. |
| F-13 | Frontend `console.error` on config fetch failure | UI degrades visibly (401 will surface on next API call). |
| F-22, F-23 | Threads / IG media-polling timeouts | Hard limits; expanding them is a tunable, not a fix. |
| F-26 | Missing TikTok refresh token surfaces only on next publish | Already surfaces via `last_error`; D3 alerts also catch the underlying expiry. |
| F-28 | Token refresh doesn't round-trip-verify | Meta does not return a different subject on refresh; low real-world risk. |
| F-35 | Sequential per-platform publish in a single tick | Throughput limit, not correctness. For a single operator at 1-50 posts/day this is fine. |
| F-36 | `PostStatus.cancelled` declared but unused | Cosmetic; left for forward compat. |
| F-40 | No rate limiting on `/api/auth/login` | Single-operator self-hosted; low priority. Could be added later as in-memory limiter. |
| F-41, F-42, F-43 | Cookie attribute mismatch / private helper import / no-op fallback in `mint_oauth_state` | Cosmetic / low-risk. |
| F-52, F-53, F-54, F-57, F-58 | Per-credential validation at config time | Endpoints already 503 cleanly when creds are missing; audit alert surfaces it. |
| F-72 | No JSONB GIN index on `platform_results` | Deliberately not needed — `post_analytics` is the queryable surface for engagement; `platform_results` is operator-readable JSON. |

## Things the operator must do manually

These are explicitly outside JACK's automation. They require the operator's
identity, account, or credit card.

1. **Replit Deployments → Reserved VM.** JACK runs on Reserved VM for 24/7
   publishing. Free-tier Repls sleep and will not publish on schedule. Cost
   is per-month; verify current pricing at https://replit.com/pricing.
2. **Neon Postgres account.** Create the database, copy the connection
   string with `?sslmode=require`, paste into Replit Secrets as
   `DATABASE_URL`.
3. **Meta developer app.** Create the app, enable Threads + Instagram Graph
   products, link your IG Business / Creator account to a Facebook Page you
   manage. Register the three OAuth redirect URIs (see README).
4. **Meta app review submission** for elevated permissions
   (`threads_content_publish`, `instagram_content_publish`). Without review
   you can only publish on behalf of yourself and a handful of test users.
   This is a Meta-mandated process; JACK can't automate it.
5. **TikTok developer app.** Create the app, enable Content Posting API,
   register the OAuth redirect URI.
6. **TikTok audit submission** if you want to bypass drafts-only. Until
   audited, JACK uses the inbox flow; you finalise each TikTok in the app.
7. **bcrypt hash + session secret.** Mint these on your laptop, paste into
   Replit Secrets. Never commit to source.
8. **Register OAuth redirect URIs at the exact paths in `BASE_URL`.** If
   `BASE_URL` changes (e.g. you promote from a Repl URL to a custom domain),
   update both the env var and every redirect URI in the platform consoles.
9. **Connect each platform in the dashboard.** OAuth flows can't be
   pre-baked. Sign in once, click Connect for Threads, then Instagram, then
   TikTok.
10. **Schedule a real test post on each platform** and confirm it publishes.
    For TikTok, verify the draft appears in your TikTok app and finalise it.
11. **Take screenshots of the live dashboard.** Save into
    `docs/screenshots/` — the brief requested this as the operator-side
    artefact. Suggested shots: dashboard with all three platforms connected,
    `/studio` mid-compose, `/calendar` with a populated month, `/analytics`
    after a sample has landed, `/settings` after a dry-run purge.
12. **Operator-side decisions on:** retention threshold (default 365d),
    timezone, SMTP setup for token-expiry email alerts (all-or-nothing —
    set all six vars together), and optional `OLLAMA_BASE_URL` for
    `/repurpose` rewrites.

## Risk register (residual)

| Risk | Severity | Mitigation |
|---|---|---|
| Replit Reserved VM restarts mid-publish | Low | Per-platform commits before + after the network call (F-79); reconcile on next boot marks in-flight platforms `needs_manual_verify`. |
| Meta / TikTok API breaking changes | Medium | Code is shaped per-platform; an API change requires editing `services/meta.py` or `services/tiktok.py` and bumping `META_GRAPH_VERSION`. |
| Token refresh failure cascading into mass publish failures | Low | Circuit breaker pauses a failing platform; daily alerts surface stuck refresh. |
| Operator deletes Neon database | Critical | `scripts/backup.py` is the operator's responsibility; restore is `scripts/restore.py` (tokens REDACTED → reconnect required). |
| TikTok engagement metrics never available | Known limitation | Documented in `/analytics` UI + this report. |

## Operator runbook (steady state)

* **Daily**: glance at the dashboard. Red/orange banners are actionable.
* **When a token alert fires**: reconnect that platform from the dashboard.
* **When a platform pauses (breaker)**: read the error, fix the underlying
  issue (e.g. media URL no longer reachable), click **Resume now**.
* **After a process restart**: check the dashboard for posts marked
  `failed` with `needs_manual_verify: true`. Check the platform UI before
  hitting Retry.
* **Monthly**: run `python scripts/backup.py` and save the file off-Repl.
* **Quarterly**: review the audit log under `/settings`.

— end of report
