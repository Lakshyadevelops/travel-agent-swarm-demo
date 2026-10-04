-- Schema for the ADK session layer + inter-agent scratchpad.
-- JSONB throughout: this is how a developer would actually model agent state in
-- Postgres, and the JSONB cost is part of what we are pricing.
--
-- Idempotent on purpose. The compose file feeds it to the container's first
-- boot, and the app replays it at every startup (registry.ensure_schema) so a
-- managed Cloud SQL instance, which has no init hook, gets the same tables.

CREATE TABLE IF NOT EXISTS adk_sessions (
  app_name         text NOT NULL,
  user_id          text NOT NULL,
  session_id       text NOT NULL,
  state            jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_update_time double precision NOT NULL,
  PRIMARY KEY (app_name, user_id, session_id)
);

CREATE TABLE IF NOT EXISTS adk_events (
  app_name   text NOT NULL,
  user_id    text NOT NULL,
  session_id text NOT NULL,
  event_id   text NOT NULL,
  ts         double precision NOT NULL,
  author     text,
  payload    jsonb NOT NULL,  -- full Event.model_dump(mode='json')
  PRIMARY KEY (app_name, user_id, session_id, event_id)
);

-- Supports both GetSessionConfig filters (num_recent_events via LIMIT on a
-- DESC scan, after_timestamp via a range predicate) without a sort.
CREATE INDEX IF NOT EXISTS adk_events_by_ts
  ON adk_events (app_name, user_id, session_id, ts DESC);

-- Inter-agent blackboard. Mirrors a Valkey HASH keyed by run_id, with TTL
-- emulated via expires_at so the two backends have matching semantics.
CREATE TABLE IF NOT EXISTS scratchpad (
  run_id     text NOT NULL,
  field      text NOT NULL,
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  PRIMARY KEY (run_id, field)
);

CREATE INDEX IF NOT EXISTS scratchpad_expiry
  ON scratchpad (expires_at) WHERE expires_at IS NOT NULL;
