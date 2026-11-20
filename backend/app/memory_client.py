"""
MemoryClient — dual-store memory abstraction for pgvector and Neo4j.

Provides a unified interface for:
- **Vector memory** (Postgres + pgvector): Stores arbitrary text with dense
  vector embeddings for semantic similarity search.
- **Graph memory** (Neo4j, optional): Stores entities, relationships, and
  episodic Engram nodes for structured knowledge retrieval. Without a driver
  (``NEO4J_ENABLED=false``, the default) the vector memory works alone, graph
  writes are skipped and reported as not recorded, and graph reads raise
  ``GraphNotRecorded``.

Embedding is delegated to ``LLMProxyClient`` so this module has no direct
dependency on any LLM SDK. DeepSeek, the default team's provider, offers no
embeddings, so the proxy embeds with a local semantic model on CPU
(multilingual-e5-small, issue #755; no key, no outside call), with the hash
vector of issue #751 standing in when the model cannot load, or with Gemini's
``text-embedding-004`` when the operator chose ``EMBEDDING_PROVIDER=gemini``;
``/health/deep``'s ``llm.embeddings`` capability says which.

Vectors from different sources do not compare, so each row records its
source in ``vector_memory.embedding_model`` (migration 0057), a search
compares only rows of the query's model, and ``reembed_stale`` writes the
other rows again once a semantic model answers (never towards the hash
vector, which would lose meaning).
"""

import json
import logging
from typing import Any

from database import engine
from sqlmodel import Session, text

logger = logging.getLogger(__name__)

NOT_RECORDED = "not_recorded"
STORED = "stored"
# The proxy's name for its hash vector: never a target for re-embedding.
HASH_MODEL = "local-fallback-embedding"
_INSERT = """
    INSERT INTO vector_memory (content, embedding, metadata, company_id, embedding_model)
    VALUES (:content, :embedding, :metadata, :company_id, :embedding_model)
"""


def _embedding(content: str) -> tuple[list, str] | None:
    """``(vector, model)`` from the proxy, or ``None`` when it gave none."""
    from llm_proxy_client import LLMProxyClient

    return LLMProxyClient().embed_with_model(content)


class GraphNotRecorded(RuntimeError):
    """Raised by a graph read or write when this client has no graph."""


class MemoryClient:
    """
    Unified client for Postgres/pgvector and Neo4j memory operations.

    Args:
        neo4j_driver: An initialised ``neo4j.Driver`` instance, or ``None``
                      when the knowledge graph is not recorded: the client
                      then works on pgvector alone.
    """

    def __init__(self, neo4j_driver=None):
        self.engine = engine
        self.neo4j_driver = neo4j_driver

    @property
    def graph_enabled(self) -> bool:
        """True when this client records and reads the knowledge graph."""
        return self.neo4j_driver is not None

    def _graph(self):
        if self.neo4j_driver is None:
            raise GraphNotRecorded("The knowledge graph is not recorded.")
        return self.neo4j_driver

    def store(
        self,
        content: str,
        metadata: dict | None = None,
        *,
        company_id: int | None = None,
    ) -> dict[str, str]:
        """
        Persist content to pgvector (semantic search) and, if kept, Neo4j.

        Workflow:
        1. Embed the content via the LLM proxy.
        2. Insert the vector into the ``vector_memory`` Postgres table.
        3. With a graph, create a ``Memory`` node in Neo4j with a ``created``
           timestamp.

        If embedding fails (proxy unavailable), the pgvector insert is skipped
        but the Neo4j node is still written.

        Args:
            content:  Raw text to store.
            metadata: Arbitrary key-value metadata persisted as JSONB / JSON.

        Returns:
            ``{"vector": ..., "graph": ...}``, each ``"stored"`` or
            ``"not_recorded"``: what was actually kept, never assumed.
        """
        outcome = {
            "vector": self.store_vector(content, metadata, company_id=company_id),
            "graph": NOT_RECORDED,
        }

        if self.neo4j_driver is None:
            return outcome
        # Write the raw content to Neo4j for graph traversal.
        with self.neo4j_driver.session() as neo_session:
            neo_session.run(
                """
                CREATE (m:Memory {
                    content: $content,
                    company_id: $company_id,
                    metadata: $metadata,
                    created: timestamp()
                })
                """,
                content=content,
                company_id=company_id,
                metadata=json.dumps(metadata or {}),
            )
        outcome["graph"] = STORED
        return outcome

    def store_vector(
        self,
        content: str,
        metadata: dict | None = None,
        *,
        company_id: int | None = None,
    ) -> str:
        """Embed *content* and insert it into pgvector with its source model.

        ``"stored"``, or ``"not_recorded"`` when the proxy gave no vector.
        """
        answer = _embedding(content)
        if not answer:
            return NOT_RECORDED
        embedding, model = answer
        with Session(self.engine) as session:
            session.execute(
                text(_INSERT),
                {
                    "content": content,
                    "company_id": company_id,
                    "embedding": json.dumps(embedding),
                    "metadata": json.dumps(metadata or {}),
                    "embedding_model": model or None,
                },
            )
            session.commit()
        return STORED

    def reembed_stale(self, limit: int = 5000, batch: int = 100) -> int:
        """Write again, with the proxy's current model, rows another source embedded.

        Runs only when that model is a semantic one (not the hash vector), and
        stops at the first text the proxy does not answer with that model (it
        went away, or fell back). Returns how many rows were re-embedded.
        """
        probe = _embedding("memory")
        if not probe or not probe[1] or probe[1] == HASH_MODEL:
            return 0
        model = probe[1]
        done = 0
        while done < limit:
            with Session(self.engine) as session:
                rows = session.execute(
                    text(
                        "SELECT id, content FROM vector_memory "
                        "WHERE embedding_model IS DISTINCT FROM :model ORDER BY id LIMIT :batch"
                    ),
                    {"model": model, "batch": min(batch, limit - done)},
                ).all()
                if not rows:
                    break
                for row_id, content in rows:
                    answer = _embedding(content)
                    if not answer or answer[1] != model:
                        session.commit()
                        return done
                    session.execute(
                        text(
                            "UPDATE vector_memory SET embedding = CAST(:embedding AS vector), "
                            "embedding_model = :model WHERE id = :id"
                        ),
                        {
                            "embedding": json.dumps(answer[0]),
                            "model": model,
                            "id": row_id,
                        },
                    )
                    done += 1
                session.commit()
        return done

    def search_similar(
        self,
        query: str,
        limit: int = 10,
        *,
        company_ids: list[int] | None = None,
        include_legacy: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Find semantically similar content using pgvector ANN search.

        Embeds *query*, then ranks stored vectors by cosine distance
        (``<=>`` operator).  Returns results ordered by descending similarity.

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return.

        Returns:
            List of dicts with keys ``content``, ``metadata``, ``similarity``
            (float in [0, 1] where 1 = identical).
        """
        answer = _embedding(query)
        if not answer:
            return []
        embedding, model = answer

        conditions = []
        params = {"embedding": json.dumps(embedding), "limit": limit}
        if model:
            # Only vectors from the query's source compare with it.
            conditions.append("embedding_model = :embedding_model")
            params["embedding_model"] = model
        if company_ids is not None:
            scope = "company_id = ANY(:company_ids)"
            if include_legacy:
                scope += " OR company_id IS NULL"
            conditions.append(f"({scope})")
            params["company_ids"] = company_ids
        company_filter = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with Session(self.engine) as session:
            # Cast the parameter to vector so pgvector accepts it.
            results_proxy = session.execute(
                text(
                    f"""
                    SELECT content, metadata, 1 - (embedding <=> CAST(:embedding AS vector)) as similarity, company_id
                    FROM vector_memory
                    {company_filter}
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                params,
            )

            results = []
            for row in results_proxy:
                results.append(
                    {
                        "content": row[0],
                        "metadata": row[1]
                        if isinstance(row[1], dict)
                        else json.loads(row[1] or "{}"),
                        "similarity": row[2],
                        "company_id": row[3],
                        "legacy": row[3] is None,
                    }
                )
            return results

    def store_entity(self, entity_type: str, properties: dict[str, Any]):
        """
        Create a labelled node in Neo4j.

        Args:
            entity_type: Cypher label for the new node (e.g. ``"User"``, ``"Sprint"``).
            properties:  Dict of node properties.
        """
        with self._graph().session() as neo_session:
            neo_session.run(f"CREATE (e:{entity_type} $props)", props=properties)

    def query_graph(self, cypher: str, **kwargs) -> list[dict[str, Any]]:
        """
        Execute an arbitrary read Cypher query against Neo4j.

        Args:
            cypher:   Cypher query string.  Use ``$param`` placeholders.
            **kwargs: Named parameters forwarded to the query.

        Returns:
            List of record dicts.
        """
        with self._graph().session() as neo_session:
            result = neo_session.run(cypher, **kwargs)
            return [dict(record) for record in result]

    def get_relationships(
        self,
        node_type: str,
        node_id: str,
        rel_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve outgoing relationships for a node identified by ``id``.

        Args:
            node_type: Cypher label of the source node.
            node_id:   Value of the ``id`` property on the source node.
            rel_type:  Optional relationship type filter (e.g. ``"MODIFIED_BY"``).
                       If ``None``, all relationship types are returned.

        Returns:
            List of record dicts containing ``n`` (source), ``r`` (rel), ``m`` (target).
        """
        if rel_type:
            cypher = (
                f"MATCH (n:{node_type} {{id: $id}})-[r:{rel_type}]->(m) RETURN n, r, m"
            )
        else:
            cypher = f"MATCH (n:{node_type} {{id: $id}})-[r]->(m) RETURN n, r, m"

        with self._graph().session() as neo_session:
            result = neo_session.run(cypher, id=node_id)
            return [dict(record) for record in result]
