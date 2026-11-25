"""The file manifest of a deliverable: which files an agent's answer declares.

An agent writes each file as a fenced block whose info string carries
``path=<relative path>``; a block marked ``edit`` carries SEARCH/REPLACE
edits instead (``edit_blocks``). The task worker sends what this module reads
to the daemon, and the evidence (the Verifier, the standards check, the
acceptance guard) reads the same manifest back from the stored artifact, so
the shape lives here, apart from both (ADR 0013). Pure: no I/O, no decision.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from services.contracts import edit_blocks

ALLOWED_ARTIFACT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".scss",
    ".html",
    ".sql",
    ".sh",
    ".go",
    ".java",
    ".kt",
    ".rs",
    ".rb",
    ".php",
    ".cs",
    ".vue",
    ".svelte",
    ".xml",
    ".ini",
    ".cfg",
    ".graphql",
}

# Fenced block whose info string carries ``path=<relative path>``, e.g.
# ```python path=src/auth/login.py
MANIFEST_BLOCK = re.compile(
    r"^```(?P<info>[^\n`]*\bpath=[^\n`]*)\n(?P<body>.*?)^```[ \t]*$",
    flags=re.DOTALL | re.MULTILINE,
)
_MANIFEST_PATH = re.compile(r"""\bpath=(?:"([^"]+)"|'([^']+)'|(\S+))""")


def sanitize_relative_path(path: str) -> str | None:
    """Normalize a relative path and reject unsafe values."""
    p = (path or "").strip().replace("\\", "/")
    if not p or p.startswith("/") or ".." in p.split("/"):
        return None
    return p


def _sha256_text(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def parse_file_manifest(deliverable: str) -> list[dict[str, str]]:
    """
    Extract the files an agent declared with ```` ```<lang> path=<file> ```` blocks.

    Each block becomes one ``{"path", "content"}`` entry (plus ``edits`` when marked ``edit``).
    Unsafe paths (absolute, ``..``) and disallowed extensions are dropped.  When
    the same path appears twice the last block wins, keeping the position of
    the first.  Returns ``[]`` when the answer declares no files.
    """
    files: dict[str, dict] = {}
    for block in MANIFEST_BLOCK.finditer(deliverable or ""):
        match = _MANIFEST_PATH.search(block.group("info"))
        if not match:
            continue
        rel = sanitize_relative_path(next(g for g in match.groups() if g))
        if not rel:
            continue
        if os.path.splitext(rel)[1].lower() not in ALLOWED_ARTIFACT_EXTENSIONS:
            continue
        files[rel] = {"path": rel, "content": block.group("body")}
        if edit_blocks.is_edit_block(block.group("info"), match):
            files[rel]["edits"] = edit_blocks.parse(block.group("body"))
        elif edits := edit_blocks.parse(block.group("body")):
            # Only well-formed SEARCH/REPLACE edits and nothing else, the
            # ``edit`` flag left out: no whole file is made of edits alone,
            # and written as one it would never parse (run 9, task 56).
            files[rel]["edits"] = edits
    return list(files.values())


def expected_hashes(targets: list[dict[str, Any]]) -> dict[str, str]:
    """The sha256 of what was sent; none for an edit, whose result only the daemon knows."""
    return {t["path"]: _sha256_text(t["content"]) for t in targets if "edits" not in t}
