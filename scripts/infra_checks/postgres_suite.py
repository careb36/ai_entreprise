"""Postgres and pgvector: connect, the extension, the vector_memory table, the <=> operator."""

from __future__ import annotations

import os

from .report import Suite, hint, timed


def check_postgres(suite: Suite) -> None:
    try:
        import psycopg2
    except ImportError:
        suite.add("Driver import", False,
                  "psycopg2 package not installed.\n"
                  "Run: pip install psycopg2-binary")
        return

    host     = os.getenv("POSTGRES_HOST",     "localhost")
    port     = int(os.getenv("POSTGRES_PORT", "5432"))
    user     = os.getenv("POSTGRES_USER",     "factory")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db       = os.getenv("POSTGRES_DB",       "factory")

    missing = [v for v, val in [
        ("POSTGRES_PASSWORD", password),
    ] if not val]
    if missing:
        suite.add("Credentials present", False,
                  f"Missing: {', '.join(missing)}")
        for v in missing:
            hint(v, "required for Postgres connection")
        return

    suite.add("Credentials present", True,
              f"host={host}:{port}  db={db}  user={user}")

    # ── Connect ──────────────────────────────────────────────────────────────
    conn = None
    try:
        conn, ms = timed(lambda: psycopg2.connect(
            host=host, port=port, user=user,
            password=password, dbname=db,
            connect_timeout=5,
        ))
        suite.add("TCP connection", True, f"Connected to {host}:{port}/{db}", ms)
    except Exception as exc:
        err_str = str(exc)
        detail = (
            f"{exc}\n"
            "Check POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB."
        )
        # Native macOS Postgres conflict: our role exists only in the Docker container
        if "role" in err_str and "does not exist" in err_str:
            detail += (
                f"\n\n⚠ Port conflict detected: a non-Docker Postgres on port {port} "
                f"rejected role '{user}' (that role lives only inside the Docker container).\n"
                "Fix options:\n"
                "  A) Stop native Postgres (macOS):\n"
                "       brew services stop postgresql@14   # adjust version as needed\n"
                "  B) Remap Docker Postgres to port 5433 in docker-compose.yml:\n"
                "       ports: ['5433:5432']\n"
                "     then add POSTGRES_PORT=5433 to your .env and re-run with --local"
            )
        suite.add("TCP connection", False, detail)
        hint("POSTGRES_HOST",     "hostname/IP of your Postgres server")
        hint("POSTGRES_PASSWORD", "your Postgres password")
        return

    cursor = conn.cursor()

    # ── pgvector extension ────────────────────────────────────────────────────
    try:
        cursor.execute(
            "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
        )
        row = cursor.fetchone()
        if row:
            suite.add("pgvector extension", True,
                      f"Extension '{row[0]}' version {row[1]} is installed.")
        else:
            # Try to create it (requires superuser or CREATE privilege)
            try:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                conn.commit()
                suite.add("pgvector extension", True,
                          "Extension 'vector' was not present — created successfully.")
            except Exception as inner:
                suite.add("pgvector extension", False,
                          f"Extension not installed and auto-create failed: {inner}\n"
                          "Run as superuser: CREATE EXTENSION vector;")
    except Exception as exc:
        suite.add("pgvector extension", False, str(exc))

    # ── vector_memory table ───────────────────────────────────────────────────
    try:
        cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'vector_memory'
            ORDER BY ordinal_position;
        """)
        cols = cursor.fetchall()
        if cols:
            col_summary = ", ".join(f"{c[0]}:{c[1]}" for c in cols)
            suite.add("vector_memory table", True, f"Columns: {col_summary}")
        else:
            suite.add("vector_memory table", False,
                      "Table 'vector_memory' does not exist.\n"
                      "Run db/init-db/ migrations to create the schema.")
    except Exception as exc:
        suite.add("vector_memory table", False, str(exc))

    # ── round-trip vector INSERT + cosine search ──────────────────────────────
    try:
        # A 3-dim dummy vector — just enough to prove the operator works.
        # We use a TEXT cast so the test works even when the column is 1536-dim.
        cursor.execute("""
            SELECT '[1,2,3]'::vector <=> '[1,2,3]'::vector AS cosine_dist;
        """)
        dist = cursor.fetchone()[0]
        assert abs(dist) < 1e-6, f"Expected ~0.0, got {dist}"
        suite.add("pgvector <=> operator", True,
                  f"Cosine distance [1,2,3]↔[1,2,3] = {dist:.6f} (expected 0.0)")
    except Exception as exc:
        suite.add("pgvector <=> operator", False,
                  f"{exc}\n"
                  "Ensure pgvector is installed and the 'vector' type is available.")

    cursor.close()
    conn.close()
