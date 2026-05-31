/*
# Post Analytics Table (Deliverable 8)

## 1. Purpose
Store per-platform engagement metrics sampled at fixed intervals after publish:
- +1h (initial engagement)
- +24h (peak reach)
- +72h (growth phase)
- +7d (long-term performance)

## 2. New Table

### post_analytics
One row per (post, platform, interval) sample:
- `id` (uuid, primary key)
- `post_id` (uuid) - references scheduled_posts.id
- `platform` (text) - "threads", "instagram", or "tiktok"
- `external_id` (text) - platform's post ID for metric lookups
- `interval_label` (text) - "+1h", "+24h", "+72h", or "+7d"
- `sampled_at` (timestamptz) - when sample was taken
- `snapshot` (jsonb) - platform-specific metrics
- `error` (text, nullable) - fetch error if failed

## 3. Platform-Specific Metrics

### Threads
views, likes, replies, reposts, quotes

### Instagram  
impressions, reach, engagement, likes, comments, shares, saved

### TikTok
Note: TikTok Content Posting API does NOT expose engagement metrics.
Drafts are finalized in-app; engagement only visible in TikTok app.
Row is still created with `error` field documenting this limitation.

## 4. Security
- RLS enabled
- Unique index on (post_id, platform, interval_label)
- Ensures idempotent upserts

## 5. Performance
- Composite unique index for efficient queries
- Allows "refetch" operations to update existing samples

## 6. Notes
- APScheduler fires one-shot jobs at scheduled times
- If process restarts between publish and fire time, job is lost
- Operator can manually trigger refetch via /analytics endpoint
- Snapshot structure varies by platform (JSONB handles this flexibly)
*/

CREATE TABLE IF NOT EXISTS post_analytics (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id uuid NOT NULL,
    platform text NOT NULL,
    external_id text NOT NULL,
    interval_label text NOT NULL,
    sampled_at timestamptz NOT NULL,
    snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text
);

-- Create unique constraint for idempotent upserts
CREATE UNIQUE INDEX IF NOT EXISTS ix_post_analytics_post_platform_interval 
ON post_analytics(post_id, platform, interval_label);

ALTER TABLE post_analytics ENABLE ROW LEVEL SECURITY;