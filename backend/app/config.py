import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "factory"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "factory"
    
    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""
    
    # NATS
    NATS_URL: str = "nats://localhost:4222"
    
    # OpenProject
    OPENPROJECT_URL: str = "http://localhost:8080"
    OPENPROJECT_TOKEN_PO: Optional[str] = None
    OPENPROJECT_TOKEN_DEV: Optional[str] = None
    
    # LLM Proxy
    LLM_PROXY_URL: str = "http://localhost:5100"
    
    # Audit
    AUDIT_LOG_PATH: str = "/app/.factory/logs"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
