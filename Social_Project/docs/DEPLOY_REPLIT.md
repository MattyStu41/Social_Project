# Deploying JACK Social Scheduler on Replit

This is the full runbook. JACK runs as a single long-lived FastAPI process on
Replit Reserved VM, talking to Neon Postgres, with APScheduler in-process.

## 0. Decision: free tier vs. Reserved VM

* **Free tier (default Repl)**: the Repl sleeps when idle. APScheduler stops
  running. **Scheduled posts will not publish while the Repl is asleep.** Only
  appropriate if you visit the dashboard before every scheduled time (which
  defeats the point).
* **Reserved VM (Deployments)**: 24/7. Posts publish on schedule. Cost is
  per-month and tier-dependent; see https://replit.com/pricing — Reserved VM
  tiers typically start in the single-digit dollars per month and scale up
  with CPU/RAM. **This is the right choice for personal-use 24/7 publishing.**
* See [ALWAYS_ON.md](ALWAYS_ON.md) for a deeper trade-off discussion.

## 1. Prerequisites

1. A Neon Postgres database (free tier is fine).
2. A Meta developer app with Threads + Instagram Graph products enabled, and
   your IG Business/Creator account linked to a Facebook Page in that app.
3. A TikTok developer app with Content Posting API access.
4. A bcrypt-hashed admin password (`python scripts/hash_password.py`).
5. A session secret (`python -c "import secrets; print(secrets.token_urlsafe(64))"`).

## 2. Create the Repl

* `Create Repl → Import from GitHub` and point at this repo.
* On first import, Replit reads `.replit` and `replit.nix` automatically.
* When prompted for a Python version, Replit will use Python 3.11 (set via
  `modules = ["python-3.11"]` in `.replit`).

## 3. Attach Neon

JACK does not use Replit's built-in DB. Use Neon for production-grade Postgres.

1. In the Neon console: copy the connection string (must include
   `?sslmode=require`).
2. In Replit's Secrets (lock-icon tab), set:
   ```
   DATABASE_URL=postgresql://USER:PASS@HOST/DB?sslmode=require
   ```

JACK rewrites `postgres://` and `postgresql://` to `postgresql+asyncpg://`
automatically — paste whatever Neon gives you.

## 4. Set the rest of the environment

In Replit Secrets, set (everything that applies to you):

| Variable | Required? | What it does |
|---|---|---|
| `DATABASE_URL` | yes | Neon Postgres connection string |
| `BASE_URL` | yes | Your public Repl URL with no trailing slash, e.g. `https://jack-yourname.replit.app` |
| `ADMIN_PASSWORD_HASH` | yes | Output of `python scripts/hash_password.py` |
| `SESSION_SECRET` | yes | Output of `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `META_APP_ID` / `META_APP_SECRET` | yes | From the Meta app dashboard |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | yes | From the TikTok app dashboard |
| `META_GRAPH_VERSION` | no | Defaults to `v21.0` |
| `SCHEDULER_INTERVAL_SECONDS` | no | Defaults to 30 |
| `CIRCUIT_BREAKER_THRESHOLD` | no | Default 3 |
| `CIRCUIT_BREAKER_COOLDOWN_SECONDS` | no | Default 900 (15 min) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` / `ALERT_EMAIL` | no | All six together → enables token-expiry email alerts |
| `SUBSTACK_FEEDS` | no | Comma-separated RSS feed URLs to poll |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | no | Point at a locally-running Ollama for /repurpose rewrites |
| `RETENTION_DEFAULT_DAYS` | no | Default 365 |
| `CORS_ORIGINS` | no | Leave blank unless you serve the UI from a different origin |
| `LOG_LEVEL` | no | Defaults to `INFO` |

## 5. Register OAuth redirect URIs

In the Meta and TikTok consoles, add exactly these callback URLs (substitute
your `BASE_URL`):

```
{BASE_URL}/api/auth/threads/callback
{BASE_URL}/api/auth/instagram/callback
{BASE_URL}/api/auth/tiktok/callback
```

## 6. Boot

Hit **Run** in the Replit editor. The `scripts/replit_boot.sh` entry point:

1. `pip install -r requirements.txt`
2. If the DB has the legacy `scheduled_posts` table from a pre-Alembic install
   but no `alembic_version` row, runs `alembic stamp head` once so subsequent
   migrations apply cleanly.
3. Starts uvicorn on `0.0.0.0:8000` (Replit maps to external port 80).
4. The FastAPI lifespan runs `alembic upgrade head`, then
   `reconcile_processing_posts()`, then starts APScheduler.

When the console shows:

```
JACK Social Scheduler started (base_url=https://...)
Scheduler started (interval=30s)
```

…the app is live.

## 7. First-run checklist

```bash
curl https://YOUR-REPL.replit.app/healthz
# {"status":"ok","version":"1.0.0","auth":true}
```

1. Open `https://YOUR-REPL.replit.app/` in a browser.
2. Sign in with the admin password.
3. **Connect each platform**: click Connect on Threads, Instagram, TikTok in
   turn. You'll be redirected to the platform's OAuth consent screen and back.
4. Hit **Studio** (`/studio`) and draft a short test post (e.g. just for
   Threads). Schedule it 2 minutes from now.
5. Within 60 seconds of the scheduled time the scheduler should publish; the
   queue card flips to **published** and `platform_results.threads.post_id`
   contains the live Threads post id.
6. Repeat for Instagram (needs a publicly-reachable image URL) and TikTok
   (needs a publicly-reachable video URL; appears in your TikTok drafts).

## 8. Promote to Reserved VM

For 24/7 publishing:

1. In Replit, open **Deployments → Reserved VM**.
2. Choose a tier (the smallest tier is usually sufficient for a single-operator
   scheduler).
3. The `[deployment]` block in `.replit` already points the build + run at
   `scripts/replit_boot.sh`.
4. Re-attach the same Secrets to the Deployment (Replit treats Repl Secrets
   and Deployment Secrets separately).
5. Deploy. The Reserved VM gives you a stable URL — update `BASE_URL` to it,
   then update the OAuth redirect URIs in the Meta + TikTok consoles to match.

## 9. Routine operation

* **Daily**: glance at the dashboard for any red/orange alert banners
  (token-expiry, paused-platform, expired-creds).
* **Weekly**: check `/analytics` for sample coverage; the +24h interval is the
  most reliable on Meta surfaces.
* **Monthly**: rotate the `SESSION_SECRET` if you want to invalidate all
  active sessions (forces re-login).

## 10. Troubleshooting

* **`/healthz` returns 200 but the UI shows a 503**: `ADMIN_PASSWORD_HASH` or
  `SESSION_SECRET` is missing. Set both, restart.
* **OAuth redirect mismatch**: the URL the platform redirects to must match
  what's registered in the platform console **exactly**, including scheme.
* **Posts stuck in `processing` after a restart**: not stuck — boot runs
  `reconcile_processing_posts()` which surfaces any in-flight platforms with
  a `needs_manual_verify` flag in `platform_results`.
* **No analytics samples after publish**: APScheduler in-process schedules
  fire-once jobs at +1h/+24h/+72h/+7d. If the Repl restarted between publish
  and the fire time, the job is lost. Use **Refetch** on the post in
  `/analytics` to manually resample.
* **TikTok captions never appear on TikTok**: by design — the inbox API does
  not accept captions. Finalise the draft in the TikTok app.

## 11. Manual ops you (the operator) must do

These are explicitly outside JACK's automation:

* **Meta app review** for elevated permissions (`threads_content_publish`,
  `instagram_content_publish`). Without review, your app can only publish on
  behalf of the developer account and a handful of test users.
* **TikTok audit submission** if you want to bypass the drafts-only
  restriction (Direct Post API). Until audited, JACK uses the inbox flow.
* **Renew TikTok refresh tokens before 365 days** — TikTok rotates refresh
  tokens every refresh, but if a refresh fails for >1y the operator must
  reconnect.
