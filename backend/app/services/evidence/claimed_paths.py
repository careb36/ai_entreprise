"""The paths a run's producer declared, read the same way wherever they are read.

``claims["files"]``, the paths named in the claims' prose, and the files the
workspace edit tool changed by the daemon's record. A declared path is a
claim: it may add to a classification or a match, never remove one. Moved out
of ``verification.py`` so the Verifier's first look and a later re-match
(``protected_check.touched``) cover the same sources (PR #624 review).
"""

from __future__ import annotations

import re
from typing import Any

from services.evidence import context_read

_PATH_PATTERN = re.compile(
    # Longest extension first, and no continuation after it, so `a.tsx` is not
    # read as `a.ts` followed by a stray `x`.
    r"[A-Za-z0-9_./-]+\.(?:json|toml|lock|html|yaml|yml|tsx|jsx|py|ts|js|md|sql|css|txt)"
    r"(?![A-Za-z0-9_])"
)
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def claim_strings(claims: Any):
    if isinstance(claims, dict):
        for value in claims.values():
            yield from claim_strings(value)
    elif isinstance(claims, list):
        for value in claims:
            yield from claim_strings(value)
    elif isinstance(claims, str):
        yield claims


def extract_claimed_paths(claims: dict | None) -> list[str]:
    paths = []
    seen = set()
    for text in claim_strings(claims):
        without_urls = _URL_PATTERN.sub("", text)
        for match in _PATH_PATTERN.finditer(without_urls):
            path = match.group(0)
            if "/" in path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def declared_paths(claims: dict | None, evidence: dict | None) -> list[str]:
    """``claims.files``, the paths in the claims' prose, and the edit tool's files."""
    claims = claims or {}
    declared = [p for p in claims.get("files") or [] if isinstance(p, str)]
    edited = sorted(context_read.files_edited((evidence or {}).get("tool_calls")))
    return [*declared, *extract_claimed_paths(claims), *edited]
