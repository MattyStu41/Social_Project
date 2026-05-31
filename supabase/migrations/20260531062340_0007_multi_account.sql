/*
# Multi-Account Per Platform Support (Deliverable 16)

## 1. Purpose
Allow operators to connect multiple accounts per platform (e.g., personal IG + brand IG + client IG) and select which account to use when scheduling a post.

## 2. Changes

### platform_tokens table
- REMOVES unique constraint on `platform` column
- Allows multiple rows with same platform but different user_ids
- Backward compatible: old code continues working with single accounts

### scheduled_posts table
Added column:
- `platform_accounts` (jsonb, NOT NULL, DEFAULT '{}')

Structure:
```json
{
  "threads": "uuid-of-pinned-threads-account",
  "instagram": "uuid-of-pinned-ig-account"
}
```

## 3. Behavior

### Default (Empty platform_accounts)
When `platform_accounts = {}`, scheduler uses the **first-created** account for each platform.

This is backward-compatible with posts created before D16.

### Explicit Selection
When operator pins a specific account:
1. UI dropdown shows all connected accounts for platform
2. Selected account UUID saved in platform_accounts
3. Scheduler uses that specific account at publish time

### Account Deletion Handling
If pinned account is deleted:
- Scheduler logs a warning
- Falls back to default (first) account
- Publish continues without blocking

## 4. Security
- No security changes - tables already have RLS
- Platform-scoped but account-specific tokens remain protected

## 5. Notes
- Circuit breaker remains **per-platform** (not per-account)
  Reason: When Meta API is down, all Meta accounts are affected
- Token expiry alerts are **per-account** (each account shows separate warning)
- OAuth deduplication uses (platform, user_id) to prevent duplicate connects
- Disconnect endpoint requires `?account_id=` when multiple accounts exist
*/
-- Add platform_accounts column to scheduled_posts
ALTER TABLE scheduled_posts 
ADD COLUMN IF NOT EXISTS platform_accounts jsonb NOT NULL DEFAULT '{}'::jsonb;