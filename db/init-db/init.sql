-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Base Tables
CREATE TABLE IF NOT EXISTS companies (
    id SERIAL PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    default_sprint_length_days INTEGER DEFAULT 14,
    autonomy_level INTEGER DEFAULT 2,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_system_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Enterprise & Governance
CREATE TABLE IF NOT EXISTS business_units (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    unit_name VARCHAR(100) NOT NULL,
    token_quota_limit INTEGER DEFAULT 1000000,
    token_quota_used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    business_unit_id INTEGER REFERENCES business_units(id),
    name VARCHAR(255) DEFAULT '',
    role VARCHAR(50) NOT NULL,
    persona TEXT,
    seniority VARCHAR(20) DEFAULT 'Senior',
    specialty VARCHAR(100),
    model VARCHAR(255) DEFAULT '',
    system_prompt TEXT,
    token_budget INTEGER DEFAULT 500000,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_credentials (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    credential_type VARCHAR(50) NOT NULL,
    access_level VARCHAR(20) NOT NULL,
    encrypted_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_members (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    human_role VARCHAR(50) NOT NULL, 
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, user_id)
);

-- 3. Sprints & Tasks
CREATE TABLE IF NOT EXISTS sprints (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    sprint_name VARCHAR(255) NOT NULL,
    start_date DATE,
    duration_days INTEGER DEFAULT 14,
    status VARCHAR(50) DEFAULT 'active',
    goal TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backlog_items (
    id SERIAL PRIMARY KEY,
    sprint_id INTEGER REFERENCES sprints(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'todo',
    priority VARCHAR(20) DEFAULT 'medium',
    assignee VARCHAR(100),
    comments JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_backlog_sprint ON backlog_items(sprint_id);
CREATE INDEX IF NOT EXISTS idx_backlog_status ON backlog_items(status);

-- 4. LangGraph & Orchestration
CREATE TABLE IF NOT EXISTS task_queue (
    id SERIAL PRIMARY KEY,
    interaction_id VARCHAR(100) NOT NULL,
    task_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    requires_approval BOOLEAN DEFAULT FALSE,
    approval_status VARCHAR(50) DEFAULT 'not_required',
    approved_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outbox_events (
    id SERIAL PRIMARY KEY,
    aggregate_type VARCHAR(100) NOT NULL,
    aggregate_id VARCHAR(100) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
);

-- 5. Memory & Discovery
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    interaction_id VARCHAR(100),
    agent_id VARCHAR(100),
    action VARCHAR(100) NOT NULL,
    details JSONB,
    result JSONB,
    decision_signature VARCHAR(500),
    cost_tokens INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vector_memory (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS vector_memory_idx ON vector_memory USING ivfflat (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS project_discovery (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    project_path TEXT NOT NULL,
    detected_stack JSONB,
    suggested_team JSONB,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. State & Analytics
CREATE TABLE IF NOT EXISTS performance_metrics (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    sprint_id INTEGER REFERENCES sprints(id) ON DELETE CASCADE,
    velocity_score INTEGER,
    total_tokens_consumed INTEGER,
    total_cost_estimate DECIMAL(10, 4),
    bottlenecks TEXT,
    ai_summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS factory_state (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    state_key VARCHAR(100) NOT NULL,
    state_value JSONB,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. Bug Tracking
CREATE TABLE IF NOT EXISTS bug_reports (
    id           SERIAL PRIMARY KEY,
    ticket_num   VARCHAR(20) UNIQUE NOT NULL,
    title        VARCHAR(255) NOT NULL,
    description  TEXT NOT NULL,
    steps        TEXT,
    expected     TEXT,
    actual       TEXT,
    severity     VARCHAR(20) NOT NULL DEFAULT 'medium',
    category     VARCHAR(50)  NOT NULL DEFAULT 'bug',
    status       VARCHAR(30)  NOT NULL DEFAULT 'open',
    reporter_email VARCHAR(255),
    assigned_to  VARCHAR(100),
    resolution   TEXT,
    browser_info JSONB,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at  TIMESTAMP
);

-- Indexes & Triggers
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events(status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_taskqueue_pending ON task_queue(status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_bug_reports_status ON bug_reports(status);

-- Auto-update updated_at for all tables
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_companies_updated_at BEFORE UPDATE ON companies FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_sprints_updated_at BEFORE UPDATE ON sprints FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_backlog_items_updated_at BEFORE UPDATE ON backlog_items FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_task_queue_updated_at BEFORE UPDATE ON task_queue FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER trg_bug_reports_updated_at BEFORE UPDATE ON bug_reports FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
