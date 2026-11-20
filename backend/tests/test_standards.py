"""The factory's quality standards (``services.evidence.standards``): a fixed catalogue
by discipline that binds every role's prompt, with the ones the loop checks
without a model: a secret in a delivered file refutes it, code without a
test, a TODO left behind and an unpinned dependency are findings; a
specification that says nothing about a quality area gets a warning;
``GET /v1/standards`` serves the catalogue and ``docs/STANDARDS.md`` lists it.

A company's own standards (issue #700, ``services.evidence.company_standards_check``)
are judged at the end: each kind a pure function of observations; written and
added paths, declared paths (which add to a match and never satisfy one),
the bytes read back and the baselines read at the activated commit. What
cannot be judged is ``unverifiable``, never ``pass``; nothing a producer
claims is read.
"""

import re
from pathlib import Path

import pytest
import worker_stages
from models.agent_run import AgentRun
from services.contracts import company_regex
from services.contracts import company_standards as cs
from services.evidence import company_standards_check as check
from services.evidence import standards, verification
from services.evidence.company_standards_check import Observation
from services.loop.worker import record as worker_record

DOC = Path(__file__).resolve().parents[2] / "docs" / "STANDARDS.md"


def test_the_catalogue_is_sound_and_every_role_is_bound():
    ids = [s["id"] for s in standards.STANDARDS]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"STD-[A-Z]+-\d+", i) for i in ids)
    assert all(s["area"] in standards.AREAS for s in standards.STANDARDS)
    assert all(s["title"] and s["rule"] and s["roles"] for s in standards.STANDARDS)
    for role in ("PO", "Architect", "Developer", "QA", "ScrumMaster", "TechWriter"):
        bound = {s["id"] for s in standards.for_role(role)}
        assert {"STD-SEC-1", "STD-AUD-1"} <= bound
    assert "STD-DEV-1" in {s["id"] for s in standards.for_role("Developer")}
    assert "STD-DEV-1" not in {s["id"] for s in standards.for_role("PO")}
    assert standards.catalogue()[0] is not standards.STANDARDS[0]


def test_the_prompt_section_names_the_rules_and_what_is_checked():
    section = standards.prompt_section("Developer")
    assert section.startswith("\n\n## Quality standards")
    assert "- STD-SEC-1 No secret in the repository, ever [checked]:" in section
    assert "- STD-DEV-3 Small, named, typed:" in section
    assert "STD-PO-1" not in section
    assert "STD-PO-1" in standards.prompt_section("PO")
    assert "never relaxes them" in standards.prompt_section(None)


def test_the_quality_areas_a_specification_forgets():
    spec = {
        "quality": [
            {"attribute": "performance", "target": "p95 under 200 ms"},
            {"attribute": "security", "target": "OWASP ASVS level 2"},
        ]
    }
    assert standards.missing_quality_areas(spec) == ["reliability", "accessibility"]
    assert standards.missing_quality_areas({"quality": []}) == [
        "security",
        "performance",
        "reliability",
        "accessibility",
    ]
    assert (
        standards.missing_quality_areas(
            {
                "quality": [
                    {"attribute": "Availability", "target": "99.9 %"},
                    {"attribute": "a11y", "target": "WCAG 2.2 AA"},
                    {"attribute": "auth", "target": "MFA"},
                    {"attribute": "latency", "target": "100 ms"},
                ]
            }
        )
        == []
    )


@pytest.mark.parametrize(
    ("path", "content", "what"),
    [
        (
            "src/cfg.py",
            'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz1234"\n',
            "an OpenAI-style key",
        ),
        ("deploy/key.pem", "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n", "a private key"),
        ("src/aws.py", "key = 'AKIAABCDEFGHIJKLMNOP'\n", "an AWS access key"),
        (
            "src/db.py",
            "password = 'hunter2hunter2'\n",
            "a password or secret assigned in code",
        ),
        ("src/gh.py", "token = 'ghp_" + "a" * 36 + "'\n", "a GitHub token"),
    ],
)
def test_a_secret_in_a_delivered_file_is_found(path, content, what):
    found = standards.secrets_in(path, content)
    assert found and found[0].startswith(what) and found[0].endswith(f"{path}:1")


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("src/cfg.py", 'API_KEY = os.environ["API_KEY"]\n'),
        ("src/cfg.py", 'password = "<your-password>"\n'),
        ("src/cfg.py", 'secret = "changeme-please"\n'),
        ("src/cfg.py", 'password = "${DB_PASSWORD}"\n'),
        ("README.example.md", "token = 'ghp_" + "a" * 36 + "'\n"),
        ("src/cfg.py", 'password_field = "password"\n'),
    ],
)
def test_a_placeholder_or_a_reference_is_not_a_secret(path, content):
    assert standards.secrets_in(path, content) == []


def test_todos_and_unpinned_dependencies_are_findings_but_identifiers_are_not():
    assert standards.todos_in("a.py", "x = 1  # TODO: later\n# FIXME\nok\n") == [
        "a.py:1",
        "a.py:2",
    ]
    assert (
        standards.todos_in("a.py", "todo_list = []\nstatus = 'todo'\nTODO_LIMIT = 3\n")
        == []
    )
    assert standards.unpinned_in(
        "requirements.txt",
        "fastapi==0.1\nrequests\n# c\n-r base.txt\nhttpx[http2]>=0.2\nnumpy\n",
    ) == [
        "requests",
        "numpy",
    ]
    assert standards.unpinned_in(
        "package.json",
        '{"dependencies": {"react": "18.3.1", "left-pad": "*", "lodash": "latest", "x": ">=1"}, "devDependencies": {"vitest": "^3.2.7"}}',
    ) == ["left-pad@*", "lodash@latest", "x@>=1"]
    assert standards.unpinned_in("package.json", "{not json") == []
    assert standards.unpinned_in("src/app.py", "requests\n") == []


def test_the_check_over_a_delivery():
    clean = standards.check_files(
        [
            {"path": "src/greet.py", "content": "def greet():\n    return 'hi'\n"},
            {"path": "tests/test_greet.py", "content": "def test_greet():\n    pass\n"},
        ]
    )
    assert clean == {
        "name": "standards",
        "outcome": "pass",
        "observation": "2 file(s) checked, nothing found",
        "findings": [],
    }
    warned = standards.check_files(
        [
            {"path": "src/greet.py", "content": "def greet():\n    pass  # TODO\n"},
            {"path": "requirements.txt", "content": "requests\n"},
        ]
    )
    assert warned["outcome"] == "pass"
    assert [f["standard"] for f in warned["findings"]] == [
        "STD-DEV-1",
        "STD-DEV-2",
        "STD-SEC-3",
    ]
    assert warned["observation"] == (
        "STD-DEV-1: code delivered without a test: src/greet.py; "
        "STD-DEV-2: left behind at src/greet.py:2; STD-SEC-3: unpinned: requests"
    )
    refuted = standards.check_files(
        [
            {
                "path": "src/cfg.py",
                "content": 'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz1234"\n',
            }
        ]
    )
    assert refuted["outcome"] == "refuted"
    assert refuted["observation"] == "STD-SEC-1: an OpenAI-style key at src/cfg.py:1"
    assert refuted["findings"][0]["severity"] == "blocking"
    assert standards.check_delivery("no files here") is None
    assert (
        standards.check_delivery("```python path=a.py\nx = 1\n```")["outcome"] == "pass"
    )


def test_the_verifier_records_the_check_and_a_secret_refutes_the_run(
    sqlite_session, monkeypatch, tmp_path
):
    from models import Company

    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit"))
    company = Company(company_name="Std Corp")
    sqlite_session.add(company)
    sqlite_session.commit()
    leaked = '```python path=src/cfg.py\nAPI_KEY = "sk-abcdefghijklmnopqrstuvwxyz1234"\n```\n'
    run = AgentRun(
        company_id=company.id,
        producer="le-fabrique-task-worker",
        external_ref="task-1",
        version=1,
        status="pending",
        artifact_text=leaked,
        artifact_digest="x",
        artifact_size=len(leaked),
        claims={"files": ["src/cfg.py"]},
    )
    sqlite_session.add(run)
    sqlite_session.commit()
    result = verification.verify_agent_run(
        sqlite_session,
        run,
        {
            "workspace_writes": [
                {"path": "src/cfg.py", "status": "written", "sha256": "x"}
            ]
        },
    )
    by_name = {c["name"]: c for c in result.checks}
    assert by_name["standards"]["outcome"] == "refuted"
    assert result.verdict == "refuted"
    worker_stages.use_engine(monkeypatch, sqlite_session.get_bind())
    sqlite_session.commit()
    problem, observation = worker_record._delivery_gate(company.id, 1)
    assert problem == "standards violated" and "STD-SEC-1" in observation


def test_the_catalogue_is_served_and_documented(test_client):
    served = test_client.get("/v1/standards")
    assert served.status_code == 200
    assert [s["id"] for s in served.json()["standards"]] == [
        s["id"] for s in standards.STANDARDS
    ]
    assert served.json()["areas"] == list(standards.AREAS)
    doc = DOC.read_text(encoding="utf-8")
    for s in standards.STANDARDS:
        assert f"**{s['id']}**" in doc, f"{s['id']} is not in docs/STANDARDS.md"
        assert s["title"] in doc


# -- A company's own standards (issue #700) ------------------------------------


def _std(kind: str, **fields) -> dict:
    return {
        "id": "ACME-X-1",
        "kind": kind,
        "severity": "blocking",
        "paths": [],
        **fields,
    }


def _one(standard, obs, commands=None):
    return check.evaluate_one(standard, obs, commands=commands)


# -- forbid_pattern -------------------------------------------------------------


PATTERN = _std(
    "forbid_pattern",
    pattern=r"StatusPill",
    files=["src/pages/**/*.tsx"],
    **{"except": ["**/*.test.tsx"]},
)


def test_a_forbidden_pattern_fails_citing_file_and_line():
    obs = Observation(
        written=["src/pages/A.tsx", "src/pages/A.test.tsx"],
        files={
            "src/pages/A.tsx": b"import x\nimport { StatusPill }\n",
            "src/pages/A.test.tsx": b"StatusPill",
        },
    )
    entry = _one(PATTERN, obs)
    assert entry["outcome"] == "fail"
    assert entry["observation"]["matches"] == [{"path": "src/pages/A.tsx", "line": 2}]


def test_a_file_not_read_back_is_unverifiable_never_a_pass():
    obs = Observation(written=["src/pages/A.tsx"], files={"src/pages/A.tsx": None})
    entry = _one(PATTERN, obs)
    assert (
        entry["outcome"] == "unverifiable"
        and entry["observation"]["reason"] == "unread"
    )


def test_the_byte_budget_spent_is_unverifiable(monkeypatch):
    monkeypatch.setattr(check, "STANDARD_BUDGET", 10)
    obs = Observation(written=["src/pages/A.tsx"], files={"src/pages/A.tsx": b"x" * 11})
    assert _one(PATTERN, obs)["observation"]["reason"] == "budget"


def test_without_re2_a_pattern_is_unverifiable(monkeypatch):
    monkeypatch.setattr(company_regex, "_re2", None)
    obs = Observation(written=["src/pages/A.tsx"], files={"src/pages/A.tsx": b"ok"})
    assert _one(PATTERN, obs)["observation"]["reason"] == company_regex.UNAVAILABLE


def test_a_backtracking_pattern_finishes_on_a_hostile_file():
    hostile = _std("forbid_pattern", pattern="(a+)+$", files=["**"])
    obs = Observation(written=["x.txt"], files={"x.txt": b"a" * 200_000 + b"!"})
    assert _one(hostile, obs)["outcome"] == "pass"


# -- require_together -----------------------------------------------------------


I18N = _std(
    "require_together", when=["i18n/en.json"], then=["i18n/es.json"], on="changed"
)


def test_require_together_fails_without_the_companion():
    entry = _one(I18N, Observation(written=["i18n/en.json"]))
    assert entry["outcome"] == "fail" and entry["observation"]["when"] == [
        "i18n/en.json"
    ]


def test_require_together_passes_with_both_observed():
    assert (
        _one(I18N, Observation(written=["i18n/en.json", "i18n/es.json"]))["outcome"]
        == "pass"
    )


def test_a_declared_path_adds_to_when_and_never_satisfies_then():
    obs = Observation(written=[], declared=["i18n/en.json", "i18n/es.json"])
    assert _one(I18N, obs)["outcome"] == "fail"


def test_on_added_counts_only_added_paths():
    doc = _std(
        "require_together",
        when=["tests/test_*.py"],
        then=["docs/TESTING.md"],
        on="added",
    )
    assert _one(doc, Observation(written=["tests/test_a.py"]))["outcome"] == "pass"
    added = Observation(written=["tests/test_a.py"], added=["tests/test_a.py"])
    assert _one(doc, added)["outcome"] == "fail"


# -- max_lines ------------------------------------------------------------------


SIZE = _std("max_lines", limit=3, files=["**/*.py"], baseline="sizes.json")


def test_a_file_over_the_limit_fails():
    obs = Observation(
        written=["a.py"], files={"a.py": b"1\n2\n3\n4"}, baselines={"sizes.json": {}}
    )
    entry = _one(SIZE, obs)
    assert entry["outcome"] == "fail" and entry["observation"]["over"][0]["lines"] == 4


def test_the_baseline_allows_a_file_that_did_not_grow():
    obs = Observation(
        written=["a.py"],
        files={"a.py": b"1\n2\n3\n4\n"},
        baselines={"sizes.json": {"a.py": 4}},
    )
    assert _one(SIZE, obs)["outcome"] == "pass"
    grown = Observation(
        written=["a.py"],
        files={"a.py": b"1\n2\n3\n4\n5\n"},
        baselines={"sizes.json": {"a.py": 4}},
    )
    assert _one(SIZE, grown)["outcome"] == "fail"


def test_an_unread_baseline_is_unverifiable():
    obs = Observation(
        written=["a.py"],
        files={"a.py": b"1\n2\n3\n4\n"},
        baselines={"sizes.json": None},
    )
    assert _one(SIZE, obs)["outcome"] == "unverifiable"


@pytest.mark.parametrize(
    "data,lines", [(b"", 0), (b"a", 1), (b"a\n", 1), (b"a\nb", 2), (b"\n\n", 2)]
)
def test_lines_are_counted_as_a_reader_counts_them(data, lines):
    assert check.lines_of(data) == lines


# -- forbid_path ----------------------------------------------------------------


ENV = _std(
    "forbid_path",
    files=[".env", "**/.env", "**/.env.*"],
    **{"except": ["**/.env.example"]},
)


def test_a_forbidden_path_written_or_declared_fails():
    assert _one(ENV, Observation(written=["app/.env"]))["outcome"] == "fail"
    assert _one(ENV, Observation(declared=[".env"]))["outcome"] == "fail"
    assert _one(ENV, Observation(written=["app/.env.example"]))["outcome"] == "pass"


def test_spelling_does_not_hide_a_forbidden_path():
    assert _one(ENV, Observation(written=["app\\.env"]))["outcome"] == "fail"
    assert _one(ENV, Observation(written=["./app/../.ENV"]))["outcome"] == "fail"


# -- paths, commands, the order -------------------------------------------------


def test_a_standard_whose_paths_are_not_touched_is_skipped():
    scoped = {**ENV, "paths": ["backend/**"]}
    assert _one(scoped, Observation(written=["frontend/.env"]))["outcome"] == "skipped"
    assert (
        _one(scoped, Observation(written=["frontend/.env"], declared=["backend/x"]))[
            "outcome"
        ]
        == "fail"
    )


def _run(**fields):
    return {
        "exit_code": 0,
        "sandbox": {"mode": "container"},
        "output_sha256": "a" * 64,
        **fields,
    }


@pytest.mark.parametrize(
    "run,outcome,reason",
    [
        (None, "not_run", "run_by_the_loop_at_delivery"),
        (_run(), "pass", None),
        (_run(exit_code=1), "fail", None),
        (_run(exit_code=127), "unverifiable", "not_runnable"),
        (_run(exit_code=None), "unverifiable", "no_exit_status"),
        (_run(timed_out=True), "unverifiable", "no_exit_status"),
        (_run(refused=True), "unverifiable", "no_exit_status"),
        (_run(sandbox={"mode": "host"}), "unverifiable", "host_sandbox"),
    ],
)
def test_a_command_is_judged_by_its_observed_run_only(run, outcome, reason):
    command = _std(
        "command", command="make lint", guards=[{"path": "Makefile", "exclude": []}]
    )
    entry = _one(
        command, Observation(written=["a.py"]), {"ACME-X-1": run} if run else None
    )
    assert entry["outcome"] == outcome
    assert entry["observation"].get("reason") == reason


def test_no_claim_field_is_read():
    """T9: a producer's summary saying the standard passed changes nothing."""
    obs = Observation(written=["app/.env"])
    obs.claims = {"ACME-X-1": "pass"}  # type: ignore[attr-defined]
    assert _one(ENV, obs)["outcome"] == "fail"


def test_the_dry_run_names_what_fails_on_the_source_tree():
    tree = ["app/.env", "src/pages/A.tsx", "i18n/en.json"]
    standards = [
        ENV,
        {**PATTERN, "id": "ACME-UI-1", "severity": "finding"},
        {**I18N, "id": "ACME-I18N-1"},
    ]
    assert check.dry_run_targets(standards, tree) == ["src/pages/A.tsx"]
    obs = Observation(
        written=tree, added=tree, files={"src/pages/A.tsx": b"StatusPill"}
    )
    summary = check.dry_run_summary(check.evaluate(standards, obs))
    assert summary["fails_on_source"] == ["ACME-X-1", "ACME-UI-1", "ACME-I18N-1"]
    assert summary["blocking_fails_on_source"] == ["ACME-X-1", "ACME-I18N-1"]


def test_the_check_name_and_kinds_are_the_closed_list():
    assert check.CHECK_NAME == "company_standards"
    assert set(cs.IN_BACKEND_KINDS) | {"command"} == set(cs.KINDS)
