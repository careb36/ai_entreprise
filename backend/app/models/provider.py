"""Model providers a company has connected: where its agents' models come from.

One row per connection: a provider (a models.dev id such as ``deepseek`` or
``openrouter``, or ``openai-compatible`` for any endpoint that speaks the
OpenAI chat API, such as Ollama, LM Studio or vLLM), the base URL to call,
the API key and any extra headers. The key and the headers are stored
encrypted with the Fernet key (``crypto.py``) and never leave the backend
through the API, which says only whether a key is set.

The last validation (a ``GET {base_url}/models`` with the key) is kept as an
observation of the provider, with when it ran and what it answered.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Column, Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ProviderConnection(SQLModel, table=True):
    __tablename__ = "provider_connections"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    provider: str
    name: str
    base_url: str
    # Encrypted (``crypto.encrypt_value``); ``None`` for a provider that needs
    # no key, such as a local Ollama.
    api_key: str | None = None
    # Encrypted JSON object of extra request headers; ``None`` when none.
    headers: str | None = None
    enabled: bool = True
    validated_at: datetime | None = None
    validation_ok: bool | None = None
    validation_detail: str | None = None
    # The model ids the provider listed at its last successful validation.
    listed_models: list[Any] | None = Field(default=None, sa_column=Column(JSON))
    created_by: str | None = None
    created_at: datetime | None = Field(default_factory=_utcnow)
    updated_at: datetime | None = Field(default_factory=_utcnow)
