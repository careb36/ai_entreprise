#!/usr/bin/env python3
"""
One place for the backend's configuration (issue #766).

The backend's operational values (timeouts, budgets, retries, caps) are
settings: fields of ``Settings`` in ``backend/app/config.py``, with a typed
default, read as ``settings.X`` and documented in ``docs/CONFIGURATION.md``.
A value read with ``os.getenv`` where it is used escapes all three: its
default is copied by hand (the proxy URL had two), ``.env`` does not reach
it, and nothing documents it. After AixleHQ/flow's ``NoEnvInApp`` rule and
its documentation test, this check holds two rules:

1. under ``backend/app``, only ``config.py`` reads the environment
   (``os.getenv``, ``os.environ``, or either imported from ``os``). The
   files that still do are listed in ``scripts/config_env_allowlist.json``
   with how many reads each has and why; an entry may shrink but never
   grow, and a file not listed may not start. ``--update`` lowers the counts
   and drops the files that no longer read it;
2. every field of ``Settings`` is named in ``docs/CONFIGURATION.md``.

Exit status 0 when both hold, 1 otherwise. Run from anywhere.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "backend" / "app"
CONFIG = APP / "config.py"
DOC = ROOT / "docs" / "CONFIGURATION.md"
ALLOWLIST = ROOT / "scripts" / "config_env_allowlist.json"


def env_reads(source: str) -> int:
    """How many times *source* reads the environment."""
    tree = ast.parse(source)
    from_os = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "os"
        for alias in node.names
        if alias.name in {"getenv", "environ"}
    }
    count = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"getenv", "environ"}
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            count += 1
        elif isinstance(node, ast.Name) and node.id in from_os:
            count += 1
    return count


def scan(app: Path = APP) -> dict[str, int]:
    """Every file under *app* but ``config.py`` that reads the environment."""
    found = {}
    for path in sorted(app.rglob("*.py")):
        if path == app / "config.py" or "__pycache__" in path.parts:
            continue
        reads = env_reads(path.read_text(encoding="utf-8"))
        if reads:
            found[path.relative_to(app).as_posix()] = reads
    return found


def settings_fields(source: str) -> list[str]:
    """The field names of the ``Settings`` class in *source*."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return [
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            ]
    return []


def undocumented(fields: list[str], doc: str) -> list[str]:
    return [name for name in fields if not re.search(rf"\b{re.escape(name)}\b", doc)]


def compare(found: dict[str, int], allowed: dict[str, dict]) -> tuple[list[str], list[str]]:
    """(failures, files that could be lowered with --update)."""
    failures, lower = [], []
    for name, reads in found.items():
        entry = allowed.get(name)
        if entry is None:
            failures.append(
                f"{name}: reads the environment {reads} time(s); make it a setting in "
                "backend/app/config.py and read settings.X"
            )
        elif reads > entry["reads"]:
            failures.append(
                f"{name}: reads the environment {reads} time(s), the allowlist allows "
                f"{entry['reads']}; make the new value a setting"
            )
        elif reads < entry["reads"]:
            lower.append(name)
    lower += [name for name in allowed if name not in found]
    return failures, lower


def main(argv: list[str]) -> int:
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    allowed = data["files"]
    found = scan()
    failures, lower = compare(found, allowed)
    missing = undocumented(
        settings_fields(CONFIG.read_text(encoding="utf-8")), DOC.read_text(encoding="utf-8")
    )
    failures += [f"{name}: a setting not named in docs/CONFIGURATION.md" for name in missing]
    if "--update" in argv and lower:
        for name in lower:
            if name in found:
                allowed[name]["reads"] = found[name]
            else:
                del allowed[name]
        ALLOWLIST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"check_config: lowered {len(lower)} entr(ies)")
        lower = []
    for line in failures:
        print(f"check_config: {line}")
    for name in lower:
        print(f"check_config: {name} reads the environment less now; run with --update")
    if failures:
        return 1
    total = sum(found.values())
    print(f"check_config: ok ({total} environment read(s) left in {len(found)} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
