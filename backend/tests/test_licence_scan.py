"""The licence scan (issue #522, part C): ``scripts/licence_scan.py``.

Every runtime dependency of the backend, the llm-proxy, the daemon and the
frontend must be under a licence on the allowlist, or be listed in
``.github/licence-exceptions.txt`` with that licence, a reason and a review
date that has not passed. These tests check the parts on small inputs of
their own, the command against a real environment's metadata, and the
committed exceptions file.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "licence_scan.py"
_spec = importlib.util.spec_from_file_location("licence_scan", SCRIPT)
assert _spec and _spec.loader
scan = importlib.util.module_from_spec(_spec)
sys.modules["licence_scan"] = scan  # dataclasses resolve their module by name
_spec.loader.exec_module(scan)

TODAY = dt.date(2026, 11, 15)


@pytest.mark.parametrize(
    ("text", "normal"),
    [
        ("MIT", "MIT"),
        ("MIT License", "MIT"),
        ("Apache License, Version 2.0", "Apache-2.0"),
        ("Apache 2.0", "Apache-2.0"),
        ("Modified BSD License", "BSD-3-Clause"),
        ("bsd-3-clause", "BSD-3-Clause"),
        ("(MIT OR Apache-2.0)", "(MIT OR Apache-2.0)"),
        ("MPL-2.0 AND (Apache-2.0 OR MIT)", "MPL-2.0 AND (Apache-2.0 OR MIT)"),
        ("Apache-2.0 WITH LLVM-exception", "Apache-2.0 WITH LLVM-exception"),
        ("LGPL with exceptions", None),
        ("Some Custom Licence", None),
    ],
)
def test_licence_names_are_normalised_to_spdx(text, normal):
    assert scan.normalise(text) == normal


@pytest.mark.parametrize(
    ("expression", "ok"),
    [
        ("MIT", True),
        ("BSD", True),
        ("MPL-2.0", True),
        ("BlueOak-1.0.0", True),
        ("GPL-3.0-only", False),
        ("AGPL-3.0-or-later", False),
        ("SSPL-1.0", False),
        ("MIT-0", False),
        ("(MIT OR GPL-3.0-only)", True),
        ("MIT OR GPL-3.0-only", True),
        ("GPL-3.0-only OR MIT", True),
        ("MIT AND GPL-3.0-only", False),
        ("Apache-2.0 AND CNRI-Python", False),
        ("MPL-2.0 AND (Apache-2.0 OR MIT)", True),
        ("MPL-2.0 AND (GPL-2.0-only OR LGPL-3.0-only)", False),
        ("Apache-2.0 WITH LLVM-exception", True),
        ("GPL-2.0-only WITH Classpath-exception-2.0", False),
        ("UNKNOWN", False),
        ("", False),
    ],
)
def test_an_expression_is_allowed_only_as_spdx_reads_it(expression, ok):
    assert scan.allowed(expression) is ok


@pytest.mark.parametrize(
    ("meta", "licence"),
    [
        (
            {"license_expression": "Apache-2.0 OR BSD-2-Clause"},
            "Apache-2.0 OR BSD-2-Clause",
        ),
        ({"license_expression": "", "license": "Apache 2.0"}, "Apache-2.0"),
        (
            {"license": "MIT License\n\nCopyright (c) someone\n\nPermission is hereby"},
            "MIT",
        ),
        (
            {
                "license": "LGPL with exceptions",
                "classifiers": [
                    "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)"
                ],
            },
            "LGPL",
        ),
        ({"classifiers": ["License :: OSI Approved :: BSD License"]}, "BSD"),
        (
            {
                "classifiers": [
                    "License :: OSI Approved :: MIT License",
                    "License :: OSI Approved :: Apache Software License",
                ]
            },
            "(Apache-2.0 OR MIT)",
        ),
        ({"license": "Proprietary"}, "Proprietary"),
        ({}, "UNKNOWN"),
    ],
)
def test_a_distribution_licence_is_read_from_its_metadata_in_order(meta, licence):
    assert scan.python_licence(meta) == licence


def _lock(tmp_path: Path, packages: dict) -> Path:
    path = tmp_path / "frontend" / "package-lock.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
    return path


def test_the_npm_scan_reads_the_runtime_set_of_the_lockfile(tmp_path):
    lock = _lock(
        tmp_path,
        {
            "": {"name": "app", "license": "MIT"},
            "node_modules/react": {"version": "18.3.1", "license": "MIT"},
            "node_modules/@scope/lib": {
                "version": "1.0.0",
                "license": "(MIT OR Apache-2.0)",
            },
            "node_modules/vitest": {"version": "3.0.0", "license": "MIT", "dev": True},
            "node_modules/typescript": {
                "version": "5.0.0",
                "license": "Apache-2.0",
                "devOptional": True,
            },
            "node_modules/a/node_modules/old": {"version": "0.1.0"},
            "node_modules/local": {"resolved": "../local", "link": True},
        },
    )
    found = {(p.name, p.licence) for p in scan.npm_packages("frontend", lock)}
    assert found == {
        ("react", "MIT"),
        ("@scope/lib", "(MIT OR Apache-2.0)"),
        ("typescript", "Apache-2.0"),
        ("old", "UNKNOWN"),
    }


def _pkg(name: str, licence: str, ecosystem: str = "npm") -> object:
    return scan.Package(ecosystem, name, "1.0.0", licence, "frontend")


def test_an_exception_matches_only_its_package_and_licence():
    entries, problems = scan.read_exceptions(
        "# comment\n\nnpm:old | UNKNOWN | 2026-12-31 | the lock has no field\n"
        "npm:gone | MIT-0 | 2026-12-31 | nothing uses it now\n",
        TODAY,
    )
    assert problems == []
    findings, excepted, unused = scan.judge(
        [
            _pkg("old", "UNKNOWN"),
            _pkg("react", "MIT"),
            _pkg("viral", "GPL-3.0-only"),
            _pkg("Old", "GPL-3.0-only"),  # same package, another licence
        ],
        entries,
    )
    assert [p.name for p in findings] == ["viral", "Old"]
    assert [(p.name, e.reason) for p, e in excepted] == [
        ("old", "the lock has no field")
    ]
    assert [e.key for e in unused] == ["npm:gone"]


@pytest.mark.parametrize(
    "line",
    [
        "npm:old | UNKNOWN | 2026-12-31 |",
        "npm:old | UNKNOWN | 2026-12-31",
        "old | UNKNOWN | 2026-12-31 | no ecosystem",
        "npm:old | UNKNOWN | 31/12/2026 | a reason",
        "npm:old | UNKNOWN | 2026-11-14 | expired yesterday",
    ],
)
def test_a_bad_or_expired_exception_is_a_problem(line):
    entries, problems = scan.read_exceptions(line, TODAY)
    assert entries == [] and len(problems) == 1


def test_an_exception_reviewed_today_still_holds():
    entries, problems = scan.read_exceptions(
        "npm:old | UNKNOWN | 2026-11-15 | reviewed today", TODAY
    )
    assert problems == [] and len(entries) == 1


def test_the_command_fails_on_a_disallowed_licence_and_passes_when_excepted(
    tmp_path, capsys
):
    lock = _lock(
        tmp_path,
        {
            "node_modules/ok": {"version": "1.0.0", "license": "ISC"},
            "node_modules/viral": {"version": "2.0.0", "license": "AGPL-3.0-only"},
        },
    )
    none = tmp_path / "none.txt"
    none.write_text("")
    assert scan.main(["--npm-lock", str(lock), "--exceptions", str(none)]) == 1
    out = capsys.readouterr().out
    assert "npm:viral 2.0.0 is under [AGPL-3.0-only]" in out

    accepted = tmp_path / "accepted.txt"
    accepted.write_text("npm:viral | AGPL-3.0-only | 2099-01-01 | owner accepted\n")
    assert scan.main(["--npm-lock", str(lock), "--exceptions", str(accepted)]) == 0

    expired = tmp_path / "expired.txt"
    expired.write_text("npm:viral | AGPL-3.0-only | 2000-01-01 | owner accepted\n")
    assert scan.main(["--npm-lock", str(lock), "--exceptions", str(expired)]) == 1
    assert "was to be reviewed by 2000-01-01" in capsys.readouterr().out


def test_the_python_scan_reads_a_real_environment(tmp_path):
    # This interpreter's own environment stands in for a throwaway one: the
    # dump runs isolated and every distribution comes back with a licence.
    packages = scan.python_packages("backend", sys.executable)
    names = {p.name.lower(): p for p in packages}
    assert "pytest" in names and names["pytest"].licence == "MIT"
    assert all(p.ecosystem == "pypi" and p.licence for p in packages)


def test_every_committed_exception_has_a_licence_a_review_date_and_a_reason():
    # The format only: an expired entry fails the CI job (`licences`), not
    # every unrelated backend run, as for .github/pip-audit-ignore.txt.
    text = (REPO / ".github" / "licence-exceptions.txt").read_text()
    entries, problems = scan.read_exceptions(text, dt.date.min)
    assert problems == []
    assert entries and all(e.key.split(":")[0] in ("pypi", "npm") for e in entries)
