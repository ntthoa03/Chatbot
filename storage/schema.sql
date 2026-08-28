-- H3-04: schema SQLite TẠM để demo; giữ tên bảng/cột cho migration Postgres.
-- TODO(Hieu/Postgres): chuyển type/default/index sang migration production.

PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version >= 1),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (conversation_id, tenant_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants (tenant_id)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    trace_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (conversation_id, tenant_id)
        REFERENCES conversations (conversation_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS leads (
    lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    name TEXT,
    phone TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (conversation_id, tenant_id)
        REFERENCES conversations (conversation_id, tenant_id),
    CHECK (name IS NOT NULL OR phone IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS usage_events (
    usage_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out INTEGER NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    cached_tokens_in INTEGER NOT NULL DEFAULT 0 CHECK (cached_tokens_in >= 0),
    cache_write_tokens_in INTEGER NOT NULL DEFAULT 0 CHECK (cache_write_tokens_in >= 0),
    cost_usd REAL NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (conversation_id, tenant_id)
        REFERENCES conversations (conversation_id, tenant_id)
);

-- Chỉ thêm index phục vụ ba truy vấn demo bắt buộc; không tối ưu sâu bản tạm.
CREATE INDEX IF NOT EXISTS idx_messages_tenant_conversation
    ON messages (tenant_id, conversation_id, message_id);
CREATE INDEX IF NOT EXISTS idx_leads_tenant_conversation
    ON leads (tenant_id, conversation_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_usage_tenant_conversation
    ON usage_events (tenant_id, conversation_id, usage_event_id);
