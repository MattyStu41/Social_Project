/*
# Add Platform Overrides (Deliverable 4)

## 1. Purpose
Support per-platform caption and media customization. This allows composing one core post and tailoring it for different platforms without creating duplicate posts.

## 2. Changes

### scheduled_posts table
Added column:
- `platform_overrides` (jsonb, NOT NULL, DEFAULT '{}') 

Structure: 
```json
{
  "threads": {"caption": "Thread-specific text", "media_url": "https://..."},
  "instagram": {"caption": "IG-specific text", "media_type": "IMAGE"}
}
```

## 3. Backward Compatibility
- Column has DEFAULT '{}' for existing rows
- Empty object {} means "use the base caption/media for all platforms"
- No data migration needed for existing posts

## 4. Security
- No security changes - table already has RLS enabled

## 5. Notes
- Overrides are optional per platform
- Empty overrides are dropped at validation time
- Each override can include: caption, media_url, media_type
- Scheduler uses _effective() helper to resolve override vs base values
*/
ALTER TABLE scheduled_posts 
ADD COLUMN IF NOT EXISTS platform_overrides jsonb NOT NULL DEFAULT '{}'::jsonb;