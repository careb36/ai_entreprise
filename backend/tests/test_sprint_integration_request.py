"""The request a sprint's integration publishes (issue #314).

It is built from the runs and the acceptance line recorded at integration,
never later ones; an unchanged rerun still describes every task, a diff the
producer sent is only a claim, and a row without its tasks' evidence is not
published.

These tests run real git in ``tmp_path``, with a bare repository standing in
for the remote; the shared fakes and rows are in ``sprint_integration_helpers``.
"""

import sys
from pathlib import Path

from models import BacklogItem, Sprint
from models.integration import SprintIntegration
from services.release import integration

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vscode-daemon"))
import sprint_integration_helpers
from sprint_integration_helpers import (
    _Daemon,
    _published_body,
    _run,
    _two_done_tasks,
    observed_part,
)
from test_git_workflow import _company

# The shared fixtures.
published_repo = sprint_integration_helpers.published_repo
suite = sprint_integration_helpers.suite


def test_the_request_shows_the_runs_recorded_at_integration_not_later_ones(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, first, *_ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()
    recorded = row["tasks"][0]
    assert recorded["run_id"] is not None and recorded["verification_id"] is not None
    later = _run(
        sqlite_session, company, first, "LATER CLAIM", ["z.py"], "refuted", version=2
    )

    text = test_client.post(
        f"/v1/sprints/{sprint.id}/integration/publish"
    ).json() and _published_body(test_client)

    assert (
        f"Run {recorded['run_id']}, verification {recorded['verification_id']}" in text
    )
    assert f"Run {later.id}" not in text
    assert "LATER CLAIM" not in text and "z.py" not in text


DIFF = """diff --git a/c.py b/c.py
new file mode 100644
--- /dev/null
+++ b/c.py
@@ -0,0 +1 @@
+c = 1
"""


def test_a_diff_the_producer_sent_is_a_claim_never_observed(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    company, sprint, _, second, _ = _two_done_tasks(sqlite_session, root)
    # The second task's latest run: no files read back, only a diff it sent.
    _run(sqlite_session, company, second, "Sent a diff", None, artifact=DIFF, version=2)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    assert test_client.post(f"/v1/sprints/{sprint.id}/integrate").status_code == 201

    test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    text = _published_body(test_client)

    observed, claimed = text.split("## Claimed by the producers (not observed)")
    assert "c.py" not in observed
    assert f"Task {second.id}, files in the diff it sent: `c.py`" in claimed


def test_an_unchanged_rerun_still_describes_every_task(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, first, second, _ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    test_client.post(f"/v1/sprints/{sprint.id}/integrate")

    again = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()

    assert again["merged"] == []
    assert {t["state"] for t in again["tasks"]} == {"already_merged"}
    test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    text = _published_body(test_client)
    assert f"### Task {first.id}: Constants" in text
    assert f"### Task {second.id}: Use them" in text
    assert "already in" in text


def test_the_acceptance_coverage_is_read_once_per_specification(
    sqlite_session, monkeypatch
):
    from models.specification import Specification
    from services.planning import spec_planning

    company = _company(sqlite_session)
    spec = Specification(company_id=company.id, title="Auth", status="approved")
    sqlite_session.add(spec)
    sqlite_session.commit()
    sprint = Sprint(company_id=company.id, sprint_name="S", specification_id=spec.id)
    sqlite_session.add(sprint)
    sqlite_session.commit()
    items = [
        BacklogItem(sprint_id=sprint.id, title=f"T{i}", covers=[f"REQ-1.{i}"])
        for i in (1, 2, 3)
    ]
    sqlite_session.add_all(items)
    sqlite_session.commit()
    calls = []

    def coverage(session, spec):
        calls.append(spec.id)
        return {
            "total": 3,
            "verified": 1,
            "criteria": [
                {"id": "REQ-1.1", "verified": True},
                {"id": "REQ-1.2", "verified": False},
                {"id": "REQ-1.3", "verified": False},
            ],
        }

    monkeypatch.setattr(spec_planning, "coverage", coverage)

    lines = spec_planning.acceptance_summaries(sqlite_session, items)

    assert calls == [spec.id]
    assert lines[items[0].id].endswith("this task's: 1 of 1.")
    assert lines[items[1].id].endswith("this task's: 0 of 1.")
    assert '1 of 3 criteria of "Auth"' in lines[items[2].id]
    assert (
        spec_planning.acceptance_summary(sqlite_session, items[0]) == lines[items[0].id]
    )


def test_an_integration_recorded_without_its_tasks_evidence_is_not_published(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    row = test_client.post(f"/v1/sprints/{sprint.id}/integrate").json()
    # As a row from before migration 0012 reads.
    legacy = sqlite_session.get(SprintIntegration, row["id"])
    sqlite_session.refresh(legacy)
    legacy.tasks = None
    sqlite_session.add(legacy)
    sqlite_session.commit()

    refused = test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")

    assert refused.status_code == 409
    assert "integrate again" in refused.json()["detail"]


def test_the_acceptance_line_is_the_one_seen_at_integration(
    sqlite_session, test_client, published_repo, suite, monkeypatch
):
    root, _, _ = published_repo
    _, sprint, *_ = _two_done_tasks(sqlite_session, root)
    monkeypatch.setattr(test_client.app.state, "nats_client", _Daemon())
    monkeypatch.setattr(
        integration,
        "acceptance_summaries",
        lambda s, items: {i.id: "**Acceptance:** AT INTEGRATION" for i in items},
    )
    assert test_client.post(f"/v1/sprints/{sprint.id}/integrate").status_code == 201
    # A later run changes the specification's coverage; the integration does not.
    monkeypatch.setattr(
        integration,
        "acceptance_summaries",
        lambda s, items: {i.id: "**Acceptance:** LATER" for i in items},
    )

    test_client.post(f"/v1/sprints/{sprint.id}/integration/publish")
    text = _published_body(test_client)

    assert "**Acceptance:** AT INTEGRATION" in observed_part(text)
    assert "LATER" not in text
