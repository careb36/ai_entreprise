"""The factory builds itself (issue #329): La Fabrique's own design documents are
the rendering of its architecture record, the record round-trips through the
product's API unchanged, and the self-hosting harness's fixtures pass the
product's own checks (``scripts/self_host/``, ``services/planning/adr_docs.py``).
"""

import importlib.util
import json
from pathlib import Path

import pytest
from models import Company
from services.planning import adr_docs, arch_author, arch_check, spec_check

REPO = Path(__file__).resolve().parents[2]
SELF_HOST = REPO / "scripts" / "self_host"


def _script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_docs = _script(SELF_HOST / "render_docs.py", "self_host_render_docs")
self_host = _script(SELF_HOST / "self_host.py", "self_host_harness")
fake_llm = _script(REPO / "scripts" / "e2e" / "fake_llm.py", "self_host_fake_llm")


def _fixture(name: str) -> dict:
    return json.loads((SELF_HOST / "fixtures" / name).read_text(encoding="utf-8"))


# --- the documents and the record --------------------------------------------


def test_architecture_md_and_the_adrs_are_the_rendering_of_the_record():
    files = render_docs.rendered()
    assert "docs/ARCHITECTURE.md" in files
    assert sorted(p for p in files if p.startswith("docs/adr/")) == [
        "docs/adr/0012-dependency-rule.md",
        "docs/adr/0013-contracts-context.md",
        "docs/adr/0014-split-task-worker.md",
        "docs/adr/0015-parallel-trees.md",
        "docs/adr/0016-migration-order.md",
        "docs/adr/0017-http-adapters.md",
    ]
    # Edit docs/architecture/architecture.json, then
    # python3 scripts/self_host/render_docs.py --write
    assert render_docs.differences(files) == []


def test_a_stray_adr_file_is_reported(tmp_path, monkeypatch):
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0099-written-by-hand.md").write_text("# ADR 0099\n")
    (adr / render_docs.LEGACY_ADRS[0]).write_text("# superseded\n")
    monkeypatch.setattr(render_docs, "REPO", tmp_path)
    problems = render_docs.differences({"docs/adr/0012-x.md": "text\n"})
    assert problems == [
        "docs/adr/0012-x.md: missing",
        "docs/adr/0099-written-by-hand.md: not a decision of the record",
    ]


# --- the ADR renderer ----------------------------------------------------------


def _arch(decisions):
    return arch_check.normalize(
        {
            "title": "Shop",
            "modules": [{"name": "Cart", "responsibility": "Holds the items."}],
            "decisions": decisions,
        }
    )


def test_each_decision_is_one_adr_numbered_from_the_first_free_number():
    arch = _arch(
        [
            {
                "title": "Store carts in Postgres, not in the session",
                "context": "Carts outlive a session.",
                "decision": "One table per cart line.",
                "consequences": "A migration per change.",
                "date": "2026-11-01",
            },
            {
                "title": "No queue",
                "decision": "Write synchronously.",
                "slug": "no-queue",
            },
        ]
    )
    files = adr_docs.adr_documents(arch, "Shop spec", 3, first_number=7)
    assert [(f["id"], f["number"], f["path"]) for f in files] == [
        ("ADR-1", 7, "docs/adr/0007-store-carts-in-postgres-not-in.md"),
        ("ADR-2", 8, "docs/adr/0008-no-queue.md"),
    ]
    first, second = files[0]["markdown"], files[1]["markdown"]
    assert first.startswith("# ADR 0007: Store carts in Postgres, not in the session\n")
    assert "Accepted (2026-11-01). ADR-1 of architecture v3, *Shop*" in first
    assert "\n## Context\n\nCarts outlive a session.\n" in first
    assert first.endswith("## Consequences\n\nA migration per change.\n")
    # No date of its own and no approval: proposed; no context: no section.
    assert "Proposed. ADR-2" in second and "## Context" not in second
    approved = adr_docs.adr_documents(arch, "Shop spec", 3, approved_on="2026-11-02")
    assert "Accepted (2026-11-01)" in approved[0]["markdown"]
    assert "Accepted (2026-11-02)" in approved[1]["markdown"]


def test_only_a_well_formed_date_and_slug_are_kept_and_never_a_models_date():
    arch = _arch(
        [
            {"title": "A", "decision": "d", "date": "yesterday", "slug": "Not A Slug"},
            {"title": "B", "decision": "d", "date": "2026-11-03", "slug": "b-slug"},
        ]
    )
    assert [
        set(d) - {"id", "title", "context", "decision", "consequences"}
        for d in arch["decisions"]
    ] == [
        set(),
        {"date", "slug"},
    ]
    drafted = arch_author.parse_draft(
        json.dumps(
            {
                "title": "Shop",
                "modules": [{"name": "Cart", "responsibility": "Holds the items."}],
                "decisions": [
                    {"title": "B", "decision": "d", "date": "2026-11-03", "slug": "b"}
                ],
            }
        )
    )
    # When a decision was accepted is a person's record, not the Architect's.
    assert (
        "date" not in drafted["decisions"][0] and drafted["decisions"][0]["slug"] == "b"
    )


# --- the record through the API --------------------------------------------------


def test_la_fabriques_record_round_trips_through_the_api(sqlite_session, test_client):
    company = Company(company_name="La Fabrique")
    sqlite_session.add(company)
    sqlite_session.commit()
    spec_record = json.loads(
        (REPO / "docs/architecture/specification.json").read_text(encoding="utf-8")
    )
    arch_record = json.loads(
        (REPO / "docs/architecture/architecture.json").read_text(encoding="utf-8")
    )
    spec = test_client.post(
        "/v1/specifications",
        json={
            "company_id": company.id,
            **{
                k: spec_record[k]
                for k in ("title", "summary", "requirements", "quality", "constraints")
            },
        },
    ).json()
    assert (
        test_client.post(f"/v1/specifications/{spec['id']}/approve").json()["status"]
        == "approved"
    )
    created = test_client.post(
        "/v1/architectures",
        json={
            "specification_id": spec["id"],
            **{
                k: arch_record[k]
                for k in (
                    "title",
                    "overview",
                    "modules",
                    "interfaces",
                    "decisions",
                    "conventions",
                )
            },
        },
    )
    assert created.status_code == 201, created.text
    arch = test_client.post(f"/v1/architectures/{created.json()['id']}/approve").json()
    assert arch["status"] == "approved" and arch["check"]["blocking"] == 0

    document = test_client.get(f"/v1/architectures/{arch['id']}/document").text
    assert document == (REPO / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    first = arch_record["adr_first_number"]
    adrs = test_client.get(
        f"/v1/architectures/{arch['id']}/adrs?first_number={first}"
    ).json()
    assert adrs["files"][0]["path"] == "docs/adr/0012-dependency-rule.md"
    for adr in adrs["files"]:
        assert adr["markdown"] == (REPO / adr["path"]).read_text(encoding="utf-8"), adr[
            "path"
        ]
    assert (
        test_client.get(
            f"/v1/architectures/{arch['id']}/adrs?first_number=0"
        ).status_code
        == 422
    )

    # Issue #535's revision through the harness's own calls: the next version
    # of La Fabrique's one architecture, #469's covers kept (follow-up 3).
    api = _ClientApi(test_client)
    issue = self_host._approved_spec(
        api, company.id, _fixture("issue-535-specification.json")
    )
    change = _fixture("issue-535-architecture.json")
    revised = self_host._revised_version(api, company.id, issue["id"], change)
    assert (revised["status"], revised["version"]) == ("approved", 2)
    assert revised["specification_ids"] == [issue["id"], spec["id"]]
    assert revised["check"]["findings"] == []
    kept = {
        m["name"]: m["earlier_covers"][str(spec["id"])]
        for m in revised["modules"]
        if m.get("earlier_covers")
    }
    assert kept == {
        m["name"]: m["covers"] for m in arch_record["modules"] if m["covers"]
    }
    assert {m["name"]: m["covers"] for m in revised["modules"] if m["covers"]} == (
        change["covers"]
    )
    # Run again, the harness finds the version instead of opening another.
    assert (
        self_host._revised_version(api, company.id, issue["id"], change)["id"]
        == (revised["id"])
    )
    view = test_client.get(f"/v1/companies/{company.id}/architecture").json()
    assert [(v["version"], v["status"]) for v in view["versions"]] == [
        (2, "approved"),
        (1, "superseded"),
    ]
    # The superseded version still renders the repository's document.
    again = self_host._approved_architecture(api, spec["id"], arch_record)
    assert again["id"] == arch["id"]
    assert test_client.get(f"/v1/architectures/{arch['id']}/document").text == (
        (REPO / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    )


class _ClientApi:
    """The harness's ``Api`` over the in-process test client."""

    def __init__(self, client):
        self.client = client

    def call(self, method, path, body=None, raw=False):
        response = self.client.request(method, path, json=body)
        if response.status_code >= 400:
            raise self_host.ApiError(method, path, response.status_code, response.text)
        return response.text if raw else response.json()

    def get(self, path, raw=False):
        return self.call("GET", path, raw=raw)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def put(self, path, body):
        return self.call("PUT", path, body)


# --- the harness's fixtures ------------------------------------------------------


def test_the_issue_specification_and_the_revision_pass_the_products_checks():
    spec = spec_check.normalize(_fixture("issue-535-specification.json"))
    assert spec_check.check(spec)["findings"] == []
    criteria = [c["id"] for r in spec["requirements"] for c in r["criteria"]]
    assert criteria == ["REQ-1.1", "REQ-1.2", "REQ-2.1", "REQ-3.1", "REQ-3.2"]
    base = json.loads(
        (REPO / "docs/architecture/architecture.json").read_text(encoding="utf-8")
    )
    revised = arch_check.normalize(
        self_host.revised_architecture(base, _fixture("issue-535-architecture.json"))
    )
    result = arch_check.check(revised, [r["id"] for r in spec["requirements"]])
    assert result["findings"] == []
    covering = {m["name"]: m["covers"] for m in revised["modules"] if m["covers"]}
    assert covering == {"contracts": ["REQ-1"], "tasks": ["REQ-1", "REQ-2", "REQ-3"]}
    assert len(revised["decisions"]) == len(base["decisions"]) + 1
    assert "date" not in revised["decisions"][-1]  # accepted when a person approves it


def test_the_fake_models_plan_covers_every_criterion_of_the_issue():
    spec = _fixture("issue-535-specification.json")
    assert fake_llm.SELF_HOST_SPEC == spec["title"]
    covered = sorted(c for t in fake_llm.SELF_HOST_PLAN["tasks"] for c in t["covers"])
    assert covered == ["REQ-1.1", "REQ-1.2", "REQ-2.1", "REQ-3.1", "REQ-3.2"]
    titles = [r["title"] for r in spec["requirements"]]
    assert set(titles) <= set(fake_llm.ACCEPTANCE_MODULES)
    slugs = {fake_llm._slug(t["title"]) for t in fake_llm.SELF_HOST_PLAN["tasks"]}
    assert {fake_llm.ACCEPTANCE_MODULES[t] for t in titles} == slugs


def test_a_hung_git_command_fails_naming_the_command(tmp_path, monkeypatch):
    # PR #575: step 9d hung for 18 minutes with nothing to diagnose; a local git
    # call must now give up after GIT_TIMEOUT_SECONDS and say which one hung.
    seen = {}

    def hung(cmd, **kwargs):
        seen.update(kwargs)
        raise self_host.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(self_host.subprocess, "run", hung)
    with pytest.raises(self_host.GitTimeoutError) as raised:
        self_host._git(tmp_path, "ls-files", "-z")
    assert seen["timeout"] == self_host.GIT_TIMEOUT_SECONDS
    message = str(raised.value)
    assert "git ls-files -z" in message
    assert str(self_host.GIT_TIMEOUT_SECONDS) in message
    assert "user.email" not in message  # the identity flags are not the command
