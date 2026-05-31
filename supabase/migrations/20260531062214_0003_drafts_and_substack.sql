/*
# Drafts and Substack RSS Fan-out (Deliverable 5)

## 1. New Tables

### drafts
Review queue for content awaiting operator promotion:
- `id` (uuid, primary key)
- `source_kind` (text) - "substack" or "manual"
- `source_url` (text, nullable) - original URL
- `source_guid` (text, nullable) - unique identifier from source
- `title` (text, nullable) - post title
- `body` (text, nullable) - full text content
- `pull_quotes` (jsonb) - extracted quote candidates
- `suggested_caption` (text, nullable) - recommended caption
- `media_url` (text, nullable) - associated media
- `created_at` (timestamptz) - when draft was created
- `promoted_post_id` (uuid, nullable) - links to scheduled_posts after promotion

### substack_seen
De-duplication table for RSS poller:
- `id` (uuid, primary key)
- `feed_url` (text) - RSS feed URL
- `guid` (text) - unique item identifier from feed
- `seen_at` (timestamptz) - when item was processed

## 2. Security
- RLS enabled on both tables
- No access without explicit policies

## 3. Performance
- Index on `drafts(source_kind, created_at)` for filtering
- Unique constraint on `substack_seen(feed_url, guid)` for fast lookups

## 4. Notes
- Drafts never auto-publish; operator must explicitly promote
- Substack poller runs every 15 minutes
- Pull quotes are extracted deterministically (no AI invention)
- Maximum 3 pull quotes per draft
- Once promoted, draft keeps `promoted_post_id` reference for audit trail
*/

-- Create drafts table
CREATE TABLE IF NOT EXISTS drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_kind text NOT NULL,
    source_url text,
    source_guid text,
    title text,
    body text,
    pull_quotes jsonb NOT NULL DEFAULT '[]'::jsonb,
    suggested_caption text,
    media_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    promoted_post_id uuid
);

-- Create substack_seen table
CREATE TABLE IF NOT EXISTS substack_seen (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    feed_url text NOT NULL,
    guid text NOT NULL,
    seen_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_substack_seen_feed_guid UNIQUE (feed_url, guid)
);

-- Create index for drafts filtering
CREATE INDEX IF NOT EXISTS ix_drafts_source_kind_created_at 
ON drafts(source_kind, created_at);

-- Enable RLS
ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE substack_seen ENABLE ROW LEVEL SECURITY;