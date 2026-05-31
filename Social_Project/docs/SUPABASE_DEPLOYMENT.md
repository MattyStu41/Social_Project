# JACK Social Scheduler - Complete Supabase Deployment Guide

This guide walks you through deploying JACK Social Scheduler with Supabase from start to finish.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Supabase Setup](#supabase-setup)
3. [Platform OAuth Setup](#platform-oauth-setup)
4. [Application Deployment](#application-deployment)
5. [First Run Checklist](#first-run-checklist)
6. [Production Considerations](#production-considerations)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

Before starting, you'll need:

- [ ] Supabase account (free tier works fine)
- [ ] Meta Developer account
- [ ] TikTok Developer account
- [ ] Python 3.11+ installed locally
- [ ] A hosting platform (Replit Reserved VM recommended, or any VPS)

## Supabase Setup

### 1. Create Supabase Project

1. Go to [supabase.com](https://supabase.com) and sign in
2. Click **New Project**
3. Choose an organization
4. Name your project (e.g., `jack-scheduler`)
5. Set a strong database password (save this!)
6. Choose a region closest to you
7. Click **Create new project** and wait ~2 minutes

### 2. Get Database Connection String

1. Go to **Project Settings** (gear icon) → **Database**
2. Scroll to **Connection string** section
3. Select **URI** format
4. Copy the connection string
5. Replace `[YOUR-PASSWORD]` with your database password

**Example:**
```
postgresql://postgres.xxxxx:YOUR-PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres
```

### 3. Database Schema

The schema is already set up! Supabase migrations were applied automatically when you created this project through the migration system. You should see these tables in the **Table Editor**:

- `scheduled_posts` - Queue of scheduled posts
- `platform_tokens` - OAuth tokens for connected accounts
- `drafts` - Substack-sourced drafts awaiting promotion
- `substack_seen` - De-duplication for RSS poller
- `app_settings` - Operator preferences
- `post_analytics` - Engagement metrics snapshots
- `audit_log` - Permanent trail of destructive actions

**Security:** All tables have Row Level Security (RLS) enabled. Only authenticated requests can access data.

## Platform OAuth Setup

### Meta (Threads + Instagram)

#### 1. Create Meta App

1. Go to [developers.facebook.com](https://developers.facebook.com)
2. Click **My Apps** → **Create App**
3. Select **Business** as app type
4. Fill in basic info:
   - App name: `JACK Scheduler`
   - Contact email: your email
   - Business account: select or create
5. Click **Create App**

#### 2. Add Products

1. In your app dashboard, click **Add Products**
2. Find **Threads** and click **Set Up**
   - This enables Threads publishing
3. Find **Instagram Graph API** and click **Set Up**
   - This enables Instagram publishing

#### 3. Get Credentials

1. Go to **App Settings** → **Basic**
2. Copy:
   - **App ID** → Save as `META_APP_ID`
   - **App Secret** → Save as `META_APP_SECRET`
3. Both are already set in your `.env` with placeholder values

#### 4. Configure OAuth Redirect URIs

This is critical! The URIs must match exactly:

1. Go to **App Settings** → **Basic**
2. Scroll to **Add Platform**
3. Click **Website**
4. For each platform, add these exact URIs:
   ```
   https://your-domain.com/api/auth/threads/callback
   https://your-domain.com/api/auth/instagram/callback
   ```
   
   For local testing:
   ```
   http://localhost:8000/api/auth/threads/callback
   http://localhost:8000/api/auth/instagram/callback
   ```

#### 5. Link Instagram Account (Important!)

Instagram requires a Business/Creator account linked to a Facebook Page:

1. Open **Meta Business Suite** (business.facebook.com)
2. Go to **Settings** → **Accounts** → **Instagram Accounts**
3. Click **Add**
4. Connect your Instagram Business/Creator account
5. Link it to a Facebook Page you manage
6. The Page must be added to your Meta App (App Settings → Roles → Test Users)

**Without this step, Instagram publishing will fail with "No Instagram Business account linked."**

### TikTok

#### 1. Create TikTok App

1. Go to [developers.tiktok.com](https://developers.tiktok.com)
2. Click **Console** → **Create App**
3. Fill in:
   - App name: `JACK Scheduler`
   - App category: **Productivity**
   - Industry: **Social Media Management**
4. Click **Create**

#### 2. Enable Content Posting API

1. In app dashboard, find **Products**
2. Enable **Content Posting API**
3. This automatically adds `user.info.basic` and `video.upload` scopes

#### 3. Get Credentials

1. Go to **App Settings**
2. Copy:
   - **Client Key** → Save as `TIKTOK_CLIENT_KEY`
   - **Client Secret** → Save as `TIKTOK_CLIENT_SECRET`

#### 4. Configure Redirect URI

TikTok uses a single redirect URI:

```
https://your-domain.com/api/auth/tiktok/callback
```

For local testing:
```
http://localhost:8000/api/auth/tiktok/callback
```

#### 5. TikTok Limitations (Important!)

**Unaudited apps can only upload to drafts (not direct publish):**
- Videos appear in your TikTok app's drafts
- You finalize captions and publish *in the TikTok app*
- JACK stores the caption locally for reference
- Engagement metrics are NOT available via API

To request direct publish capability, submit for **TikTok App Audit** in the developer console. This requires:
- Verified business
- Clear privacy policy
- Demonstrated legitimate use case

## Application Deployment

### Option 1: Local Development

```bash
# 1. Clone and setup
cd Social_Project
python3 -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
pip install -r requirements.txt

# 2. Generate secure credentials
python scripts/hash_password.py  # Follow prompts
python -c "import secrets; print(secrets.token_urlsafe(64))"

# 3. Update .env with your values
# - Paste DATABASE_URL from Supabase
# - Paste ADMIN_PASSWORD_HASH from step 2
# - Paste SESSION_SECRET from step 2
# - Add META_APP_ID and META_APP_SECRET
# - Add TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET

# 4. Update BASE_URL for OAuth
# Local: http://localhost:8000
# Production: https://your-domain.com

# 5. Start the server
uvicorn main:app --reload

# 6. Open http://localhost:8000
```

### Option 2: Replit Deployment (Recommended)

#### Why Replit Reserved VM?

- **Always-on**: Scheduler runs 24/7 without sleep
- **Single process**: APScheduler runs in-process (simple)
- **Automatic HTTPS**: Secure OAuth callbacks
- **Simple deployment**: Just push and run

#### Steps

1. **Create Repl**
   - Go to [replit.com](https://replit.com)
   - Create new Repl → Import from GitHub
   - Paste your repo URL
   - Click **Import from GitHub**

2. **Set Environment Variables**
   - Click **Secrets** (lock icon)
   - Add all variables from `.env`:
     ```
     DATABASE_URL=postgresql://...
     BASE_URL=https://your-repl.your-username.repl.co
     ADMIN_PASSWORD_HASH=...
     SESSION_SECRET=...
     META_APP_ID=...
     META_APP_SECRET=...
     TIKTOK_CLIENT_KEY=...
     TIKTOK_CLIENT_SECRET=...
     ```

3. **Deploy to Reserved VM**
   - Click **Deployments** tab
   - Select **Reserved VM**
   - Choose smallest tier (usually sufficient for single operator)
   - Click **Deploy**

4. **Update OAuth Redirect URIs**
   - After deployment, copy your Repl's URL (e.g., `https://jack-scheduler.yourname.repl.co`)
   - Update Meta and TikTok apps with production redirect URIs
   - Update `BASE_URL` in Secrets

5. **Run**
   - The Repl automatically boots on push
   - `scripts/replit_boot.sh` runs Alembic migrations
   - Health check: visit `https://your-repl.repl.co/healthz`

### Option 3: Docker / VPS

```dockerfile
# Use the provided Dockerfile
docker build -t jack-scheduler .
docker run -d \
  -p 8000:8000 \
  -e DATABASE_URL="..." \
  -e BASE_URL="https://your-domain.com" \
  -e ADMIN_PASSWORD_HASH="..." \
  -e SESSION_SECRET="..." \
  -e META_APP_ID="..." \
  -e META_APP_SECRET="..." \
  -e TIKTOK_CLIENT_KEY="..." \
  -e TIKTOK_CLIENT_SECRET="..." \
  --name jack \
  jack-scheduler
```

## First Run Checklist

After deployment, complete this checklist:

### 1. Verify Health

```bash
curl https://your-domain.com/healthz
# {"status":"ok","version":"1.0.0","auth":true}
```

### 2. Sign In

1. Open `https://your-domain.com/` in browser
2. Sign in with the admin password you hashed
3. You should see the dashboard

### 3. Connect Platforms

For each platform (Threads, Instagram, TikTok):

1. Click **Connect** button
2. Authorize in the popup window
3. Grant permissions
4. Verify you're redirected back and see "Connected"

### 4. Schedule Test Posts

**Threads Test:**
1. Go to `/studio`
2. Caption: `Test post from JACK Scheduler`
3. Check **Threads** platform
4. Select a time 2 minutes in the future
5. Click **Schedule**
6. Wait for the scheduled time
7. Post should auto-publish

**Instagram Test:**
1. Go to `/studio`
2. Caption: `IG test post`
3. Media URL: A public image URL (must be publicly accessible)
4. Media type: **Image**
5. Check **Instagram** platform
6. Schedule and verify

**TikTok Test:**
1. Go to `/studio`
2. Caption: `TikTok test`
3. Media URL: A public video URL
4. Media type: **Video**
5. Check **TikTok** platform
6. Schedule
7. Video appears in your TikTok app's drafts
8. Finalize and publish in TikTok app

### 5. Verify Analytics

1. Wait 1 hour after publishing
2. Go to `/analytics`
3. You should see engagement metrics (except TikTok)
4. Click **Refetch** to manually pull latest data

### 6. Test Calendar Drag-and-Drop

1. Go to `/calendar`
2. Navigate to a day with a scheduled post
3. Click and drag a post pill to a different day
4. Refresh page
5. Verify post moved to the new date

### 7. Test Retention Purge

1. Go to `/settings`
2. Click **Preview** under "Purge published posts"
3. Review which posts would be deleted
4. Only proceed if you understand the impact
5. Every purge is logged in the audit trail

## Production Considerations

### Security

1. **Password Management**
   - Use a strong admin password
   - Rotate `SESSION_SECRET` to invalidate all sessions
   - Keep `.env` file secure (never commit to git)

2. **HTTPS**
   - Always use HTTPS in production
   - OAuth requires HTTPS
   - Let's Encrypt provides free certificates

3. **Database**
   - Supabase handles SSL automatically
   - Connection strings already include security parameters
   - RLS policies prevent unauthorized access

### Monitoring

1. **Health Checks**
   - Set up uptime monitoring (UptimeRobot, Pingdom)
   - Monitor `/healthz` endpoint
   - Alert on 5xx responses

2. **Logs**
   - Replit: View in console
   - Docker: `docker logs jack`
   - Logs are JSON-formatted for easy parsing

3. **Alerts**
   - Set up SMTP for token expiry alerts
   - Dashboard shows alerts without email
   - Circuit breaker pauses failing platforms

### Performance

1. **Connection Pooling**
   - Default pool size: 5
   - Max overflow: 5
   - Adjust in `database.py` for high volume

2. **Scheduler Throttle**
   - Default interval: 30 seconds
   - Adjust `SCHEDULER_INTERVAL_SECONDS` in `.env`
   - Lower = faster publishing, higher DB load

### Backup Strategy

1. **Database Backups**
   - Supabase: Automatic daily backups (Pro plan)
   - Free tier: Use backup script
   ```bash
   python scripts/backup.py --out backup-$(date +%Y-%m-%d).json
   ```

2. **Restore Process**
   ```bash
   python scripts/restore.py backup-2026-05-31.json
   # Reconnect OAuth tokens (secrets are redacted in backups)
   ```

## Troubleshooting

### Common Issues

#### "DATABASE_URL is not set"

**Problem:** Environment variable missing
**Solution:** Add `DATABASE_URL` to your `.env` or Supabase secrets

#### "Admin auth is not configured"

**Problem:** `ADMIN_PASSWORD_HASH` or `SESSION_SECRET` missing
**Solution:** Generate and set both in environment

#### "OAuth redirect mismatch"

**Problem:** Redirect URIs don't match exactly
**Solution:** 
- Check `BASE_URL` has no trailing slash
- Ensure OAuth URIs in platform consoles match exactly
- Include scheme (`https://`)

#### "Instagram requires media_url"

**Problem:** Attempting to post without media
**Solution:** Instagram only supports image/video posts, not text-only

#### "TikTok caption doesn't appear"

**Expected:** TikTok inbox API discards captions
**Solution:** Caption is stored locally for reference; set it in TikTok app

#### "Post stuck in Processing"

**Problem:** Scheduler crashed mid-publish
**Solution:** App automatically reconciles at boot:
- Check last platform that succeeded
- Retry skips already-published platforms
- Review `platform_results` JSON for details

#### "Platform paused (circuit breaker)"

**Problem:** Too many consecutive failures
**Solution:**
1. Check logs for root cause (e.g., bad media URL)
2. Fix underlying issue
3. Click **Resume now** in dashboard

#### "Token expired" alert

**Problem:** OAuth token near expiry
**Solution:**
- Automatic refresh runs every 6 hours
- If refresh fails, reconnect platform via dashboard
- Check logs for refresh errors

### Getting Help

1. **Logs** - Check application logs first
2. **Database** - Query `audit_log` table for recent actions
3. **Platform Status** - Check Meta/TikTok status pages
4. **Tests** - Run `pytest -q` to verify logic locally

### Emergency Recovery

If the scheduler is stuck:

1. **Stop the server**
2. **Check database** for posts in `processing` status
   ```sql
   SELECT id, status, platform_results 
   FROM scheduled_posts 
   WHERE status = 'processing';
   ```
3. **Manual reconciliation:**
   - Set status to `failed` for posts that never published
   - Set status to `published` for posts with all-success `platform_results`
4. **Restart server** - boot reconciliation handles the rest

## Cost Breakdown

### Supabase (Free Tier)
- 500MB database
- 5GB bandwidth
- 50,000 monthly active users
- 1GB file storage

**Cost:** $0/month for typical single-operator use

### Replit Reserved VM
- Always-on process
- Smallest tier: ~$7/month (verify pricing)
- Includes hosting + automatic HTTPS

**Total Monthly Cost:** ~$7/month for full 24/7 operation

---

## Next Steps

After successful deployment:

1. **Set timezone** in `/settings` for accurate calendar display
2. **Configure retention** for automatic old-post cleanup
3. **Add Substack feeds** (optional) for content ideas
4. **Set up Ollama** (optional) for AI-assisted repurposing
5. **Export backup** and store securely

Your JACK Social Scheduler is now ready for production use! 🎉
