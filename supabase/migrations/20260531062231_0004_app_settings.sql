/*
# App Settings Table (Deliverable 7 + 13)

## 1. Purpose
Store operator preferences like timezone and retention defaults. Single-row configuration table used by the /settings endpoint and retention purge logic.

## 2. New Table

### app_settings
Key-value configuration store:
- `key` (text, primary key) - setting name (e.g., "timezone", "retention_days")
- `value` (jsonb) - setting value (flexible structure)
- `updated_at` (timestamptz) - last modification time

## 3. Default Values
In code, defaults are:
- timezone: "UTC"
- retention_days: 365 (from RETENTION_DEFAULT_DAYS env var)

## 4. Security
- RLS enabled
- Only authenticated admin can read/write

## 5. Notes
- Table starts empty; defaults from config.py apply until operator saves preferences
- Changing timezone affects calendar view scheduling
- Retention days controls purge threshold for published posts
- Used by both Deliverable 7 (calendar timezone) and Deliverable 13 (retention purge)
*/

CREATE TABLE IF NOT EXISTS app_settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;