"""The comparison of the symbol part of the ``grounded`` check (issue #444,
``services/evidence/signatures.py``): it reads only what the daemon reported
and the delivered text. Too many or too few positionals and unknown keywords
are found; star arguments, defaults and unpacking accepted; a rebound,
shadowed, unknown or missing name left alone; imports and relative imports
reach the definition; what it asks the daemon for stays within its bounds;
a mismatch is a finding that leaves the verdict verified.
"""

from __future__ import annotations

from models.company import Company
from services.evidence import grounding
from services.evidence import signatures as symbol_check

# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def _sig(**fields):
    base = {
        "status": "found",
        "kind": "function",
        "line": 1,
        "positional_only": [],
        "positional": [],
        "positional_defaults": 0,
        "varargs": False,
        "keyword_only": [],
        "varkw": False,
    }
    return {**base, **fields}


def _evidence(names, files=("pkg/lib.py", "pkg/__init__.py")):
    return {
        "project_files": {"files": list(files), "truncated": False},
        "signatures": {
            "observed": True,
            "files": [{"path": "pkg/lib.py", "sha256": "ab" * 32, "names": names}],
        },
    }


def _audit(code, names, path="app.py"):
    return symbol_check.audit(_evidence(names), [{"path": path, "content": code}])


def test_too_many_and_too_few_positionals_are_found():
    names = {"f": _sig(positional=["a", "b"], positional_defaults=1)}
    code = "from pkg.lib import f\n\nf(1, 2, 3)\nf()\nf(1)\nf(1, 2)\n"
    result = _audit(code, names)
    problems = [(m["line"], m["problem"]) for m in result["mismatches"]]
    assert problems == [
        (3, "takes at most 2 positional, 3 given"),
        (4, "missing 'a'"),
    ]
    assert result["checked"] == 4
    first = result["mismatches"][0]
    assert first["source"] == "daemon" and first["sha256"] == "ab" * 32
    assert first["defined_in"] == "pkg/lib.py" and first["signature"] == "f(a, b=…)"
    assert first["passed"] == {"positional": 3, "keywords": []}


def test_keywords_are_held_to_the_parameter_names():
    names = {
        "f": _sig(
            positional_only=["a"],
            positional=["b"],
            keyword_only=[
                {"name": "c", "default": False},
                {"name": "d", "default": True},
            ],
        )
    }
    code = (
        "from pkg.lib import f\n"
        "f(1, 2, c=3, d=4)\n"  # fine
        "f(1, b=2, c=3)\n"  # fine
        "f(1, 2, c=3, z=9)\n"  # unknown keyword
        "f(1, 2)\n"  # missing keyword-only
        "f(a=1, b=2, c=3)\n"  # positional-only by keyword
        "f(1, 2, b=2, c=3)\n"  # given twice
    )
    problems = {m["line"]: m["problem"] for m in _audit(code, names)["mismatches"]}
    assert problems == {
        4: "no parameter 'z'",
        5: "missing 'c'",
        6: "'a' is positional-only; missing 'a'",
        7: "'b' given twice",
    }


def test_star_args_kwargs_defaults_and_unpacking_are_accepted():
    names = {
        "g": _sig(positional=["a"], varargs=True, varkw=True),
        "h": _sig(positional=["a", "b"], positional_defaults=2),
    }
    code = (
        "from pkg.lib import g, h\n"
        "g(1, 2, 3, x=1, y=2)\n"
        "h()\nh(1)\nh(b=2)\n"
        "args = (1, 2, 3)\nh(*args)\nh(**{'z': 1})\n"
    )
    result = _audit(code, names)
    assert result["mismatches"] == []
    assert result["checked"] == 4  # the two unpacking calls are skipped


def test_a_rebound_or_shadowed_name_is_left_alone():
    names = {"f": _sig(positional=["a"])}
    rebound = "from pkg.lib import f\nf = print\nf(1, 2, 3)\n"
    shadowed = "from pkg.lib import f\n\ndef run(f):\n    f(1, 2, 3)\n"
    assert _audit(rebound, names)["mismatches"] == []
    assert _audit(shadowed, names)["mismatches"] == []


def test_unknown_and_missing_names_are_left_alone():
    names = {"f": {"status": "unknown", "kind": "function"}, "g": {"status": "missing"}}
    result = _audit("from pkg.lib import f, g\nf(1, 2, 3)\ng()\n", names)
    assert result["mismatches"] == [] and result["checked"] == 0


def test_module_imports_reach_the_definition():
    names = {"f": _sig(positional=["a"])}
    code = (
        "import pkg.lib\nimport pkg.lib as m\nfrom pkg import lib\n\n"
        "pkg.lib.f()\nm.f(1, 2)\nlib.f(a=1, b=2)\n"
    )
    result = symbol_check.audit(_evidence(names), [{"path": "app.py", "content": code}])
    assert [m["callee"] for m in result["mismatches"]] == ["pkg.lib.f", "m.f", "lib.f"]


def test_a_delivered_files_own_definitions_are_checked():
    files = [
        {
            "path": "pkg/tools.py",
            "content": "class Box:\n    def __init__(self, size):\n        pass\n\n"
            "def make(n, *, label):\n    return Box(n, n)\n",
        },
        {"path": "app.py", "content": "from pkg.tools import make\nmake(1)\n"},
    ]
    result = symbol_check.audit({"project_files": {"files": []}}, files)
    problems = sorted((m["path"], m["problem"]) for m in result["mismatches"])
    assert problems == [
        ("app.py", "missing 'label'"),
        ("pkg/tools.py", "takes at most 1 positional, 2 given"),
    ]
    assert all(m["source"] == "delivered" for m in result["mismatches"])


def test_a_relative_import_names_the_sibling_file():
    names = {"f": _sig(positional=["a"])}
    files = [{"path": "pkg/app.py", "content": "from .lib import f\nf()\n"}]
    result = symbol_check.audit(_evidence(names), files)
    assert result["mismatches"][0]["defined_in"] == "pkg/lib.py"


def test_a_standard_library_name_never_matches_a_nested_project_file():
    names = {"loads": _sig(positional=["a"])}
    evidence = _evidence(names, files=("pkg/json.py",))
    evidence["signatures"]["files"][0]["path"] = "pkg/json.py"
    result = symbol_check.audit(
        evidence, [{"path": "app.py", "content": "from json import loads\nloads()\n"}]
    )
    assert result["mismatches"] == [] and result["checked"] == 0


def test_wanted_asks_only_for_called_names_in_files_not_delivered_whole():
    files = [
        {
            "path": "app.py",
            "content": "from pkg.lib import f, g\nfrom pkg.mine import h\n"
            "f()\nh()\nprint(g)\n",
        },
        {"path": "pkg/mine.py", "content": "def h():\n    pass\n"},
    ]
    evidence = {
        "project_files": {"files": ["pkg/lib.py", "pkg/mine.py"], "truncated": False}
    }
    asked, capped = symbol_check.wanted(evidence, files)
    assert asked == [{"path": "pkg/lib.py", "names": ["f"]}] and capped is False


def test_wanted_keeps_within_the_daemons_bounds(monkeypatch):
    monkeypatch.setattr(symbol_check, "MAX_PATHS", 1)
    files = [
        {
            "path": "app.py",
            "content": "from pkg.a import f\nfrom pkg.b import g\nf()\ng()\n",
        }
    ]
    evidence = {"project_files": {"files": ["pkg/a.py", "pkg/b.py"]}}
    asked, capped = symbol_check.wanted(evidence, files)
    assert asked == [{"path": "pkg/a.py", "names": ["f"]}] and capped is True


def test_a_mismatch_is_a_finding_and_the_verdict_stays_verified(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = Company(company_name="Signatures Corp")
    sqlite_session.add(company)
    sqlite_session.commit()
    sqlite_session.refresh(company)
    artifact = "```python path=app.py\nfrom pkg.lib import f\n\nf(1, 2, 3)\n```\n"
    run = test_client.post(
        "/v1/agent_runs",
        json={
            "company_id": company.id,
            "external_ref": "task-signatures",
            "producer": "le-fabrique-task-worker",
            "artifact": artifact,
            "claims": {"summary": "delivered app.py"},
        },
    ).json()
    response = test_client.post(
        f"/v1/agent_runs/{run['id']}/verify",
        json={
            "company_id": company.id,
            "evidence": _evidence({"f": _sig(positional=["a"])}),
        },
    ).json()
    checks = {check["name"]: check for check in response["checks"]}
    grounded = checks["grounded"]
    assert grounded["outcome"] == "pass"
    assert grounded["signatures_observed"] is True
    assert grounded["signature_mismatches"][0]["problem"] == (
        "takes at most 1 positional, 3 given"
    )
    assert "contradict a signature" in grounded["observation"]
    assert response["verdict"] == "verified"


def test_without_an_observation_the_check_says_so():
    files = [{"path": "app.py", "content": "from pkg.lib import f\nf(1, 2, 3)\n"}]
    evidence = {"project_files": {"files": ["pkg/lib.py"], "truncated": False}}
    check = grounding.check_grounded(evidence, files)
    assert check["outcome"] == "pass" and check["signatures_observed"] is None
    assert check["signature_mismatches"] == []
    assert "not compared" in check["observation"]
    evidence["signatures"] = {"observed": False, "reason": "no daemon answers it"}
    check = grounding.check_grounded(evidence, files)
    assert check["signatures_observed"] is False
    assert "no daemon answers it" in check["observation"]


def test_a_local_mismatch_alone_still_makes_a_check():
    files = [{"path": "app.py", "content": "def f(a):\n    pass\n\nf()\n"}]
    check = grounding.check_grounded({}, files)
    assert check["outcome"] == "pass"
    assert check["signature_mismatches"][0]["problem"] == "missing 'a'"
    assert grounding.check_grounded({}, [{"path": "a.py", "content": "x = 1\n"}]) is (
        None
    )
