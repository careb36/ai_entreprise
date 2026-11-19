"""The stored code graph in Neo4j (issue #438, second slice).

The same interface as ``code_graph_store.SqlStore``, chosen by
``code_graph_store.store_for`` when ``CODE_GRAPH_STORE`` is ``neo4j`` and the
knowledge graph is recorded (``NEO4J_ENABLED``, ``platform.memory_graph``).
A file is ``(:CodeFile {company_id, root, path, sha256, status, truncated,
observed_at})``; it ``-[:DEFINES {line, depth}]->`` and ``-[:REFERENCES
{count}]->`` ``(:CodeSymbol {company_id, root, name})``. A reported file
drops its own ``DEFINES`` and ``REFERENCES`` and gets the new ones; a
removed file is detached and deleted. The edges from records are
``(:CodeRef {company_id, root, ref})-[:CODE_EDGE {kind, source, source_ref,
detail, recorded_at}]->(:CodeRef)``, ``detail`` as JSON text. Every
statement is scoped to the company and the folder.

The edge rule and the walk are ``code_graph_store``'s, on what this store
returns, so both stores give the same related code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from database import get_neo4j_driver


def _driver() -> Any:
    return get_neo4j_driver()


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


_FILE = "(f:CodeFile {company_id: $company_id, root: $root})"


class Neo4jStore:
    """The code graph in the knowledge graph."""

    def _run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        driver = _driver()
        try:
            with driver.session() as session:
                return [dict(row) for row in session.run(cypher, **params)]
        finally:
            driver.close()

    def known(self, company_id: int, root: str) -> dict[str, str]:
        rows = self._run(
            f"MATCH {_FILE} RETURN f.path AS path, f.sha256 AS sha256",
            company_id=company_id,
            root=root,
        )
        return {r["path"]: r["sha256"] for r in rows}

    def observed_at(self, company_id: int, root: str) -> datetime | None:
        rows = self._run(
            f"MATCH {_FILE} RETURN max(f.observed_at) AS observed_at",
            company_id=company_id,
            root=root,
        )
        value = rows[0].get("observed_at") if rows else None
        try:
            return datetime.fromisoformat(value) if value else None
        except (TypeError, ValueError):
            return None

    def apply(
        self,
        company_id: int,
        root: str,
        files: list[dict[str, Any]],
        removed: list[str],
    ) -> tuple[int, int]:
        gone = 0
        if removed:
            rows = self._run(
                f"MATCH {_FILE} WHERE f.path IN $paths DETACH DELETE f RETURN count(*) AS n",
                company_id=company_id,
                root=root,
                paths=list(removed),
            )
            gone = int(rows[0].get("n") or 0) if rows else 0
        now = _now()
        for f in files:
            self._run(
                "MERGE (f:CodeFile {company_id: $company_id, root: $root, path: $path}) "
                "SET f.sha256 = $sha256, f.status = $status, f.truncated = $truncated, "
                "f.observed_at = $observed_at "
                "WITH f OPTIONAL MATCH (f)-[r:DEFINES|REFERENCES]->() DELETE r "
                "WITH DISTINCT f "
                "FOREACH (d IN $defs | MERGE (s:CodeSymbol {company_id: $company_id, "
                "root: $root, name: d.name}) "
                "CREATE (f)-[:DEFINES {line: d.line, depth: d.depth}]->(s)) "
                "FOREACH (r IN $refs | MERGE (s:CodeSymbol {company_id: $company_id, "
                "root: $root, name: r.name}) "
                "CREATE (f)-[:REFERENCES {count: r.count}]->(s))",
                company_id=company_id,
                root=root,
                path=f["path"],
                sha256=f["sha256"],
                status=f["status"],
                truncated=f["truncated"],
                observed_at=now,
                defs=[
                    {"name": n, "line": line, "depth": d} for n, line, d in f["defs"]
                ],
                refs=[{"name": n, "count": c} for n, c in f["refs"]],
            )
        return len(files), gone

    def _pairs(
        self, relation: str, where: str, company_id: int, root: str, values: list[str]
    ):
        if not values:
            return []
        rows = self._run(
            f"MATCH {_FILE}-[:{relation}]->(s:CodeSymbol) WHERE {where} IN $values "
            "RETURN f.path AS path, s.name AS name",
            company_id=company_id,
            root=root,
            values=sorted(set(values)),
        )
        return [(r["path"], r["name"]) for r in rows]

    def references_of(self, company_id: int, root: str, paths: list[str]):
        return self._pairs("REFERENCES", "f.path", company_id, root, paths)

    def definitions_of(self, company_id: int, root: str, paths: list[str]):
        return self._pairs("DEFINES", "f.path", company_id, root, paths)

    def definers(self, company_id: int, root: str, names: list[str]):
        return self._pairs("DEFINES", "s.name", company_id, root, names)

    def referrers(self, company_id: int, root: str, names: list[str]):
        return self._pairs("REFERENCES", "s.name", company_id, root, names)

    def replace_edges(
        self, company_id: int, root: str, source: str, edges: list[dict[str, Any]]
    ) -> None:
        self._run(
            "MATCH (:CodeRef {company_id: $company_id, root: $root})"
            "-[e:CODE_EDGE {source: $source}]->() DELETE e",
            company_id=company_id,
            root=root,
            source=source,
        )
        if not edges:
            return
        self._run(
            "UNWIND $edges AS e "
            "MERGE (a:CodeRef {company_id: $company_id, root: $root, ref: e.from_ref}) "
            "MERGE (b:CodeRef {company_id: $company_id, root: $root, ref: e.to_ref}) "
            "CREATE (a)-[:CODE_EDGE {kind: e.kind, source: $source, "
            "source_ref: e.source_ref, detail: e.detail, recorded_at: $now}]->(b)",
            company_id=company_id,
            root=root,
            source=source,
            now=_now(),
            edges=[
                {
                    **e,
                    "detail": json.dumps(e.get("detail")) if e.get("detail") else None,
                }
                for e in edges
            ],
        )
