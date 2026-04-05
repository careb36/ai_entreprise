-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Companies table
CREATE TABLE IF NOT EXISTS companies (
    id SERIAL PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    default_sprint_length_days INTEGER DEFAULT 14,
    autonomy_level INTEGER DEFAULT 2, -- 1: Copilot (Manual), 2: Exception Manager (Semi-Auto), 3: Full Autonomous
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agents table (linked to companies)
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,
    persona TEXT,
    seniority VARCHAR(20) DEFAULT 'Senior', -- 'Junior', 'Mid', 'Senior', 'Staff'
    specialty VARCHAR(100), -- 'Frontend', 'Backend', 'DevOps', etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Project Infrastructure Permissions (Vault Simulation)
CREATE TABLE IF NOT EXISTS project_credentials (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    credential_type VARCHAR(50) NOT NULL, -- 'git_token', 'cloud_key', 'db_password'
    access_level VARCHAR(20) NOT NULL, -- 'read_only', 'write', 'admin'
    encrypted_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Human Users (for B2B SaaS Collaboration)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_system_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Company/Project Memberships (Human RBAC with Engineering Roles)
CREATE TABLE IF NOT EXISTS company_members (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    human_role VARCHAR(50) NOT NULL, 
    -- 'Owner': Business/Billing
    -- 'AI_Orchestrator': PM/Tech Lead managing AI objectives
    -- 'Engram_Validator': Senior Dev/Architect reviewing AI output
    -- 'Implementation_Engineer': Dev working alongside AI
    -- 'Viewer': Stakeholder
    -- 'SecOps_Auditor': Security professional reviewing AI SAST reports
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, user_id)
);

-- Update Business Units (Departments) for Multi-Tenancy
CREATE TABLE IF NOT EXISTS business_units (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    unit_name VARCHAR(100) NOT NULL,
    token_quota_limit INTEGER DEFAULT 1000000,
    token_quota_used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Update agents to belong to a Business Unit
ALTER TABLE agents ADD COLUMN IF NOT EXISTS business_unit_id INTEGER REFERENCES business_units(id);

-- Task queue with Human-in-the-loop Governance
ALTER TABLE task_queue ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN DEFAULT FALSE;
ALTER TABLE task_queue ADD COLUMN IF NOT EXISTS approval_status VARCHAR(50) DEFAULT 'not_required'; -- 'pending', 'approved', 'rejected'
ALTER TABLE task_queue ADD COLUMN IF NOT EXISTS approved_by VARCHAR(100);

-- Sprints table
CREATE TABLE IF NOT EXISTS sprints (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    sprint_name VARCHAR(255) NOT NULL,
    start_date DATE,
    duration_days INTEGER DEFAULT 14,
    status VARCHAR(50) DEFAULT 'active',   -- active | completed | cancelled
    goal TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Backlog items table
CREATE TABLE IF NOT EXISTS backlog_items (
    id SERIAL PRIMARY KEY,
    sprint_id INTEGER REFERENCES sprints(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'todo',  -- todo | in_progress | in_review | done | blocked
    priority VARCHAR(20) DEFAULT 'medium',  -- low | medium | high | critical
    assignee VARCHAR(100),              -- agent role or human username
    comments JSONB DEFAULT '[]',        -- [{author, text, created_at}]
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_backlog_sprint ON backlog_items(sprint_id);
CREATE INDEX IF NOT EXISTS idx_backlog_status ON backlog_items(status);

-- Auto-update updated_at for backlog_items
CREATE OR REPLACE FUNCTION update_backlog_items_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_backlog_items_updated_at ON backlog_items;
CREATE TRIGGER trg_backlog_items_updated_at
    BEFORE UPDATE ON backlog_items
    FOR EACH ROW EXECUTE FUNCTION update_backlog_items_updated_at();

-- Audit log table
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

-- Vector memory table for pgvector
CREATE TABLE IF NOT EXISTS vector_memory (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create index for vector similarity search
CREATE INDEX IF NOT EXISTS vector_memory_idx ON vector_memory USING ivfflat (embedding vector_cosine_ops);

-- Performance and Analytics metrics
CREATE TABLE IF NOT EXISTS performance_metrics (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    sprint_id INTEGER REFERENCES sprints(id) ON DELETE CASCADE,
    velocity_score INTEGER, -- Tasks per day equivalent
    total_tokens_consumed INTEGER,
    total_cost_estimate DECIMAL(10, 4),
    bottlenecks TEXT,
    ai_summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Factory state table
CREATE TABLE IF NOT EXISTS factory_state (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    state_key VARCHAR(100) NOT NULL,
    state_value JSONB,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Task queue for LangGraph
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

-- Outbox events for distributed transactions
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

-- Optimize Janitor queries with partial indexes
CREATE INDEX IF NOT EXISTS idx_outbox_completed ON outbox_events(processed_at) WHERE status = 'completed';
CREATE INDEX IF NOT EXISTS idx_taskqueue_completed ON task_queue(completed_at) WHERE status IN ('completed', 'failed');

-- Bug reports / support tickets (GitHub Issues / Linear style)
CREATE TABLE IF NOT EXISTS bug_reports (
    id           SERIAL PRIMARY KEY,
    ticket_num   VARCHAR(20) UNIQUE NOT NULL,  -- e.g. FAB-00042
    title        VARCHAR(255) NOT NULL,
    description  TEXT NOT NULL,
    steps        TEXT,                         -- steps to reproduce
    expected     TEXT,                         -- expected behaviour
    actual       TEXT,                         -- actual behaviour
    severity     VARCHAR(20) NOT NULL DEFAULT 'medium',   -- low | medium | high | critical
    category     VARCHAR(50)  NOT NULL DEFAULT 'bug',     -- bug | feature | question | incident
    status       VARCHAR(30)  NOT NULL DEFAULT 'open',    -- open | in_progress | waiting | resolved | closed
    reporter_email VARCHAR(255),
    assigned_to  VARCHAR(100),
    resolution   TEXT,
    browser_info JSONB,                        -- user-agent, url, viewport…
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at  TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bug_reports_status   ON bug_reports(status);
CREATE INDEX IF NOT EXISTS idx_bug_reports_severity ON bug_reports(severity);
CREATE INDEX IF NOT EXISTS idx_bug_reports_reporter ON bug_reports(reporter_email);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_bug_reports_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_bug_reports_updated_at ON bug_reports;
CREATE TRIGGER trg_bug_reports_updated_at
    BEFORE UPDATE ON bug_reports
    FOR EACH ROW EXECUTE FUNCTION update_bug_reports_updated_at();
