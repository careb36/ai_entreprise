#!/usr/bin/env python3
"""The licence scan: every runtime dependency under a licence the project allows.

Issue #522, part C. Two kinds of source, read without a model and without any
third-party package (the runner's ``python3``, standard library only):

- ``--python NAME=PYTHON``: the interpreter of a throwaway virtual environment
  that holds nothing but one project's runtime lock, installed with hashes
  (``uv venv`` seeds no pip). The scan reads each installed distribution's own
  metadata, in this order: ``License-Expression`` (SPDX), the ``License``
  field, then the ``License ::`` classifiers. The metadata comes from the
  hashed files the lock names, not from a registry's description of them.
- ``--npm-lock PATH``: a ``package-lock.json`` (version 2 or 3). Every package
  ``npm ci --omit=dev`` would install, with the ``license`` the lockfile
  recorded from its ``package.json``.

A licence is allowed when its SPDX expression is satisfied by ``ALLOWED``: an
``OR`` needs one allowed side, an ``AND`` needs both, and ``X WITH exception``
counts as ``X`` (an exception only adds permissions). Anything else, an
unknown or missing licence included, is a finding unless
``.github/licence-exceptions.txt`` lists the package with the licence exactly
as the scan reports it, a review date that has not passed and a reason. An
entry with no reason or whose date has passed fails the scan, so an exception
is never permanent (the same rule as ``.github/pip-audit-ignore.txt``).

Exit status 0 when every package is allowed or excepted, 1 otherwise.
``.github/workflows/security-scan.yml`` (job ``licences``) runs it on every
pull request; ``backend/tests/test_licence_scan.py`` checks it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCEPTIONS = ROOT / ".github" / "licence-exceptions.txt"

# The allowlist (SECURITY.md §5.3). MPL-2.0 is file-level copyleft: using an
# unmodified package is allowed, changing its files means publishing them.
# "BSD" is a package that says only "BSD" or "BSD License" with no clause
# count; it is read as the 2- or 3-clause licence, which is what such packages
# carry in practice, and never as the 4-clause one.
ALLOWED = frozenset(
    {
        "MIT",
        "BSD",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "ISC",
        "MPL-2.0",
        "PSF-2.0",
        "Python-2.0",
        "Unlicense",
        "CC0-1.0",
        "0BSD",
        "BlueOak-1.0.0",
    }
)

# Spellings found in package metadata, lower-cased, mapped to an SPDX id.
ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "the mit license": "MIT",
    "mit licence": "MIT",
    "expat": "MIT",
    "apache": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache2": "Apache-2.0",
    "apache 2 license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache license version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache software license 2.0": "Apache-2.0",
    "bsd": "BSD",
    "bsd license": "BSD",
    "modified bsd license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "3-clause bsd license": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-3": "BSD-3-Clause",
    "simplified bsd license": "BSD-2-Clause",
    "2-clause bsd license": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "isc license": "ISC",
    "isc license (iscl)": "ISC",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "psf": "PSF-2.0",
    "psf license": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "the unlicense (unlicense)": "Unlicense",
    "cc0 1.0 universal (cc0 1.0) public domain dedication": "CC0-1.0",
}

# SPDX ids recognised as such (case-insensitively), allowed or not, so a
# finding names the licence rather than "unknown".
KNOWN_IDS = ALLOWED | {
    "MIT-0",
    "CNRI-Python",
    "CC-BY-4.0",
    "CC-BY-3.0",
    "CC-BY-SA-4.0",
    "BSD-4-Clause",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "SSPL-1.0",
    "EUPL-1.2",
    "Zlib",
    "WTFPL",
}
_BY_LOWER = {i.lower(): i for i in KNOWN_IDS}

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Package:
    ecosystem: str  # "pypi" or "npm"
    name: str
    version: str
    licence: str  # as reported: an SPDX expression, or the raw text
    project: str

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{self.name.lower()}"


# --- licence expressions ----------------------------------------------------------


def _token(text: str) -> str | None:
    """One licence name as an SPDX id, or None when it is not recognised."""
    value = text.strip().strip("()").strip()
    lower = value.lower()
    return _BY_LOWER.get(lower) or ALIASES.get(lower)


def _tokens(expression: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expression)


def normalise(expression: str) -> str | None:
    """*expression* with each name as its SPDX id, or None if a name is unknown."""
    whole = _token(expression)
    if whole:
        return whole
    out = []
    for tok in _tokens(expression):
        if tok in "()" or tok.upper() in ("AND", "OR", "WITH"):
            out.append(tok.upper() if tok not in "()" else tok)
        elif out and out[-1] == "WITH":
            out.append(tok)  # the exception's own id, kept as written
        else:
            spdx = _token(tok)
            if spdx is None:
                return None
            out.append(spdx)
    text = " ".join(out).replace("( ", "(").replace(" )", ")")
    return text or None


def allowed(expression: str) -> bool:
    """Whether a normalised SPDX *expression* is satisfied by ``ALLOWED``."""
    tokens = _tokens(expression)
    pos = 0

    def atom() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            return False
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            value = disjunction()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return value
        value = tok in ALLOWED
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 2  # an exception only adds permissions
        return value

    def conjunction() -> bool:
        nonlocal pos
        value = atom()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            value = atom() and value
        return value

    def disjunction() -> bool:
        nonlocal pos
        value = conjunction()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            value = conjunction() or value
        return value

    result = disjunction()
    return result and pos == len(tokens)


def _classifier(text: str) -> str:
    name = text.split("::")[-1].strip()
    spdx = _token(name)
    if spdx:
        return spdx
    lower = name.lower()
    if "affero" in lower:
        return "AGPL"
    if "lesser" in lower or "library" in lower:
        return "LGPL"
    if "general public" in lower:
        return "GPL"
    return name


def python_licence(meta: dict) -> str:
    """The licence a distribution's metadata states (see the module docstring)."""
    expression = (meta.get("license_expression") or "").strip()
    if expression:
        return normalise(expression) or expression
    raw = (meta.get("license") or "").strip()
    first = raw.splitlines()[0].strip() if raw else ""
    # A short field, or the heading of a full licence text ("MIT License").
    if first and len(first) <= 100:
        spdx = normalise(first)
        if spdx:
            return spdx
    found = sorted({_classifier(c) for c in meta.get("classifiers") or []})
    if found:
        # Several licence classifiers on one package mean a choice of them.
        return found[0] if len(found) == 1 else "(" + " OR ".join(found) + ")"
    return first or UNKNOWN


# --- sources ----------------------------------------------------------------------

# Run by the environment's own interpreter, isolated (-I): it prints each
# installed distribution's name, version and licence fields as JSON.
_DUMP = """
import importlib.metadata as m, json
out = []
for d in m.distributions():
    md = d.metadata
    out.append({
        "name": md["Name"], "version": md["Version"],
        "license_expression": md.get("License-Expression"),
        "license": md.get("License"),
        "classifiers": [c for c in (md.get_all("Classifier") or [])
                        if c.startswith("License ::")],
    })
print(json.dumps(out))
"""


def python_packages(project: str, python: str) -> list[Package]:
    raw = subprocess.run(
        [python, "-I", "-c", _DUMP], check=True, capture_output=True, text=True
    ).stdout
    return sorted(
        (
            Package("pypi", d["name"], d["version"], python_licence(d), project)
            for d in json.loads(raw)
        ),
        key=lambda p: p.key,
    )


def npm_packages(project: str, lock: Path) -> list[Package]:
    """Every package ``npm ci --omit=dev`` installs (a devOptional one included)."""
    data = json.loads(lock.read_text())
    seen: dict[tuple[str, str], Package] = {}
    for path, entry in (data.get("packages") or {}).items():
        if not path or entry.get("link") or entry.get("dev"):
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[-1]
        raw = entry.get("license")
        licence = (normalise(raw) or raw) if isinstance(raw, str) else UNKNOWN
        version = entry.get("version", "")
        seen[(name, version)] = Package("npm", name, version, licence, project)
    return sorted(seen.values(), key=lambda p: (p.key, p.version))


# --- exceptions -------------------------------------------------------------------


@dataclass(frozen=True)
class Exception_:
    key: str
    licence: str
    review: dt.date
    reason: str


def read_exceptions(text: str, today: dt.date) -> tuple[list[Exception_], list[str]]:
    """The entries, and the problems that fail the scan (format, date, reason).

    One per line: ``ecosystem:package | licence as reported | YYYY-MM-DD | reason``.
    """
    entries, problems = [], []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) != 4 or not all(parts) or ":" not in parts[0]:
            problems.append(
                f"line {number}: needs 'ecosystem:package | licence | "
                f"YYYY-MM-DD | reason'"
            )
            continue
        key, licence, review, reason = parts
        try:
            date = dt.date.fromisoformat(review)
        except ValueError:
            problems.append(f"line {number}: {review!r} is not a YYYY-MM-DD date")
            continue
        if date < today:
            problems.append(f"line {number}: {key} was to be reviewed by {review}")
            continue
        entries.append(Exception_(key.lower(), licence, date, reason))
    return entries, problems


def judge(
    packages: list[Package], exceptions: list[Exception_]
) -> tuple[list[Package], list[tuple[Package, Exception_]], list[Exception_]]:
    """(findings, excepted packages, exceptions no package used)."""
    by_key = {(e.key, e.licence): e for e in exceptions}
    findings, excepted, used = [], [], set()
    for package in packages:
        if package.licence != UNKNOWN and allowed(package.licence):
            continue
        entry = by_key.get((package.key, package.licence))
        if entry is None:
            findings.append(package)
        else:
            excepted.append((package, entry))
            used.add((entry.key, entry.licence))
    unused = [e for e in exceptions if (e.key, e.licence) not in used]
    return findings, excepted, unused


# --- command line -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--python", action="append", default=[], metavar="NAME=PY")
    parser.add_argument("--npm-lock", action="append", default=[], metavar="PATH")
    parser.add_argument("--exceptions", type=Path, default=EXCEPTIONS)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    if not args.python and not args.npm_lock:
        parser.error("give at least one --python NAME=PY or --npm-lock PATH")

    packages: list[Package] = []
    for spec in args.python:
        name, _, python = spec.partition("=")
        if not python:
            parser.error(f"--python {spec!r}: expected NAME=PATH")
        packages += python_packages(name, python)
    for lock in args.npm_lock:
        path = Path(lock)
        packages += npm_packages(path.parent.name or str(path), path)

    # dt.timezone.utc, not dt.UTC: the runner's python3 may predate 3.11.
    today = args.today or dt.datetime.now(dt.timezone.utc).date()  # noqa: UP017
    text = args.exceptions.read_text() if args.exceptions.is_file() else ""
    exceptions, problems = read_exceptions(text, today)
    findings, excepted, unused = judge(packages, exceptions)

    print(f"Scanned {len(packages)} runtime packages.")
    for package, entry in excepted:
        print(
            f"excepted  {package.project}: {package.key} {package.version} "
            f"[{package.licence}] until {entry.review}: {entry.reason}"
        )
    for entry in unused:
        print(f"note      exception for {entry.key} [{entry.licence}] matched nothing")
    for problem in problems:
        print(f"::error file=.github/licence-exceptions.txt::{problem}")
    for package in findings:
        print(
            f"::error::{package.project}: {package.key} {package.version} is under "
            f"[{package.licence}], which is not on the allowlist"
        )
    if findings or problems:
        print(f"{len(findings)} disallowed, {len(problems)} bad exception entries.")
        return 1
    print("Every runtime dependency is allowed or excepted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
