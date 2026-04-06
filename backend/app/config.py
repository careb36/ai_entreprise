"""
Application settings for Fabrique Enterprise Backend.

All values can be overridden by environment variables or a local ``.env`` file.
pydantic-settings automatically reads env vars that match the field names
(case-sensitive by default, see the inner ``Config`` class).

Usage::

    from config import settings
    db_url = settings.POSTGRES_HOST
"""

import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ---------------------------------------------------------------------------
    # PostgreSQL — primary relational store (companies, sprints, backlog, audit)
    # ---------------------------------------------------------------------------
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "factory"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "factory"

    # ---------------------------------------------------------------------------
    # Neo4j — Temporal Knowledge Graph for episodic memory (Engrams)
    # ---------------------------------------------------------------------------
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""

    # ---------------------------------------------------------------------------
    # NATS — asynchronous event bus for agent orchestration and Glassroom SSE
    # ---------------------------------------------------------------------------
    NATS_URL: str = "nats://localhost:4222"

    # ---------------------------------------------------------------------------
    # OpenProject — external project-management tool used by PO / Dev agents
    # In production, tokens should be retrieved from Vault/KMS instead.
    # ---------------------------------------------------------------------------
    OPENPROJECT_URL: str = "http://localhost:8080"
    OPENPROJECT_TOKEN_PO: Optional[str] = None   # Product Owner API token
    OPENPROJECT_TOKEN_DEV: Optional[str] = None  # Developer API token

    # ---------------------------------------------------------------------------
    # LLM Proxy — internal gateway that routes requests to Gemini / DeepSeek
    # ---------------------------------------------------------------------------
    LLM_PROXY_URL: str = "http://localhost:5100"

    # ---------------------------------------------------------------------------
    # Audit — filesystem path for append-only JSONL audit logs
    # ---------------------------------------------------------------------------
    AUDIT_LOG_PATH: str = "/app/.factory/logs"

    class Config:
        env_file = ".env"
        case_sensitive = True


# Module-level singleton; import this object rather than instantiating Settings directly.
settings = Settings()
