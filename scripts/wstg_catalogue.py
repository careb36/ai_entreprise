#!/usr/bin/env python3
"""Build La Fabrique's versioned catalogue of the OWASP Web Security Testing Guide.

    python3 scripts/wstg_catalogue.py /path/to/wstg [--out backend/app/data/wstg/catalogue.json]

Reads ``checklists/checklist.json`` (the guide's current tests) and
``checklists/WSTG-Checklist_v4.2.xlsx`` (the tests of release 4.2) from a
checkout of https://github.com/OWASP/wstg and writes the adapted catalogue
(issue #539). Each test gets a versioned identifier, in the scheme the
security probe uses (``services/evidence/security_probe.py``, issue #540):

- ``WSTG-v42-<CAT>-<NN>`` when release 4.2 has the same test under the same
  identifier (its name equal once the "Test"/"Testing for" prefixes, case,
  punctuation and plurals are set aside, or listed in ``SAME_TEST``);
- ``WSTG-<commit7>-<CAT>-<NN>`` otherwise: a test 4.2 lacks, or one whose
  identifier 4.2 gave to another test (``API Reconnaissance`` is ``APIT-01``
  now; in 4.2 that was ``Testing GraphQL``).

The output is a derivative of the guide and stays under CC BY-SA 4.0 with the
attribution written into it (and listed in ``NOTICE``). The script itself is
Apache-2.0 like the rest of the code. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "backend" / "app" / "data" / "wstg" / "catalogue.json"
SOURCE_URL = "https://github.com/OWASP/wstg"
# Renamed in the current guide but the same test as in 4.2.
SAME_TEST = {"INFO-01"}
_PREFIX = re.compile(r"^(?:testing for|testing|test for|test)\s+", re.IGNORECASE)
_ID = re.compile(r"WSTG-([A-Z]{4})-(\d\d)")


def _normal(name: str) -> str:
    text = _PREFIX.sub("", name.strip()).lower().replace("-", "")
    words = re.findall(r"[a-z0-9]+", text)
    return "".join(
        w[:-1] if w.endswith("s") else w for w in words if w not in ("the", "and")
    )


def v42_names(xlsx: Path) -> dict[str, str]:
    """The test names of release 4.2's checklist workbook, by short id (``CONF-06``)."""
    with zipfile.ZipFile(xlsx) as z:
        shared = re.findall(
            r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode(), re.S
        )
        strings = ["".join(re.findall(r"<t[^>]*>(.*?)</t>", s, re.S)) for s in shared]
        names: dict[str, str] = {}
        sheets = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))
        for sheet in sheets:
            xml = z.read(sheet).decode()
            for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
                values = []
                for attrs, inner in re.findall(
                    r"<c ([^>]*?)(?:/>|>(.*?)</c>)", row, re.S
                ):
                    v = re.search(r"<v>(.*?)</v>", inner or "")
                    if not v:
                        values.append("")
                    elif 't="s"' in attrs:
                        values.append(strings[int(v.group(1))])
                    else:
                        values.append(v.group(1))
                for i, value in enumerate(values):
                    match = _ID.fullmatch(value)
                    if match and i + 1 < len(values):
                        short = f"{match.group(1)}-{match.group(2)}"
                        names.setdefault(short, values[i + 1])
                        break
    return names


def build(checkout: Path, commit: str) -> dict:
    checklist = json.loads((checkout / "checklists" / "checklist.json").read_text())
    old = v42_names(checkout / "checklists" / "WSTG-Checklist_v4.2.xlsx")
    at = commit[:7]
    categories = []
    for name, category in checklist["categories"].items():
        tests = []
        for test in category["tests"]:
            match = _ID.fullmatch(test["id"])
            if not match:
                raise SystemExit(f"unexpected test id {test['id']!r}")
            short = f"{match.group(1)}-{match.group(2)}"
            same = short in SAME_TEST or (
                short in old and _normal(old[short]) == _normal(test["name"])
            )
            tests.append(
                {
                    "id": f"WSTG-{'v42' if same else at}-{short}",
                    "short": short,
                    "upstream_id": test["id"],
                    "name": test["name"],
                    "in_v42": same,
                    "reference": test.get("reference") or "",
                    "objectives": list(test.get("objectives") or []),
                }
            )
        categories.append({"id": category["id"], "name": name, "tests": tests})
    return {
        "licence": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": (
            "Adapted from the OWASP Web Security Testing Guide checklist "
            f"(checklists/checklist.json and checklists/WSTG-Checklist_v4.2.xlsx, "
            f"{SOURCE_URL}, commit {commit}), by the OWASP Foundation and the WSTG "
            "contributors, licensed under CC BY-SA 4.0."
        ),
        "changes": (
            "La Fabrique added the versioned identifiers (WSTG-v42-... where release "
            "4.2 has the same test, WSTG-<commit>-... otherwise), the short id and "
            "the in_v42 flag; names, references and objectives are the guide's."
        ),
        "source": SOURCE_URL,
        "commit": commit,
        "categories": categories,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path, help="a checkout of OWASP/wstg")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--commit", help="the checkout's commit (read from git if omitted)"
    )
    args = parser.parse_args(argv)
    commit = (
        args.commit
        or subprocess.run(
            ["git", "-C", str(args.checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        print(f"not a commit: {commit!r}", file=sys.stderr)
        return 2
    catalogue = build(args.checkout, commit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalogue, indent=1, ensure_ascii=False) + "\n")
    count = sum(len(c["tests"]) for c in catalogue["categories"])
    print(f"wrote {args.out} ({count} tests, commit {commit[:7]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
