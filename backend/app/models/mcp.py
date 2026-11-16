"""MCP servers a company's agents may call tools on.

One row per server and company, in the shape of the ``mcpServers`` map every
MCP client reads (``command``/``args`` for stdio, ``url`` for HTTP or SSE).
The secrets a server needs (environment variables, HTTP headers) are stored
apart, encrypted with the Fernet key (``crypto.py``), and never leave the
backend through the API: it lists their names only.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field, SQLModel

MCP_TRANSPORTS = ("stdio", "http", "sse")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class McpServer(SQLModel, table=True):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    name: str
    transport: str  # one of MCP_TRANSPORTS
    url: str | None = None
    command: str | None = None
    args: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    cwd: str | None = None
    # Encrypted JSON ``{"env": {...}, "headers": {...}}``; ``None`` when the
    # server needs no secret.
    secrets: str | None = None
    # Agent roles allowed to call its tools; empty means every role that
    # executes tasks.
    roles: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    enabled: bool = True
    description: str | None = None
    timeout_seconds: float | None = None
    created_by: str | None = None
    created_at: datetime | None = Field(default_factory=_utcnow)
    updated_at: datetime | None = Field(default_factory=_utcnow)
