"""A company's regular expressions, on RE2 only (issue #700;
``docs/archive/design/COMPANY_STANDARDS.md`` §10 T5 and §12 decision 3).

A ``forbid_pattern`` standard carries a pattern a company wrote in its
repository, which agents also write to. Python's ``re`` backtracks, so a
pattern such as ``(a+)+$`` can take exponential time on a crafted line
(OWASP: Regular expression Denial of Service; CWE-1333). Every such pattern
runs here, on RE2 through the pinned ``google-re2`` binding: no
backtracking, time linear in the input, memory bounded by ``MAX_MEM``. A
pattern RE2 refuses (a back-reference, lookaround) is refused at import with
RE2's reason, never translated or retried on ``re``.

When the binding cannot be imported (a broken install), nothing falls back
to ``re``: ``compile_refusal`` names ``re2_unavailable``, so the import
refuses the standard, and ``search`` raises ``Unavailable``, which the
evaluation records as ``unverifiable``.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - the binding is a pinned main dependency.
    import re2 as _re2
except Exception:  # noqa: BLE001 - any import failure means "not available".
    _re2 = None

MAX_PATTERN_CHARS = 1000
# RE2's own memory budget for one compiled pattern, in bytes.
MAX_MEM = 8 * 1024 * 1024
UNAVAILABLE = "re2_unavailable"


class Unavailable(RuntimeError):
    """The RE2 binding is not importable; a pattern is never run on ``re``."""


def available() -> bool:
    return _re2 is not None


def _options() -> Any:
    options = _re2.Options()
    options.log_errors = False
    options.max_mem = MAX_MEM
    options.never_capture = True
    return options


def compile_refusal(pattern: Any) -> str | None:
    """Pure: why *pattern* cannot be a standard's pattern, or None.

    ``re2_unavailable`` when the binding is missing; otherwise the pattern's
    type and length, then RE2's own parse error."""
    if not isinstance(pattern, str) or not pattern:
        return "the pattern is a non-empty string"
    if len(pattern) > MAX_PATTERN_CHARS:
        return f"the pattern is longer than {MAX_PATTERN_CHARS} characters"
    if "\x00" in pattern:
        return "the pattern holds a NUL character"
    if _re2 is None:
        return UNAVAILABLE
    try:
        _re2.compile(pattern.encode("utf-8"), _options())
    except Exception as exc:  # noqa: BLE001 - RE2 raises its own error type.
        reason = exc.args[0] if exc.args else exc
        if isinstance(reason, bytes):
            reason = reason.decode("utf-8", "replace")
        return f"RE2 refuses the pattern: {reason}"
    return None


def compile(pattern: str) -> Any:
    """The compiled pattern, over bytes; raises ``Unavailable`` or ValueError."""
    if _re2 is None:
        raise Unavailable(UNAVAILABLE)
    refusal = compile_refusal(pattern)
    if refusal is not None:
        raise ValueError(refusal)
    return _re2.compile(pattern.encode("utf-8"), _options())


def matching_lines(compiled: Any, data: bytes, limit: int = 20) -> list[int]:
    """The 1-based numbers of the lines of *data* that *compiled* matches, up
    to *limit*. Each line is searched on its own, so ``^`` and ``$`` mean
    the line's start and end, as a person reading the file expects."""
    found: list[int] = []
    for number, line in enumerate(data.split(b"\n"), start=1):
        if compiled.search(line.rstrip(b"\r")) is not None:
            found.append(number)
            if len(found) >= limit:
                break
    return found
