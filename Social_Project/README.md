# JACK Social Scheduler

**A personal, single-operator social-media scheduler.** Not a SaaS, not a
multi-tenant service. One operator, but the operator can connect **multiple
accounts per platform** (e.g. personal IG + a brand IG + Frontier Media IG)
and pick which account a post publishes from. The pipeline hits the platform
APIs directly — no monthly platform fee, no third-party scheduler in the loop,
no data shared with anyone else.

- **Threads** — text and image/video, fully auto-published.
- **Instagram** — image and Reels, fully auto-published (Business/Creator account linked to a Facebook Page).
- **TikTok** — video pushed to your drafts (TikTok API caps unaudited apps to drafts; you finalise in the TikTok app).

## Quick Start with Supabase

**Recommended: Supabase** (managed Postgres with built-in auth, real-time, and storage).

### 1. Create Supabase Project

1. Go to [supabase.com](https://supabase.com) and create a new project
2. Wait for the database to provision (~2 minutes)
3. Go to **Project Settings → Database**
4. Copy the connection string under **Connection string** → **URI**
5. Paste into `.env` as `DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres`

### 2. Generate Admin Credentials

```bash
# Generate password hash
python scripts/hash_password.py

# Generate session secret
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Copy outputs to `.env` as `ADMIN_PASSWORD_HASH` and `SESSION_SECRET`.

### 3. Create OAuth Apps

**Meta (Threads + Instagram):**
1. Go to [developers.facebook.com](https://developers.facebook.com)
2. Create app → Business → Apps for Messenger or Instagram
3. Add products: **Threads** and **Instagram Graph API**
4. Copy App ID and App Secret to `.env`
5. Add redirect URIs:
   - `{BASE_URL}/api/auth/threads/callback`
   - `{BASE_URL}/api/auth/instagram/callback`

**TikTok:**
1. Go to [developers.tiktok.com](https://developers.tiktok.com)
2. Create app → Content Posting API
3. Copy Client Key and Client Secret to `.env`
4. Add redirect URI: `{BASE_URL}/api/auth/tiktok/callback`

### 4. Install and Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Database migrations are applied automatically on first run
uvicorn main:app --reload

# Open http://localhost:8000
# Sign in with your admin password
# Connect each platform (Threads, Instagram, TikTok)
# Schedule your first post!
```

### 5. Production Deployment

See [docs/DEPLOY_REPLIT.md](docs/DEPLOY_REPLIT.md) for:
- Replit Reserved VM (recommended for 24/7 publishing)
- Environment variable configuration
- OAuth redirect URI setup
- First-run checklist

**Important:** Free-tier Repls sleep when idle. Use Reserved VM for reliable scheduled publishing.

Built on FastAPI + SQLAlchemy 2 + asyncpg + APScheduler, designed for **Supabase
Postgres** (or Neon), deployed on Replit Reserved VM (single long-running process).

## Pages

| Path | What it does |
|---|---|
| `/` | Dashboard: connect/disconnect accounts (one per row, multiple per platform), see the queue + status, see alerts, retry failures |
| `/studio` | Compose one post + per-platform overrides (caption, media). Limits enforced before scheduling, no silent truncation. |
| `/calendar` | Month grid of all scheduled / published / failed posts. Drag a pill to reschedule. Operator timezone stored in user settings. |
| `/repurpose` | Paste long-form text; deterministic sentence-boundary splitter produces per-platform candidates. Optional local Ollama rewrite. |
| `/analytics` | Per-post + weekly samples of impressions / engagement pulled from platform APIs at +1h, +24h, +72h, +7d. |
| `/settings` | Timezone, default retention, purge published posts older than N days, audit log of destructive ops. |

## Architecture

```
┌─────────────────────────┐         ┌──────────────────────┐
│ Static SPA (vanilla JS) │ ──────► │ FastAPI (/api/*)     │
│  (one HTML page per     │         │  ├─ posts            │
│   route, sharing the    │         │  ├─ drafts           │
│   /api endpoints)       │         │  ├─ studio overrides │
└─────────────────────────┘         │  ├─ repurpose split  │
                                    │  ├─ analytics        │
                                    │  ├─ settings + audit │
                                    │  ├─ auth (OAuth)     │
                                    │  ├─ platforms        │
                                    │  └─ admin            │
                                    └──┬─────────┬─────────┘
                                       │         │
                                APScheduler   Supabase/Neon Postgres
                                  (in-proc)     (Migration-managed)
                                       │
                              ┌────────┼─────────┐
                              ▼        ▼         ▼
                       Meta Graph   TikTok      Substack RSS
                       (Threads+IG) (Content    (poller every 15m,
                                     Posting)    optional)
```

## Cron / interval jobs (APScheduler, in-process)

| Job | Cadence | Purpose |
|---|---|---|
| `publish_scheduler` | every `SCHEDULER_INTERVAL_SECONDS` (default 30s) | Atomic claim + publish of any pending post whose `scheduled_at` is in the past |
| `token_refresher` | every 6h | Refresh Meta / TikTok tokens within 48h of expiry |
| `token_expiry_alerts` | every 24h | Compute operator alerts (expiry, paused platforms) + optional email digest |
| `substack_poller` | every 15m (only if `SUBSTACK_FEEDS` is set) | Pull new Substack posts into the drafts queue with 3 pull-quote candidates |
| `analytics:<post>:<platform>:<interval>` | one-shot at +1h, +24h, +72h, +7d after publish | Fetch platform insights, write a snapshot row |

## Environment variables

Required:

| Variable | Notes |
|---|---|
| `DATABASE_URL` | **Supabase** or Neon Postgres connection string. Supabase: Get from Project Settings → Database. |
| `BASE_URL` | Public URL, no trailing slash, scheme required (`http://` or `https://`). |
| `ADMIN_PASSWORD_HASH` | bcrypt(12) hash from `python scripts/hash_password.py`. |
| `SESSION_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Rotate to invalidate all sessions. |
| `META_APP_ID` / `META_APP_SECRET` | From Meta developer dashboard. |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | From TikTok developer dashboard. |

Optional / tunable:

| Variable | Default | Notes |
|---|---|---|
| `META_GRAPH_VERSION` | `v21.0` | Bump when Meta releases a new Graph version. |
| `SCHEDULER_INTERVAL_SECONDS` | 30 | Drain cadence; lower = faster pickup, higher = less DB load. Range 10–300. |
| `SESSION_TTL_HOURS` | 336 (14d) | Cookie lifetime. |
| `CORS_ORIGINS` | empty | Comma-separated origins. Leave blank for same-origin self-hosted. |
| `LOG_LEVEL` | `INFO` | Validated against DEBUG / INFO / WARNING / ERROR / CRITICAL. |
| `CIRCUIT_BREAKER_THRESHOLD` | 3 | Consecutive failures before a platform is paused. |
| `CIRCUIT_BREAKER_COOLDOWN_SECONDS` | 900 (15 min) | Pause length when the breaker trips. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` / `ALERT_EMAIL` | empty | All six required together to enable email alerts; missing any disables email (dashboard banner still works). |
| `SUBSTACK_FEEDS` | empty | Comma-separated RSS feed URLs. Empty = poller disabled. |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | empty / `llama3.2` | Optional locally-running Ollama for `/repurpose` rewrites. Degrades silently if unreachable. |
| `RETENTION_DEFAULT_DAYS` | 365 | Default age threshold for the `/settings` purge. |
| `DISABLE_SCHEDULER` | false | Used by tests + one-shot CLIs; never set in production. |

## OAuth redirect URIs

Register these exact paths in each platform console, substituting your
`BASE_URL`:

```
{BASE_URL}/api/auth/threads/callback
{BASE_URL}/api/auth/instagram/callback
{BASE_URL}/api/auth/tiktok/callback
```

## Replit deployment

See [docs/DEPLOY_REPLIT.md](docs/DEPLOY_REPLIT.md) for the full runbook
(Reserved VM is required for 24/7 publishing — see
[docs/ALWAYS_ON.md](docs/ALWAYS_ON.md) for the trade-off). Short version:

1. Import this repo into Replit.
2. Add all required env vars (table above) as Secrets.
3. Hit Run. `scripts/replit_boot.sh` installs deps, stamps Alembic if needed,
   and starts uvicorn on `0.0.0.0:8000` (external port 80).
4. The FastAPI lifespan applies pending Alembic migrations, reconciles any
   posts left in `processing` from a prior crash (F-30), and starts APScheduler.
5. Register the three OAuth redirect URIs in the Meta and TikTok consoles.
6. Sign in with the admin password, connect each platform, schedule a test
   post.
7. For 24/7: promote to **Reserved VM** in Replit Deployments.

## Local development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes pytest, ruff, mypy

cp .env.example .env
# Fill in DATABASE_URL (Neon scratch), BASE_URL=http://localhost:8000,
# ADMIN_PASSWORD_HASH, SESSION_SECRET, the Meta + TikTok creds.
python scripts/hash_password.py       # to mint the password hash

uvicorn main:app --reload
```

For OAuth callbacks to work locally, either register
`http://localhost:8000/api/auth/*/callback` as redirect URIs in the Meta /
TikTok consoles or front the dev server with a tunnel like `cloudflared`.

### Tests

```bash
pytest -q                      # 70+ tests; one PG-only test gated on
                               # JACK_TEST_PG_URL
ruff check .                   # must be clean
mypy .                         # must be clean
```

## Data model (Alembic-managed)

| Table | Owns |
|---|---|
| `scheduled_posts` | The queue: caption, target platforms, per-platform overrides, scheduled time, platform-results JSON, attempt log |
| `platform_tokens` | One row per platform: access_token, refresh_token, expiry, account label |
| `drafts` | Substack-sourced (or manual) draft candidates the operator promotes into scheduled_posts |
| `substack_seen` | De-dup for the RSS poller, keyed on (feed_url, guid) |
| `post_analytics` | (post_id, platform, interval) snapshots from the +1h/+24h/+72h/+7d follow-ups |
| `app_settings` | Operator preferences (timezone, retention_days) |
| `audit_log` | Destructive-op trail (purge_dry_run, purge_executed) |

Migrations live under `alembic/versions/`. The boot path runs
`alembic upgrade head` automatically. If you have an existing pre-Alembic
database, run `alembic stamp head` once first.

## API

All `/api/posts/*`, `/api/drafts/*`, `/api/platforms/*`, `/api/repurpose/*`,
`/api/analytics/*`, `/api/settings/*`, and `/api/auth/*/login` endpoints
require the admin cookie. OAuth callbacks are open (they verify the signed
`state` parameter).

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/login` | `{ password }` → sets session cookie |
| `POST` | `/api/auth/logout` | clears cookie |
| `GET` | `/api/auth/session` | `{ authenticated, auth_required }` |
| `GET` | `/api/auth/{platform}/login` | redirects to platform OAuth |
| `GET` | `/api/auth/{platform}/callback` | OAuth callback, persists token |
| `DELETE` | `/api/auth/{platform}/disconnect` | wipes one account (use `?account_id=<uuid>` when multiple are connected) |
| `GET` | `/api/platforms/status` | per-platform connected-accounts list + breaker state |
| `POST` | `/api/platforms/{platform}/resume` | manually reset the circuit breaker |
| `POST` | `/api/posts` | schedule a post (+ optional `platform_overrides` + optional `platform_accounts` pinning) |
| `GET` | `/api/posts` | list (filter by `?status=`) |
| `GET` | `/api/posts/{id}` | one post |
| `PATCH` | `/api/posts/{id}` | edit (pending or failed); also used by the calendar's drag-and-drop |
| `POST` | `/api/posts/{id}/retry` | re-queue a failed post (skips already-succeeded platforms) |
| `POST` | `/api/posts/{id}/publish-now` | trigger immediately |
| `DELETE` | `/api/posts/{id}` | remove |
| `GET` | `/api/drafts` | list drafts not yet promoted |
| `GET` | `/api/drafts/{id}` | one draft + its pull-quote candidates |
| `DELETE` | `/api/drafts/{id}` | discard |
| `POST` | `/api/drafts/{id}/promote` | turn a draft into a ScheduledPost |
| `POST` | `/api/repurpose/split` | long body → per-platform candidates |
| `POST` | `/api/repurpose/rewrite` | optional Ollama rewrite (operator approves before use) |
| `GET` | `/api/analytics/posts` | recent posts with their sample rows |
| `GET` | `/api/analytics/weekly` | per-week aggregates |
| `POST` | `/api/analytics/posts/{id}/refetch` | re-sample the +24h interval |
| `GET` | `/api/settings` | current preferences |
| `PATCH` | `/api/settings` | update preferences (timezone, retention_days) |
| `GET` | `/api/settings/purge` | preview the purge (no deletion) |
| `POST` | `/api/settings/purge` | execute the purge (idempotent; audit-logged) |
| `GET` | `/api/settings/audit` | last 100 destructive actions |
| `GET` | `/api/admin/config` | public — which platforms have creds configured + auth flag |
| `GET` | `/api/admin/alerts` | current alerts (auth required) |
| `GET` | `/healthz` | health probe |

Interactive docs at `/api/docs`.

## Multiple accounts per platform (D16)

The dashboard's **Connections** section shows one row per connected account.
Click **Connect another** to add a second (or third) account on the same
platform — each runs through the same OAuth flow and JACK distinguishes by
the platform's user_id.

When you compose a post and more than one account is connected for a target
platform, the form shows a dropdown so you can pick the account. With one
account, no dropdown — JACK uses it automatically. The pinned choice
persists on the post as `platform_accounts`; the scheduler honors it at
publish time.

The circuit breaker is intentionally **per-platform, not per-account**:
when Meta is down, every Meta account is affected, so a trip applies to the
whole platform. Token-expiry alerts are per-account so each account that's
within 7 days of expiring shows up separately.

## Operational notes

- **No double-publish.** The drain claims rows atomically
  (`UPDATE … RETURNING` flipping `pending → processing`). The
  per-platform skip-on-retry fix (F-21) prevents republishing a platform
  that already succeeded.
- **Circuit breaker.** After `CIRCUIT_BREAKER_THRESHOLD` (default 3)
  consecutive failures on a platform, that platform is paused for
  `CIRCUIT_BREAKER_COOLDOWN_SECONDS` (default 15 min). The dashboard surfaces
  this and offers a "Resume now" button.
- **Crash recovery.** On boot, `reconcile_processing_posts` resolves any
  post left in `processing`: marks all-success as `published`, partial-success
  as `failed` (next retry skips already-succeeded platforms), in-flight as
  `failed` with a `needs_manual_verify` flag so the operator can check the
  platform UI before retrying.
- **TikTok is drafts-by-design.** This is a TikTok API restriction, not a
  code limit. Captions are entered in the TikTok app; JACK stores the
  caption locally for reference but does not send it to TikTok.
- **TikTok engagement metrics are unavailable** via the Content Posting API.
  The analytics page documents this; engagement is visible in the TikTok app.
- **CORS is closed by default.** Set `CORS_ORIGINS` only if you serve the UI
  from a different origin.
- **Backup / restore.** `python scripts/backup.py` dumps all tables to a
  single JSON file (token secrets redacted). `python scripts/restore.py
  path/to/backup.json` re-imports on a fresh DB; you must reconnect each
  platform via OAuth after a restore.

## Known limitations

These are deliberate scope choices, not bugs:

- **No video-duration / aspect-ratio validation at draft time.** Validating
  these requires fetching the media URL and inspecting the file, which would
  add a per-draft third-party network call. Out-of-spec media still publishes
  (Meta/TikTok crop / letterbox or reject); failures surface in
  `platform_results[p].error` and trip the circuit breaker.
- **TikTok inbox API discards captions.** This is platform-imposed; captions
  are entered in the TikTok app when you finalise the draft. JACK stores the
  caption locally for reference.
- **TikTok engagement metrics are unavailable** via the Content Posting API.
  The `/analytics` page documents this; engagement is visible in the TikTok app.
- **Analytics follow-up jobs are in-process.** If the Reserved VM restarts
  between publish and a scheduled +24h sample, that sample is lost. The
  `/analytics` page has a **Refetch** button per post.
- **Backups redact platform tokens.** Restoring requires reconnecting each
  platform via OAuth. This is intentional — backup files should never carry
  publish credentials.
- **No git history** (operator-chosen). Edits land directly on the working
  copy. Diff-via-`git log` is unavailable; rely on the audit log + this
  README + per-deliverable docs.

## Costs

- Meta Graph API: free.
- TikTok Content Posting API: free.
- Neon free tier: enough for tens of thousands of posts.
- Replit Reserved VM: low single-digit USD / mo at the smallest tier (verify
  on https://replit.com/pricing). Free tier sleeps and is not appropriate
  for scheduled publishing — see [docs/ALWAYS_ON.md](docs/ALWAYS_ON.md).

So: **$0 in API fees**, single-digit USD / mo for hosting.

## Security model

- Single admin user. Password is bcrypt-hashed (cost 12) in env. The container
  never sees plaintext.
- Sessions are signed (`itsdangerous`) with `SESSION_SECRET`. Rotate by
  changing the env var — all sessions invalidate.
- OAuth state is an HMAC-signed payload bound to the target platform and a
  10-minute window; replay-proof.
- All `/api/*` mutating endpoints require `require_admin`. OAuth callbacks
  verify `state` before persisting tokens. The only unauth endpoints are
  `/healthz`, `/api/admin/config` (no secrets), the auth surface itself,
  and the OAuth callbacks.
- Tokens live in Postgres only. Rotate by disconnecting + reconnecting from
  the UI.
- Backups redact `access_token` and `refresh_token` before writing.

## Project layout

```
.
├── main.py                  # FastAPI app + lifespan (init_schema → reconcile → scheduler start)
├── config.py                # pydantic-settings + validators
├── database.py              # async engine, Base, init_schema (Alembic on Postgres, create_all on SQLite tests)
├── models.py                # ScheduledPost, PlatformToken, Draft, SubstackSeen, PostAnalytics, AppSetting, AuditLog
├── schemas.py               # request/response shapes, per-platform validation
├── scheduler.py             # APScheduler jobs (publish, refresh, alerts, substack) + crash reconcile
├── security.py              # bcrypt, sessions, OAuth state
├── logging_config.py        # JSON logs
├── routers/                 # FastAPI routers (admin, auth, posts, platforms, drafts, repurpose,
│                             analytics, settings)
├── services/                # platform clients + cross-cutting services
│   ├── meta.py              # Threads + Instagram
│   ├── tiktok.py            # TikTok Content Posting
│   ├── circuit_breaker.py   # per-platform breaker
│   ├── alerts.py            # alert computation + SMTP
│   ├── substack.py          # RSS poller + pull-quote extraction
│   ├── repurpose.py         # deterministic splitter + optional Ollama
│   └── analytics.py         # per-platform metric fetchers + APScheduler date-trigger plumbing
├── static/                  # one HTML + JS per page (index, studio, calendar, repurpose, analytics, settings)
├── alembic/                 # migration scripts (0001..0006)
├── alembic.ini
├── scripts/
│   ├── hash_password.py
│   ├── backup.py            # JSON dump (secrets redacted)
│   ├── restore.py           # re-import onto a fresh DB
│   └── replit_boot.sh       # Replit entry point
├── tests/                   # pytest + asyncio fixtures (SQLite in-memory)
├── docs/
│   ├── AUDIT.md             # codebase audit with stable F-NN finding IDs
│   ├── DEPLOY_REPLIT.md     # Replit setup runbook
│   ├── ALWAYS_ON.md         # tier trade-off
│   └── PRODUCTION_READINESS.md
├── requirements.txt         # runtime deps
├── requirements-dev.txt     # tests + lint + types
├── pyproject.toml           # ruff + mypy + pytest config
├── Dockerfile               # for self-hosted Docker, Fly.io, etc.
├── Procfile                 # for Heroku / Render-style PaaS
├── railway.toml             # for Railway (retained as alternative target)
├── nixpacks.toml            # for Nixpacks-based hosts
├── .replit                  # Replit Reserved-VM deployment config
├── replit.nix               # Replit Nix system deps
└── .env.example
```

## License

Private / internal. Not for resale.
