"""Neo4j (the knowledge graph): connect, create and delete a node, look for APOC."""

from __future__ import annotations

import os
from typing import Optional
from uuid import uuid4

from .report import Suite, hint, timed


def check_neo4j(suite: Suite) -> None:
    try:
        import neo4j
    except ImportError:
        suite.add("Driver import", False,
                  "neo4j package not installed.\n"
                  "Run: pip install neo4j")
        hint("—", "pip install neo4j")
        return

    uri      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
    user     = os.getenv("NEO4J_USER",     "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")

    if not password:
        suite.add("Credentials present", False,
                  "NEO4J_PASSWORD is empty.")
        hint("NEO4J_PASSWORD", "your Neo4j database password")
        return

    suite.add("Credentials present", True,
              f"URI={uri}  USER={user}  PASSWORD={'*' * len(password)}")

    # ── Connect ──────────────────────────────────────────────────────────────
    driver: Optional[neo4j.Driver] = None
    try:
        driver, ms = timed(lambda: neo4j.GraphDatabase.driver(
            uri, auth=(user, password),
            connection_timeout=5,
            max_transaction_retry_time=5,
        ))
        driver.verify_connectivity()
        suite.add("TCP connection", True, f"Connected to {uri}", ms)
    except Exception as exc:
        suite.add("TCP connection", False,
                  f"{exc}\nVerify NEO4J_URI is reachable and the password is correct.")
        hint("NEO4J_URI",      "bolt://<host>:7687")
        hint("NEO4J_PASSWORD", "your Neo4j password")
        return

    # ── Create dummy Semantic_JavaClass node ─────────────────────────────────
    node_id = f"verify_infra_{uuid4().hex[:8]}"
    create_cypher = """
        CREATE (n:Semantic_JavaClass:_VerifyInfra {
            id: $id,
            fqn: 'com.fabrique.VerifyInfra',
            created_at: datetime()
        })
        RETURN n.id AS id
    """
    try:
        with driver.session() as session:
            result, ms = timed(lambda: session.run(create_cypher, id=node_id).single())
        returned_id = result["id"] if result else None
        assert returned_id == node_id, f"Expected {node_id!r}, got {returned_id!r}"
        suite.add(
            "CREATE Semantic_JavaClass node",
            True,
            f"Node id={node_id!r}  label=Semantic_JavaClass:_VerifyInfra",
            ms,
        )
    except Exception as exc:
        suite.add("CREATE Semantic_JavaClass node", False, str(exc))
        return
    finally:
        # ── Cleanup (always runs, even if assert failed) ──────────────────────
        try:
            with driver.session() as session:
                deleted, ms = timed(lambda: session.run(
                    "MATCH (n:_VerifyInfra {id: $id}) DELETE n RETURN count(n) AS cnt",
                    id=node_id,
                ).single())
            cnt = deleted["cnt"] if deleted else 0
            suite.add("Cleanup (DELETE node)", True, f"Deleted {cnt} node(s)", ms)
        except Exception as exc:
            suite.add("Cleanup (DELETE node)", False, str(exc))
        try:
            driver.close()
        except Exception:
            pass

    # ── APOC availability (optional — absence is a warning, not a failure) ──────
    try:
        with neo4j.GraphDatabase.driver(uri, auth=(user, password)) as drv:
            with drv.session() as session:
                result = session.run(
                    "CALL apoc.help('text') YIELD name WITH name LIMIT 1 RETURN name"
                ).single()
        suite.add("APOC plugin (optional)", True, f"apoc.{result['name']} available")
    except Exception:
        # Not a blocking failure — APOC is only needed for advanced graph ops
        suite.add("APOC plugin (optional)", True,
                  "APOC not installed. Not required for core features.\n"
                  "To enable: add APOC jar to Neo4j Docker image.")
