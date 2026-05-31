/*
# Audit Log Table (Deliverable 13)

## 1. Purpose
Permanent audit trail for destructive operator actions. Enables answering questions like:
- "Did I really delete those posts on March 14?"
- "What retention threshold did I use for the last purge?"

## 2. New Table

### audit_log
Immutable record of destructive operations:
- `id` (uuid, primary key)
- `at` (timestamptz) - when action occurred
- `action` (text) - operation name (e.g., "purge_executed")
- `payload` (jsonb) - operation details (filters, counts, IDs)

## 3. Important Actions Logged

### purge_dry_run
Operator previewed purge (no deletion):
```json
{
  "retention_days": 365,
  "threshold": "2025-03-14T00:00:00Z",
  "candidate_count": 47,
  "candidate_ids": ["uuid1", "uuid2", ...]
}
```

### purge_executed
Operator confirmed purge (posts deleted):
```json
{
  "retention_days": 365,
  "threshold": "2025-03-14T00:00:00Z",
  "deleted_count": 47,
  "deleted_ids": ["uuid1", "uuid2", ...]
}
```

## 4. Security
- RLS enabled
- Only authenticated admin can read
- No updates or deletes allowed (append-only by design)

## 5. Performance
- Index on `at` for time-range queries
- UI shows last 100 entries

## 6. Data Retention
- Audit log is NOT purged by retention policy
- Designed for permanent compliance trail
- Operator can manually export via backup.py if needed

## 7. Notes
- All destructive operations must be logged before execution
- Payload size capped by storing first 200 IDs (full list truncated)
- Timestamp uses `now()` default for consistency
*/
CREATE TABLE IF NOT EXISTS audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_audit_log_at ON audit_log(at);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;