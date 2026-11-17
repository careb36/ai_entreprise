"""A specification's canonical text and its sha256 (REQ-20 of ``docs/design/rsi-loop.md``).

``learning/findings.py`` keys a finding by the sha256 of its specification's
canonical text, and the cycle runner (``scripts/rsi/cycle.py``) compares a
specification with the reverted ones by the same key, without a database. Both
read these two functions, so the rule is written once. Pure: no I/O, and it
imports no other context.
"""

from __future__ import annotations

import hashlib
import json


def normalize_specification_text(text: str) -> str:
    """The specification's canonical text, the one REQ-20 compares.

    A JSON specification is parsed and written again with its keys sorted
    and no whitespace between tokens, so pretty or compact, its keys in any
    order, its non-ASCII characters escaped or not, it is the same text; its
    string values are kept exactly, whitespace included, since that is what
    a requirement says. Any other text keeps its words and its spacing and
    only forgets its line endings (CRLF, CR), the trailing whitespace of
    each line and blank lines at either end.
    """
    raw = text or ""
    try:
        parsed = json.loads(raw)
    except ValueError:
        pass
    else:
        return json.dumps(
            parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def specification_sha256(text: str) -> str:
    """The sha256 of the canonical text, computed by the caller's side —
    never received from a producer (rule 6)."""
    normalized = normalize_specification_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
