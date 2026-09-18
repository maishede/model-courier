PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS owners (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id),
    token_hash TEXT NOT NULL UNIQUE,
    revoked_at REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id),
    token_hash TEXT NOT NULL UNIQUE,
    capabilities_json TEXT NOT NULL,
    revoked_at REAL,
    last_seen_at REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    task_type TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    request_json TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    requested_provider TEXT,
    requested_model TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'uploading', 'queued', 'leased', 'running', 'succeeded',
        'failed', 'expired', 'canceled'
    )),
    expires_at REAL NOT NULL,
    execution_deadline REAL NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    lease_owner TEXT,
    lease_generation INTEGER NOT NULL DEFAULT 0,
    lease_token_hash TEXT,
    lease_until REAL,
    bound_capability_id TEXT,
    error_json TEXT,
    result_json TEXT,
    result_available INTEGER NOT NULL DEFAULT 0,
    result_expires_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (owner_id, device_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('input', 'result')),
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    published INTEGER NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_claim
    ON tasks (status, next_attempt_at, expires_at, updated_at);
CREATE INDEX IF NOT EXISTS idx_tasks_lease
    ON tasks (status, lease_until);
CREATE INDEX IF NOT EXISTS idx_artifacts_expiry
    ON artifacts (expires_at, published);
