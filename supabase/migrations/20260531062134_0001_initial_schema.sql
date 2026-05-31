/*
# Initial Schema Baseline

This migration establishes the core foundation for JACK Social Scheduler:

## 1. New Tables

### scheduled_posts
The main queue table that drives the scheduler:
- `id` (uuid, primary key) - unique post identifier
- `caption` (text) - the post content/caption
- `platforms` (jsonb) - array of target platforms ["threads", "instagram", "tiktok"]
- `media_url` (text, nullable) - URL to media asset
- `media_type` (text, nullable) - IMAGE or VIDEO
- `scheduled_at` (timestamptz) - when the post should be published
- `status` (enum) - pending/processing/published/failed/cancelled
- `platform_results` (jsonb) - per-platform publish results and external IDs
- `attempts` (jsonb) - audit trail of publish attempts
- `last_error` (text, nullable) - most recent error message
- `created_at`/`updated_at` (timestamptz) - timestamps

### platform_tokens
OAuth tokens for connected platforms (supports multiple accounts per platform):
- `id` (uuid, primary key)
- `platform` (text) - "threads", "instagram", or "tiktok"
- `access_token` (text) - OAuth access token
- `refresh_token` (text, nullable) - OAuth refresh token
- `user_id` (text, nullable) - platform's user ID
- `account_label` (text, nullable) - human-readable label like "@username"
- `expires_at` (timestamptz, nullable) - token expiration time
- `created_at`/`updated_at` (timestamptz) - timestamps

## 2. Security
- RLS enabled on all tables
- Tables are locked down by default (no access without policies)
- Policies will be added after initial data is migrated

## 3. Performance
- Composite index on `scheduled_posts(status, scheduled_at)` for efficient queue queries
- This index is critical for the scheduler's drain loop

## 4. Notes
- The `post_status` enum is created first to support the scheduled_posts table
- All timestamps use `timestamptz` for proper timezone handling
- JSON columns use `jsonb` for better query performance and indexing capabilities
*/

-- Create post_status enum
CREATE TYPE post_status AS ENUM ('pending', 'processing', 'published', 'failed', 'cancelled');

-- Create scheduled_posts table
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    caption text NOT NULL,
    platforms jsonb NOT NULL,
    media_url text,
    media_type text,
    scheduled_at timestamptz NOT NULL,
    status post_status NOT NULL DEFAULT 'pending',
    platform_results jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Create platform_tokens table
CREATE TABLE IF NOT EXISTS platform_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform text NOT NULL,
    access_token text NOT NULL,
    refresh_token text,
    user_id text,
    account_label text,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Create index for scheduler queue queries
CREATE INDEX IF NOT EXISTS ix_scheduled_posts_status_scheduled_at 
ON scheduled_posts(status, scheduled_at);

-- Enable RLS on all tables
ALTER TABLE scheduled_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_tokens ENABLE Row Level Security;