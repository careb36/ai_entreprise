"""Whether the knowledge graph (Neo4j) is recorded, and how its absence reads.

The graph is optional and on by default (``NEO4J_ENABLED``, issue #328).
Off, nothing is written to it and every reader that needs it answers
"not recorded" with the reason, never an error and never a silent empty
list: an empty answer would read as "no decisions were made", which the
graph cannot say when it was never kept. Memory search and store run on
Postgres (pgvector) alone. Memory is supporting material for prompts, never
evidence (docs/POSITIONING.md).
"""

from __future__ import annotations

from typing import Any

from config import settings
from database import get_neo4j_driver
from fastapi import HTTPException
from logging_config import logger

NOT_RECORDED = "not_recorded"
RECORDED = "recorded"

DISABLED_REASON = (
    "The knowledge graph is not recorded on this deployment (NEO4J_ENABLED=false)."
)

MEMORY_UNAVAILABLE = (
    "Memory search is unavailable: it needs Postgres with pgvector's "
    "vector_memory table (db/init-db/init.sql). The knowledge graph is not needed."
)


def graph_enabled() -> bool:
    """True when this deployment records the knowledge graph."""
    return bool(settings.NEO4J_ENABLED)


def not_recorded(reason: str = DISABLED_REASON, **extra: Any) -> dict[str, Any]:
    """The answer a graph reader gives when the graph is not kept."""
    return {"status": NOT_RECORDED, "recorded": False, "reason": reason, **extra}


def lineage(
    file_path: str,
    company_id: int | None,
    company_ids: list[int],
    *,
    scoped: bool,
) -> dict[str, Any]:
    """The decisions the graph recorded on a file, newest first (up to 10).

    ``scoped`` limits the answer to ``company_ids`` (and rows without a
    company) when no single ``company_id`` is asked for. Without the graph the
    answer says so; a graph that is kept but fails to answer is a 500.
    """
    if not graph_enabled():
        return not_recorded(file=file_path, lineage=[])
    company_filter = (
        "AND e.company_id = $company_id"
        if company_id is not None
        else "AND (e.company_id IN $company_ids OR e.company_id IS NULL)"
        if scoped
        else ""
    )
    cypher = f"""
    MATCH (e:Episodic)-[r:MODIFIED_BY]->(s)
    WHERE s.id ENDS WITH $file_path
    {company_filter}
    RETURN e.id AS engram_id, e.agent_role AS role, e.summary AS decision, e.created_at AS timestamp,
           e.company_id AS company_id, e.company_id IS NULL AS legacy
    ORDER BY e.created_at DESC LIMIT 10
    """
    try:
        driver = get_neo4j_driver()
        with driver.session() as neo_session:
            result = neo_session.run(
                cypher,
                file_path=file_path,
                company_id=company_id,
                company_ids=company_ids,
            )
            entries = [dict(record) for record in result]
    except Exception as e:  # noqa: BLE001 - Translate graph failures to a sanitized API error.
        logger.error(f"Lineage query failed: {e}")
        raise HTTPException(
            status_code=500, detail="Neo4j Time Travel query failed."
        ) from None
    if not entries:
        return {
            "file": file_path,
            "status": "No historical engrams found.",
            "recorded": True,
            "lineage": [],
        }
    return {
        "file": file_path,
        "status": "success",
        "recorded": True,
        "lineage": entries,
    }
