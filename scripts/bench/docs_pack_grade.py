#!/usr/bin/env python3
"""Grade a documentation pack against a person's expectations (issue #698).

A pack (``backend/app/services/planning/project_docs``) is compiled from the
records of a reference specification's run. Nothing measured whether it is
complete and right; this grader compares it with an expectations file a
person wrote for that specification, and writes a card of what held.

    docs_pack_grade.py PACK.json EXPECTATIONS.json [--out CARD.json]

``PACK.json`` is what ``GET /v1/documentation/{pack_id}`` returns (an object
with ``documents``), or the list of documents itself. ``EXPECTATIONS.json``::

    {
      "format": "la-fabrique.docs-pack-expectations/1",
      "spec": "05-confcheck",
      "written_by": "who wrote these expectations",
      "documents": {
        "<document key>": {
          "headings": ["## A heading line, exactly"],
          "contains": ["a text the document must hold"],
          "complete": true
        }
      },
      "traceability": {
        "requirements": ["REQ-1", "REQ-2"],
        "every_requirement_has_a_task": true,
        "verified": true,
        "integrated": false
      },
      "checks": {"<check id>": "met"}
    }

Every key but ``format`` is optional. What is graded, one item each:

- **Documents.** The document is in the pack; each heading is one of its
  lines; each text is in it; ``complete`` means it lists no section without
  a record (its ``missing`` is empty).
- **Traceability.** Read from the matrix's ``data.trace`` (the records the
  table renders, in no language). Each expected requirement has a row, and
  no row names a requirement the person did not list; with
  ``every_requirement_has_a_task``, each has a task; with ``verified``, each
  linked task's latest verification is ``verified``; with ``integrated``,
  each was integrated.
- **Checks.** Each named check's computed result (``data.checks`` of the
  ``checks`` document) equals the expected one (``met``, ``not met``,
  ``measured``, ``not computable``, ``person``).

An item is ``pass``, ``fail`` or ``unmeasured``. A pack compiled before the
documents carried ``data`` leaves the traceability and the checks
``unmeasured``, never failed and never passed. The card's verdict is
``pass`` only when every item passed. The digests on the card are the
grader's own, over the files it read.

Exit 0 on a pass, 1 on a fail, 3 when something is unmeasured and nothing
failed, 2 on a file it cannot read.

The expectations are a person's: the grader never writes them, and a pack
graded against expectations written from that same pack measures nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXPECTATIONS_FORMAT = "la-fabrique.docs-pack-expectations/1"
CARD_FORMAT = "la-fabrique.docs-pack-grade/1"
PASS, FAIL, UNMEASURED = "pass", "fail", "unmeasured"
RESULTS = ("met", "not met", "measured", "not computable", "person")
TRACE_KEY, CHECKS_KEY = "traceability", "checks"
EXIT_PASS, EXIT_FAIL, EXIT_INPUT, EXIT_UNMEASURED = 0, 1, 2, 3


class InputError(ValueError):
    """A pack or an expectations file that cannot be graded."""


def _item(what: str, outcome: str, detail: str = "") -> dict[str, str]:
    return {"what": what, "outcome": outcome, "detail": detail}


def _strings(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InputError(f"{where} must be a list of strings")
    return value


def _absent_as(value: Any, default: Any) -> Any:
    """*default* for a key left out (or null); the value as given otherwise."""
    return default if value is None else value


def check_expectations(expect: Any) -> dict[str, Any]:
    """*expect* if it is an expectations file; ``InputError`` says why not."""
    if not isinstance(expect, dict):
        raise InputError("the expectations must be a JSON object")
    if expect.get("format") != EXPECTATIONS_FORMAT:
        raise InputError(f"the expectations' format must be {EXPECTATIONS_FORMAT!r}")
    documents = _absent_as(expect.get("documents"), {})
    if not isinstance(documents, dict):
        raise InputError("documents must be an object keyed by document key")
    for key, spec in documents.items():
        if not isinstance(spec, dict):
            raise InputError(f"documents.{key} must be an object")
        _strings(spec.get("headings"), f"documents.{key}.headings")
        _strings(spec.get("contains"), f"documents.{key}.contains")
    trace = _absent_as(expect.get("traceability"), {})
    if not isinstance(trace, dict):
        raise InputError("traceability must be an object")
    _strings(trace.get("requirements"), "traceability.requirements")
    checks = _absent_as(expect.get("checks"), {})
    if not isinstance(checks, dict):
        raise InputError("checks must be an object keyed by check id")
    for check_id, result in checks.items():
        if result not in RESULTS:
            raise InputError(f"checks.{check_id} must be one of {', '.join(RESULTS)}")
    return expect


def documents_of(pack: Any) -> dict[str, dict[str, Any]]:
    """The pack's documents by key."""
    listed = pack.get("documents") if isinstance(pack, dict) else pack
    if not isinstance(listed, list):
        raise InputError("the pack must be an object with documents, or a list")
    out = {}
    for doc in listed:
        if isinstance(doc, dict) and isinstance(doc.get("key"), str):
            out[doc["key"]] = doc
    return out


def _grade_documents(docs: dict, expected: dict) -> list[dict[str, str]]:
    items = []
    for key, spec in expected.items():
        doc = docs.get(key)
        if doc is None:
            items.append(_item(f"document {key}", FAIL, "not in the pack"))
            continue
        items.append(_item(f"document {key}", PASS))
        markdown = str(doc.get("markdown") or "")
        lines = {line.strip() for line in markdown.splitlines()}
        for heading in _strings(spec.get("headings"), key):
            found = heading.strip() in lines
            items.append(_item(f"{key}: heading {heading!r}", PASS if found else FAIL))
        for text in _strings(spec.get("contains"), key):
            found = text in markdown
            items.append(_item(f"{key}: contains {text!r}", PASS if found else FAIL))
        if spec.get("complete"):
            missing = doc.get("missing") or []
            items.append(
                _item(
                    f"{key}: complete",
                    FAIL if missing else PASS,
                    f"no record for: {', '.join(map(str, missing))}" if missing else "",
                )
            )
    return items


def _grade_traceability(docs: dict, expected: dict) -> list[dict[str, str]]:
    if not expected:
        return []
    wanted = _strings(expected.get("requirements"), "traceability.requirements")
    doc = docs.get(TRACE_KEY)
    rows = ((doc or {}).get("data") or {}).get("trace")
    if doc is None:
        return [_item("traceability matrix", FAIL, "not in the pack")]
    if not isinstance(rows, list):
        return [
            _item(
                "traceability",
                UNMEASURED,
                "the matrix carries no data (a pack compiled before #698)",
            )
        ]
    by_req: dict[str, list[dict]] = {}
    for row in rows:
        if isinstance(row, dict):
            by_req.setdefault(str(row.get("requirement")), []).extend(
                t for t in row.get("tasks") or [] if isinstance(t, dict)
            )
    items = []
    for req in wanted:
        if req not in by_req:
            items.append(_item(f"traceability: {req} listed", FAIL, "no row"))
            continue
        items.append(_item(f"traceability: {req} listed", PASS))
        links = by_req[req]
        if expected.get("every_requirement_has_a_task"):
            items.append(
                _item(
                    f"traceability: {req} has a task",
                    PASS if links else FAIL,
                    "" if links else "no task covers it",
                )
            )
        if expected.get("verified"):
            unverified = [
                str(t.get("task_id")) for t in links if t.get("verdict") != "verified"
            ]
            ok = bool(links) and not unverified
            detail = (
                "no task"
                if not links
                else (
                    f"not verified: T-{', T-'.join(unverified)}" if unverified else ""
                )
            )
            items.append(
                _item(f"traceability: {req} verified", PASS if ok else FAIL, detail)
            )
        if expected.get("integrated"):
            loose = [str(t.get("task_id")) for t in links if not t.get("integrated")]
            ok = bool(links) and not loose
            detail = (
                "no task"
                if not links
                else (f"not integrated: T-{', T-'.join(loose)}" if loose else "")
            )
            items.append(
                _item(f"traceability: {req} integrated", PASS if ok else FAIL, detail)
            )
    if wanted:
        extra = sorted(set(by_req) - set(wanted))
        items.append(
            _item(
                "traceability: no requirement beyond the expected",
                FAIL if extra else PASS,
                f"also listed: {', '.join(extra)}" if extra else "",
            )
        )
    return items


def _grade_checks(docs: dict, expected: dict) -> list[dict[str, str]]:
    if not expected:
        return []
    doc = docs.get(CHECKS_KEY)
    if doc is None:
        return [_item("check results", FAIL, "not in the pack")]
    listed = (doc.get("data") or {}).get("checks")
    if not isinstance(listed, list):
        return [
            _item(
                "check results",
                UNMEASURED,
                "the checks carry no data (a pack compiled before #698)",
            )
        ]
    results = {c.get("id"): c.get("result") for c in listed if isinstance(c, dict)}
    items = []
    for check_id, want in expected.items():
        got = results.get(check_id)
        if got is None:
            items.append(_item(f"check {check_id}", FAIL, "not computed in the pack"))
            continue
        items.append(
            _item(
                f"check {check_id}",
                PASS if got == want else FAIL,
                "" if got == want else f"expected {want!r}, computed {got!r}",
            )
        )
    return items


def grade(pack: Any, expect: Any) -> dict[str, Any]:
    """The card: every item graded, the totals and the verdict."""
    expect = check_expectations(expect)
    docs = documents_of(pack)
    items = (
        _grade_documents(docs, expect.get("documents") or {})
        + _grade_traceability(docs, expect.get("traceability") or {})
        + _grade_checks(docs, expect.get("checks") or {})
    )
    totals = {
        o: sum(1 for i in items if i["outcome"] == o) for o in (PASS, FAIL, UNMEASURED)
    }
    graded = totals[PASS] + totals[FAIL]
    verdict = (
        FAIL
        if totals[FAIL]
        else (UNMEASURED if totals[UNMEASURED] or not items else PASS)
    )
    return {
        "format": CARD_FORMAT,
        "spec": expect.get("spec"),
        "pack_id": pack.get("id") if isinstance(pack, dict) else None,
        "expectations_written_by": expect.get("written_by"),
        "items": items,
        "totals": {**totals, "total": len(items)},
        "score": round(totals[PASS] / graded, 4) if graded else None,
        "verdict": verdict,
    }


def _read(path: Path) -> tuple[Any, str]:
    try:
        raw = path.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def render(card: dict[str, Any]) -> str:
    """The card as Markdown, failures first."""
    t = card["totals"]
    lines = [
        f"# Documentation pack grade: {card.get('spec') or '-'}",
        "",
        (
            f"Verdict **{card['verdict']}**: {t['pass']} passed, {t['fail']} failed, "
            f"{t['unmeasured']} unmeasured of {t['total']}."
        ),
        "",
        "| Outcome | What | Detail |",
        "|---|---|---|",
    ]
    order = {FAIL: 0, UNMEASURED: 1, PASS: 2}
    for item in sorted(card["items"], key=lambda i: order[i["outcome"]]):
        detail = item["detail"].replace("|", "\\|") or "-"
        what = item["what"].replace("|", "\\|")
        lines.append(f"| {item['outcome']} | {what} | {detail} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pack", type=Path)
    parser.add_argument("expectations", type=Path)
    parser.add_argument(
        "--out", type=Path, help="write the card here (JSON, and .md beside it)"
    )
    args = parser.parse_args(argv)
    try:
        pack, pack_sha = _read(args.pack)
        expect, expect_sha = _read(args.expectations)
        card = grade(pack, expect)
    except InputError as exc:
        print(f"docs_pack_grade: {exc}", file=sys.stderr)
        return EXIT_INPUT
    card["pack_sha256"], card["expectations_sha256"] = pack_sha, expect_sha
    if args.out:
        args.out.write_text(
            json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        args.out.with_suffix(".md").write_text(render(card), encoding="utf-8")
    print(render(card), end="")
    return {PASS: EXIT_PASS, FAIL: EXIT_FAIL, UNMEASURED: EXIT_UNMEASURED}[
        card["verdict"]
    ]


if __name__ == "__main__":
    sys.exit(main())
