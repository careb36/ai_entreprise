#!/usr/bin/env python3
"""
La Fabrique — Infrastructure Verification Script
=================================================
Validates Neo4j (TKG), Postgres/pgvector, and OpenProject without invoking
any LLM. Run from the project root:

    python scripts/verify_infra.py [--env /path/to/.env]

Running from the HOST (outside Docker):
    python scripts/verify_infra.py --local
    # Translates Docker-internal hostnames (neo4j, postgres, openproject)
    # → localhost / 127.0.0.1 so the script can reach containers from macOS.

Exit codes:
    0  All checks passed
    1  One or more checks failed

The checks themselves live in ``scripts/infra_checks/``, one module per piece
of infrastructure; this file is the command line and the summary.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import List

from infra_checks.neo4j_suite import check_neo4j
from infra_checks.openproject_suite import check_openproject
from infra_checks.postgres_suite import check_postgres
from infra_checks.report import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    YELLOW,
    Suite,
    colour,
    head,
    info,
    note,
    warn,
)

# ── Optional dependency: python-dotenv ───────────────────────────────────────
try:
    from dotenv import load_dotenv
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False


# ── Docker service name → localhost translation ───────────────────────────────

_DOCKER_SERVICES = {"neo4j", "postgres", "nats", "openproject"}


def _apply_local_overrides() -> None:
    """Translate Docker-internal service hostnames → localhost for host execution."""
    note("--local mode: translating Docker service names → localhost / 127.0.0.1")

    def _replace_host(val: str, replacement: str) -> str:
        for svc in _DOCKER_SERVICES:
            val = val.replace(f"//{svc}:", f"//{replacement}:")
            val = val.replace(f"//{svc}/", f"//{replacement}/")
        return val

    # NEO4J_URI
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    new_uri = _replace_host(uri, "localhost")
    if new_uri != uri:
        os.environ["NEO4J_URI"] = new_uri
        print(info(f"NEO4J_URI: {uri!r} → {new_uri!r}"))

    # POSTGRES_HOST — use 127.0.0.1 (IPv4) to avoid native-Postgres on [::1]
    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    if pg_host in _DOCKER_SERVICES:
        os.environ["POSTGRES_HOST"] = "127.0.0.1"
        print(info(f"POSTGRES_HOST: {pg_host!r} → '127.0.0.1'"))
    elif pg_host in ("localhost", "::1"):
        os.environ["POSTGRES_HOST"] = "127.0.0.1"
        print(info(f"POSTGRES_HOST: {pg_host!r} → '127.0.0.1'  (forcing IPv4)"))

    # OPENPROJECT_URL — replace Docker service name; port stays the same
    op_url = os.getenv("OPENPROJECT_URL", "")
    new_op = _replace_host(op_url, "localhost")
    if new_op != op_url:
        os.environ["OPENPROJECT_URL"] = new_op
        print(info(f"OPENPROJECT_URL: {op_url!r} → {new_op!r}"))
        note("If Docker exposes OpenProject on a different host port, set OPENPROJECT_URL manually.")

    print()


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

def _load_env(env_file: Path) -> None:
    if _HAS_DOTENV:
        load_dotenv(env_file, override=False)
    else:
        # Minimal hand-rolled parser — no dependency required
        if not env_file.exists():
            return
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _banner() -> None:
    print(colour("""
╔══════════════════════════════════════════════════════════════╗
║        La Fabrique — Infrastructure Verification             ║
║        Neo4j TKG · Postgres/pgvector · OpenProject           ║
╚══════════════════════════════════════════════════════════════╝
""", CYAN, BOLD))


def _summary(suites: List[Suite]) -> int:
    all_results = [r for s in suites for r in s.results]
    passed = sum(1 for r in all_results if r.passed)
    failed = sum(1 for r in all_results if not r.passed)

    print(colour(f"\n{'═'*60}", CYAN))
    print(colour(f"  Summary", CYAN, BOLD))
    print(colour(f"{'═'*60}", CYAN))

    for suite in suites:
        icon = colour("✓", GREEN, BOLD) if suite.all_passed else colour("✗", RED, BOLD)
        print(f"  {icon}  {suite.name}")
        for r in suite.results:
            sub_icon = colour("✓", GREEN) if r.passed else colour("✗", RED)
            ms_str   = colour(f"  {r.elapsed_ms:.0f}ms", DIM) if r.elapsed_ms else ""
            print(f"       {sub_icon} {r.name}{ms_str}")

    print()
    if failed == 0:
        print(colour(f"  ✓  All {passed} checks passed. Infrastructure is ready.", GREEN, BOLD))
    else:
        print(colour(f"  {passed} passed  ·  {failed} failed", BOLD))
        print(colour("  Fix the failing checks before starting the LangGraph simulation.", YELLOW))
    print()

    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify La Fabrique infrastructure (Neo4j, Postgres, OpenProject)"
    )
    parser.add_argument(
        "--env",
        default=str(Path(__file__).parent.parent / ".env"),
        help="Path to .env file (default: <project-root>/.env)",
    )
    parser.add_argument(
        "--only",
        choices=["neo4j", "postgres", "openproject"],
        default=None,
        help="Run only one suite",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        default=False,
        help=(
            "Translate Docker-internal service hostnames (neo4j, postgres, openproject) "
            "→ localhost/127.0.0.1 for running from the host machine outside Docker."
        ),
    )
    args = parser.parse_args()

    env_path = Path(args.env)
    _load_env(env_path)

    _banner()

    if env_path.exists():
        print(info(f"Loaded env vars from: {env_path}"))
    else:
        print(warn(f".env not found at {env_path} — using shell environment only"))

    print()

    if args.local:
        _apply_local_overrides()

    suites: List[Suite] = []

    CHECKS = [
        ("neo4j",       "Neo4j TKG",          check_neo4j),
        ("postgres",    "Postgres / pgvector", check_postgres),
        ("openproject", "OpenProject API",     check_openproject),
    ]

    for key, label, fn in CHECKS:
        if args.only and args.only != key:
            continue
        print(head(label))
        suite = Suite(label)
        try:
            fn(suite)
        except Exception:
            suite.add(
                "Unexpected error",
                False,
                traceback.format_exc(),
            )
        suites.append(suite)

    return _summary(suites)


if __name__ == "__main__":
    sys.exit(main())
