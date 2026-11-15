"""
The checks behind ``scripts/verify_infra.py``, one module per piece of
infrastructure: ``neo4j_suite`` (the knowledge graph), ``postgres_suite``
(Postgres and pgvector) and ``openproject_suite`` (the OpenProject API).
``report`` holds what they share: the coloured output and the ``Suite`` that
records each check's result.
"""
