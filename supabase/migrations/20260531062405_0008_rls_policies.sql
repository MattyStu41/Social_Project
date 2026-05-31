/*
# Row Level Security Policies

## 1. Purpose
Secure all tables with restrictive RLS policies. In a single-operator setup, authenticated users can access all data.

## 2. Security Model
- All tables start in locked-down state (RLS enabled, no policies)
- Policies grant access to authenticated users only
- Unauthenticated requests are rejected
- Service role (backend) bypasses RLS for internal operations

## 3. Policy Strategy
Since JACK is a single-operator tool:
- All tables use `TO authenticated` for data access
- No tenant isolation needed (one operator only)
- Platform tokens remain protected from unauthenticated access
- OAuth flows verify state before writing tokens

## 4. Tables Covered
1. scheduled_posts - full CRUD for authenticated users
2. platform_tokens - full CRUD for authenticated users
3. drafts - full CRUD for authenticated users
4. substack_seen - full CRUD for authenticated users
5. app_settings - full CRUD for authenticated users
6. post_analytics - read/write for authenticated users
7. audit_log - read-only for authenticated users (append-only design)

## 5. Notes
- Using `auth.uid()` to check authentication status
- Policies separated by operation (SELECT, INSERT, UPDATE, DELETE) for clarity
- audit_log disallows DELETE and UPDATE to preserve audit trail
*/

-- scheduled_posts policies
CREATE POLICY "Authenticated users can read scheduled posts"
  ON scheduled_posts
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert scheduled posts"
  ON scheduled_posts
  FOR INSERT
  TO authenticated
  WITH CHECK (true);

CREATE POLICY "Authenticated users can update scheduled posts"
  ON scheduled_posts
  FOR UPDATE
  TO authenticated
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Authenticated users can delete scheduled posts"
  ON scheduled_posts
  FOR DELETE
  TO authenticated
  USING (true);

-- platform_tokens policies
CREATE POLICY "Authenticated users can read platform tokens"
  ON platform_tokens
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert platform tokens"
  ON platform_tokens
  FOR INSERT
  TO authenticated
  WITH CHECK (true);

CREATE POLICY "Authenticated users can update platform tokens"
  ON platform_tokens
  FOR UPDATE
  TO authenticated
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Authenticated users can delete platform tokens"
  ON platform_tokens
  FOR DELETE
  TO authenticated
  USING (true);

-- drafts policies
CREATE POLICY "Authenticated users can read drafts"
  ON drafts
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert drafts"
  ON drafts
  FOR INSERT
  TO authenticated
  WITH CHECK (true);

CREATE POLICY "Authenticated users can update drafts"
  ON drafts
  FOR UPDATE
  TO authenticated
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Authenticated users can delete drafts"
  ON drafts
  FOR DELETE
  TO authenticated
  USING (true);

-- substack_seen policies
CREATE POLICY "Authenticated users can read substack_seen"
  ON substack_seen
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert substack_seen"
  ON substack_seen
  FOR INSERT
  TO authenticated
  WITH CHECK (true);

CREATE POLICY "Authenticated users can delete substack_seen"
  ON substack_seen
  FOR DELETE
  TO authenticated
  USING (true);

-- app_settings policies
CREATE POLICY "Authenticated users can read app settings"
  ON app_settings
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert app settings"
  ON app_settings
  FOR INSERT
  TO authenticated
  WITH CHECK (true);

CREATE POLICY "Authenticated users can update app settings"
  ON app_settings
  FOR UPDATE
  TO authenticated
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Authenticated users can delete app settings"
  ON app_settings
  FOR DELETE
  TO authenticated
  USING (true);

-- post_analytics policies
CREATE POLICY "Authenticated users can read post analytics"
  ON post_analytics
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert post analytics"
  ON post_analytics
  FOR INSERT
  TO authenticated
  WITH CHECK (true);

CREATE POLICY "Authenticated users can update post analytics"
  ON post_analytics
  FOR UPDATE
  TO authenticated
  USING (true)
  WITH CHECK (true);

-- audit_log policies (read-only for authenticated, insert allowed for logging)
CREATE POLICY "Authenticated users can read audit log"
  ON audit_log
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Authenticated users can insert audit log entries"
  ON audit_log
  FOR INSERT
  TO authenticated
  WITH CHECK (true);