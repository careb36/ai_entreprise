"""The factory's quality standards (``services.standards``): a fixed catalogue
by discipline that binds every role's prompt, with the ones the loop checks
without a model: a secret in a delivered file refutes it, code without a
test, a TODO left behind and an unpinned dependency are findings; a
specification that says nothing about a quality area gets a warning;
``GET /v1/standards`` serves the catalogue and ``docs/STANDARDS.md`` lists it.
"""

import re
from pathlib import Path

import pytest
import worker_stages
from models.agent_run import AgentRun
from services import standards, verification
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
