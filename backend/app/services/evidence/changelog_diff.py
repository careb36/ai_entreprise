"""The Verifier's ``changelog_matches_diff`` check: a fragment says only what the diff shows.

In sprint 8 (2026-11-24, issue #701) a Developer wrote
``changelog.d/701-container-sandbox-mode.md`` under ``Added``: the harness
"gains ``--container-mode`` (``scripts/self_host/sandbox_mode.py``,
``add_arguments`` and ``apply``)". The flag and both functions were already on
the base; the sprint had changed one test file, a README and the fragment. The
fragment followed the plan's task titles, not the diff, and QA approved it. In
sprint 7 a fragment named issue #780, which does not exist; the sprint's issue
was #756.

A fragment is the agent's claim. What it is judged against is observed by the
platform and recorded on the evidence as ``changelog_diff`` by the loop
(``services/loop/changelog_observe.py``), never taken from the agent:

- ``changed``: every path the delivery's commit changes against the merge base
  of the company's base branch (the daemon's ``workspace.git.diff``
  inventory, the task branch with the prerequisites merged into it), and
  ``changed_truncated`` when the daemon cut the list;
- ``fragments``: each fragment the delivery wrote, as git holds it at that
  commit (``workspace.git.read_at``; the sha256 computed by the backend);
- ``base_files``: the paths the fragments cite and the changed files, as the
  merge base holds them: ``status`` and, for each code name searched
  (``names_searched``), whether it occurs there (``found``, by ``found_in``);
  ``base_cut`` when not every file was read;
- ``sprint_issue``: the issue the sprint's objective (the person's own text)
  names, when it names exactly one (``sprint_issue``).

The rules, each explained in the observation:

a. Every repository path the fragment cites in backticks (a ``/`` or a known
   file suffix, and present in the diff or at the base) is among the changed
   files, a folder holding one, unless its sentence marks it as unchanged
   context with one of ``UNCHANGED_WORDS``. A cited path the diff did not
   touch refutes.
b. Under ``Added``, a cited code name (a ``--flag``, an identifier with ``_``
   or in camelCase, a ``name()``, or a plain word defined as ``def``/
   ``class``/``function``/``const``/an assignment) that already occurs at the
   base, in a file the fragment cites or a changed file, refutes: "claims to
   add what the base already has: X (in <path>)". The same context words
   excuse.
c. The issue in the file name, and in the summary's ``(issue #N)``, is the
   sprint's when the platform knows it; a mismatch refutes, an unknown issue
   is a finding.
d. What cannot be decided is ``unverifiable``, never ``pass``: no observation
   (no Git commit, no daemon), a cut listing, a fragment not read, a file not
   read at the base. The ``risk_tier`` check then rises to ``high``
   (``hold_reason``), so a person decides.

A refuted check sends the delivery back before QA (``delivery_gate``).

Pure: no database, no I/O.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.evidence.doc_links import PATH_SUFFIXES

CHECK_NAME = "changelog_matches_diff"
EVIDENCE_KEY = "changelog_diff"
HOLD_AREA = "changelog"
FOLDER = "changelog.d"
ADDED = "Added"
MAX_BASE_FILES = 60
SHOWN = 20
# Words that mark a sentence as describing context the diff did not change.
# Keep the list small: each word lets a sentence cite unchanged code.
UNCHANGED_WORDS = (
    "already",
    "existing",
    "existed",
    "unchanged",
    "untouched",
    "ya existía",
    "ya existia",
    "ya existe",
    "sin cambios",
)
_CODE = re.compile(r"`([^`\n]+)`")
_PATH = re.compile(r"^[\w.\-/]+$")
_FLAG = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*)")
_IDENT = re.compile(r"^[A-Za-z_][\w.]*(\(\))?$")
_CAMEL = re.compile(r"[a-z][A-Z]")
_SENTENCE_END = re.compile(r"[.!?](?:\*\*)?(?=\s)")
_FILE_ISSUE = re.compile(r"^(\d+)-")
_ISSUE = re.compile(r"\bissues?\s+#(\d+)", re.IGNORECASE)
_SUMMARY = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def is_fragment(path: Any) -> bool:
    """A changelog fragment: ``changelog.d/<name>.md``, not its README."""
    if not isinstance(path, str):
        return False
    folder, _, name = path.strip("/").rpartition("/")
    return folder == FOLDER and name.endswith(".md") and name.upper() != "README.MD"


def fragments_written(evidence: dict[str, Any] | None) -> list[str]:
    """The fragments the daemon reports this delivery wrote (``workspace_writes``)."""
    writes = (evidence or {}).get("workspace_writes") or []
    return sorted(
        {
            w["path"].strip("/")
            for w in writes
            if isinstance(w, dict) and w.get("status") == "written"
            and is_fragment(w.get("path"))
        }
    )  # fmt: skip


def _sentences(text: str) -> list[str]:
    """*text* cut into sentences; a ``.`` inside backticks never ends one."""
    masked = _CODE.sub(lambda m: "`" + "x" * len(m.group(1)) + "`", text)
    out, start = [], 0
    for match in _SENTENCE_END.finditer(masked):
        out.append(text[start : match.end()])
        start = match.end()
    out.append(text[start:])
    return [s for s in out if s.strip()]


def _excused(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(word in lowered for word in UNCHANGED_WORDS)


def _path_shaped(token: str) -> bool:
    if not _PATH.match(token) or token.startswith(("-", "/", ".")) or "//" in token:
        return False
    if "/" in token:
        return True
    return "." in token and token.rsplit(".", 1)[-1].lower() in PATH_SUFFIXES


def _name_kind(name: str) -> str:
    if name.startswith("--"):
        return "flag"
    if "_" in name.strip("_") or _CAMEL.search(name):
        return "identifier"
    return "word"


def _names(token: str) -> list[str]:
    flags = _FLAG.findall(token)
    if flags:
        return flags
    match = _IDENT.match(token)
    if not match:
        return []
    name = token.removesuffix("()").rsplit(".", 1)[-1]
    if token.endswith("()") and "_" not in name and not _CAMEL.search(name):
        return [name + "()"]
    return [name] if name else []


def parse(path: str, text: str) -> dict[str, Any]:
    """What fragment *path* cites: its section, paths, code names and issues."""
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), len(lines))
    section = lines[first].strip() if first < len(lines) else ""
    body = "\n".join(lines[first + 1 :])
    paths: dict[str, bool] = {}
    names: dict[str, bool] = {}
    for sentence in _sentences(body):
        excused = _excused(sentence)
        for token in _CODE.findall(sentence):
            token = token.strip()
            if _path_shaped(token):
                key = posixpath.normpath(token.rstrip("/"))
                paths[key] = paths.get(key, True) and excused
            elif section == ADDED:
                for name in _names(token):
                    names[name] = names.get(name, True) and excused
    issues = []
    if match := _FILE_ISSUE.match(posixpath.basename(path)):
        issues.append(int(match.group(1)))
    if summary := _SUMMARY.search(body):
        issues += [int(n) for n in _ISSUE.findall(summary.group(1))]
    return {
        "path": path,
        "section": section,
        "paths": [{"path": p, "excused": e} for p, e in paths.items()],
        "names": [
            {"name": n, "kind": _name_kind(n.removesuffix("()")), "excused": e}
            for n, e in names.items()
        ],
        "issues": sorted(set(issues)),
    }


def base_paths(
    parsed: list[dict[str, Any]], changed: list[str]
) -> tuple[list[str], bool]:
    """The paths to read at the merge base (cited first, then changed), and
    whether ``MAX_BASE_FILES`` cut them."""
    wanted = [c["path"] for p in parsed for c in p["paths"]]
    wanted += [c for c in changed if not is_fragment(c)]
    unique = list(dict.fromkeys(w for w in wanted if w))
    return unique[:MAX_BASE_FILES], len(unique) > MAX_BASE_FILES


def names_to_search(parsed: list[dict[str, Any]]) -> list[str]:
    """The code names whose presence at the base rule b needs."""
    return list(dict.fromkeys(n["name"] for p in parsed for n in p["names"]))


def _pattern(name: str) -> re.Pattern[str]:
    bare = re.escape(name.removesuffix("()"))
    kind = _name_kind(name.removesuffix("()"))
    if kind == "flag":
        return re.compile(rf"(?<![\w-]){bare}(?![\w-])")
    if kind == "identifier" or name.endswith("()"):
        return re.compile(rf"\b{bare}\b")
    return re.compile(
        rf"^\s*(?:async\s+)?(?:def|class)\s+{bare}\b"
        rf"|\b(?:function|const|let|var)\s+{bare}\b"
        rf"|^\s*{bare}\s*(?::[^=\n]*)?=",
        re.MULTILINE,
    )


def found_in(text: str, names: list[str]) -> list[str]:
    """The *names* that occur in *text* (a file's bytes at the base), by kind:
    a flag or a distinctive identifier anywhere, a plain word as a definition."""
    return [n for n in names if _pattern(n).search(text)]


def sprint_issue(objective: str | None) -> dict[str, Any]:
    """The issue the sprint's objective names, when it names exactly one."""
    named = sorted({int(n) for n in _ISSUE.findall(objective or "")})
    if len(named) == 1:
        return {"number": named[0], "source": "the sprint's objective"}
    reason = "names no issue" if not named else f"names several issues {named}"
    return {"number": None, "reason": f"the sprint's objective {reason}"}


def _shown(items: list[str]) -> str:
    more = f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else ""
    return "; ".join(items[:SHOWN]) + more


def _unverifiable(reason: str, fragments: list[str]) -> dict[str, Any]:
    return {
        "name": CHECK_NAME,
        "outcome": VERDICT_UNVERIFIABLE,
        "fragments": fragments,
        "observation": f"{reason}; the fragment's claims cannot be compared with the diff",
    }


def _judge_paths(
    fragment: dict[str, Any],
    record: dict[str, Any],
    base: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    changed = set(record.get("changed") or [])
    cut = bool(record.get("changed_truncated"))
    refuted: list[str] = []
    undecided: list[str] = []
    findings: list[str] = []
    for cited in fragment["paths"]:
        path = cited["path"]
        if path in changed or any(c.startswith(path + "/") for c in changed):
            continue
        if cited["excused"]:
            continue
        status = (base.get(path) or {}).get("status")
        if cut:
            undecided.append(f"`{path}` (the daemon cut the list of changed files)")
        elif status in ("present", "not_file", "symlink", "too_large"):
            refuted.append(f"`{path}`")
        elif status == "missing":
            findings.append(f"`{path}` is neither in the diff nor at the base")
        else:
            undecided.append(f"`{path}` (not read at the base)")
    return refuted, undecided, findings


def _judge_names(
    fragment: dict[str, Any],
    record: dict[str, Any],
    base: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    searched = set(record.get("names_searched") or [])
    refuted: list[str] = []
    undecided: list[str] = []
    for cited in fragment["names"]:
        name = cited["name"]
        if cited["excused"]:
            continue
        where = [p for p, f in base.items() if name in (f.get("found") or [])]
        if where:
            paths = ", ".join(f"`{p}`" for p in sorted(where)[:3])
            refuted.append(f"`{name}` (in {paths})")
        elif name not in searched or record.get("base_cut"):
            undecided.append(f"`{name}` (not every file was searched at the base)")
    return refuted, undecided


def _judge_issue(
    fragment: dict[str, Any], issue: dict[str, Any]
) -> tuple[list[str], list[str]]:
    number = issue.get("number")
    if not isinstance(number, int):
        reason = issue.get("reason") or "the sprint's issue is not known"
        return [], [f"issue not compared: {reason}"]
    wrong = [n for n in fragment["issues"] if n != number]
    if wrong:
        named = ", ".join(f"#{n}" for n in wrong)
        source = issue.get("source") or "known to the platform"
        return [f"names issue {named}; the sprint's issue is #{number} ({source})"], []
    return [], []


def check(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """The check for a delivery that wrote a changelog fragment; ``None`` otherwise."""
    written = fragments_written(evidence)
    if not written:
        return None
    record = (evidence or {}).get(EVIDENCE_KEY)
    if not isinstance(record, dict):
        return _unverifiable("no observation of the task's diff was recorded", written)
    if record.get("error"):
        return _unverifiable(
            f"the task's diff was not observed: {record['error']}", written
        )
    if not isinstance(record.get("changed"), list):
        return _unverifiable("the daemon listed no changed files", written)
    texts = {
        f.get("path"): f.get("text")
        for f in record.get("fragments") or []
        if isinstance(f, dict) and f.get("status") == "present"
    }
    base = {
        f["path"]: f
        for f in record.get("base_files") or []
        if isinstance(f, dict) and isinstance(f.get("path"), str)
    }
    issue = record.get("sprint_issue") or {}
    refuted: list[str] = []
    undecided: list[str] = []
    findings: list[str] = []
    for path in written:
        text = texts.get(path)
        if not isinstance(text, str):
            undecided.append(f"{path}: not read at the delivery's commit")
            continue
        fragment = parse(path, text)
        bad_paths, open_paths, notes = _judge_paths(fragment, record, base)
        bad_names, open_names = _judge_names(fragment, record, base)
        bad_issue, issue_notes = _judge_issue(fragment, issue)
        if bad_paths:
            refuted.append(
                f"{path} cites paths the diff did not change: {', '.join(bad_paths)}"
            )
        if bad_names:
            refuted.append(
                f"{path} claims to add what the base already has: {', '.join(bad_names)}"
            )
        refuted += [f"{path} {r}" for r in bad_issue]
        undecided += [f"{path}: {u}" for u in open_paths + open_names]
        findings += [f"{path}: {n}" for n in notes + issue_notes]
    result: dict[str, Any] = {
        "name": CHECK_NAME,
        "fragments": written,
        "changed": len(record["changed"]),
        "refuted": refuted,
        "unchecked": undecided,
    }
    if findings:
        result["findings"] = findings
    rule = (
        "a fragment describes only what the diff against "
        f"{record.get('base') or 'the base'} shows (cited paths changed, names "
        "under Added new, the sprint's issue); context is marked with "
        "'already', 'existing' or 'unchanged'"
    )
    if refuted:
        result.update(
            outcome=VERDICT_REFUTED,
            observation=f"{len(refuted)} claim(s) the diff does not show: "
            f"{_shown(refuted)}. Rule: {rule}",
        )
    elif undecided:
        result.update(
            outcome=VERDICT_UNVERIFIABLE,
            observation=f"{len(undecided)} claim(s) not compared: {_shown(undecided)}",
        )
    else:
        result.update(
            outcome="pass",
            observation=f"{len(written)} fragment(s) match the diff of "
            f"{len(record['changed'])} changed file(s)",
        )
    return result


def hold_reason(result: dict[str, Any] | None) -> str | None:
    """Why a person decides: the fragment could not be compared with the diff."""
    if result and result.get("outcome") == VERDICT_UNVERIFIABLE:
        return "a changelog fragment could not be compared with the task's diff"
    return None
