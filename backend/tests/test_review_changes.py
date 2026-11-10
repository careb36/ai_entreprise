"""
What a run changed, shown the way a code review shows it.

The workspace daemon reports, per file it writes, the diff between the bytes
that were there and the bytes it read back (``vscode-daemon/file_diff.py``);
``GET /v1/agent_runs/{id}/changes`` reads that observation, or the diff
fetched for an ingested pull request, never the producer's claims.
"""

import hashlib
import importlib.util
import secrets
from pathlib import Path

import auth_tokens
import file_diff
import pytest
from config import settings
from models import Company, CompanyMember, User
from models.agent_run import AgentRun, Verification
from services.evidence.review_changes import parse_unified_diff

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location(
        "fabrique_daemon_review", _DAEMON_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon()


# --- the daemon observes what each write changed ------------------------------


def test_a_new_file_is_added_with_every_line(tmp_path):
    [result] = daemon.write_files(
        str(tmp_path), [{"path": "src/app.py", "content": "a = 1\nb = 2\n"}]
    )

    assert result["status"] == "written"
    assert result["change"] == "added"
    assert result["previous_sha256"] is None
    assert (result["additions"], result["deletions"]) == (2, 0)
    assert result["diff"].startswith(
        "diff --git a/src/app.py b/src/app.py\nnew file mode 100644\n"
    )
    assert (
        "--- /dev/null\n+++ b/src/app.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n"
        in result["diff"]
    )


def test_an_overwrite_is_the_diff_against_what_was_on_disk(tmp_path):
    (tmp_path / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    before = hashlib.sha256((tmp_path / "app.py").read_bytes()).hexdigest()

    [result] = daemon.write_files(
        str(tmp_path), [{"path": "app.py", "content": "a = 1\nb = 20\nc = 3\n"}]
    )

    assert result["change"] == "modified"
    assert result["previous_sha256"] == before
    assert (result["additions"], result["deletions"]) == (1, 1)
    assert "-b = 2\n+b = 20\n" in result["diff"]
    assert "--- a/app.py\n+++ b/app.py\n" in result["diff"]


def test_rewriting_the_same_bytes_is_unchanged_with_no_diff(tmp_path):
    (tmp_path / "same.txt").write_text("hello\n", encoding="utf-8")

    [result] = daemon.write_files(
        str(tmp_path), [{"path": "same.txt", "content": "hello\n"}]
    )

    assert result["change"] == "unchanged"
    assert result["diff"] is None
    assert (result["additions"], result["deletions"]) == (0, 0)


def test_binary_and_oversized_files_are_reported_without_a_diff(tmp_path, monkeypatch):
    [binary] = daemon.write_files(
        str(tmp_path), [{"path": "blob.bin", "content": "a\0b"}]
    )
    assert (binary["change"], binary["diff"], binary["diff_omitted"]) == (
        "added",
        None,
        "binary",
    )

    monkeypatch.setattr(file_diff, "MAX_DIFF_SOURCE_BYTES", 10)
    [big] = daemon.write_files(
        str(tmp_path), [{"path": "big.txt", "content": "x" * 50}]
    )
    assert (big["change"], big["diff"], big["diff_omitted"]) == (
        "added",
        None,
        "too_large",
    )


def test_a_large_file_overwritten_is_hashed_not_read_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(file_diff, "MAX_DIFF_SOURCE_BYTES", 16)
    old = b"line\n" * 100
    (tmp_path / "log.txt").write_bytes(old)

    [result] = daemon.write_files(
        str(tmp_path), [{"path": "log.txt", "content": "short\n"}]
    )

    assert result["change"] == "modified"
    assert result["previous_sha256"] == hashlib.sha256(old).hexdigest()
    assert (result["diff"], result["diff_omitted"]) == (None, "too_large")


def test_one_reply_keeps_its_diffs_under_the_cap_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(file_diff, "MAX_REPLY_DIFF_CHARS", 150)
    results = daemon.write_files(
        str(tmp_path),
        [
            {"path": "one.txt", "content": "1\n"},
            {"path": "two.txt", "content": "2\n" * 40},
        ],
    )

    assert results[0]["diff"] is not None
    assert results[1]["diff"] is None
    assert results[1]["diff_omitted"] == "reply_size"
    # The counts stay: the review still says how much changed.
    assert results[1]["additions"] == 40


# --- one parser for the daemon's diffs and a pull request's --------------------

PULL_REQUEST_DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,3 @@
 a = 1
-b = 2
+b = 20
 c = 3
diff --git a/docs/new.md b/docs/new.md
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/docs/new.md
@@ -0,0 +1,2 @@
+# New
+text
diff --git a/old.txt b/old.txt
deleted file mode 100644
index 4444444..0000000
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
diff --git a/before.py b/after.py
similarity index 90%
rename from before.py
rename to after.py
diff --git a/logo.png b/logo.png
index 5555555..6666666 100644
Binary files a/logo.png and b/logo.png differ
"""


def test_a_pull_request_diff_is_read_file_by_file():
    files = parse_unified_diff(PULL_REQUEST_DIFF)

    assert [(f["path"], f["change"]) for f in files] == [
        ("src/app.py", "modified"),
        ("docs/new.md", "added"),
        ("old.txt", "deleted"),
        ("after.py", "renamed"),
        ("logo.png", "modified"),
    ]
    assert (files[0]["additions"], files[0]["deletions"]) == (1, 1)
    assert files[0]["patch"].startswith("@@ -1,3 +1,3 @@\n a = 1\n-b = 2")
    assert (files[1]["additions"], files[2]["deletions"]) == (2, 1)
    assert files[3]["old_path"] == "before.py"
    assert files[3]["patch"] is None
    assert files[4]["omitted"] == "binary"


def test_nothing_to_read_is_no_files():
    assert parse_unified_diff("") == []
    assert parse_unified_diff(None) == []
    assert parse_unified_diff("not a diff at all") == []


# --- GET /v1/agent_runs/{id}/changes -------------------------------------------


@pytest.fixture(autouse=True)
def enforcement(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    key = secrets.token_urlsafe(48)
    monkeypatch.setattr(auth_tokens, "_configured_key", lambda: key)


def _member(session, company_id, role):
    user = User(
        email=f"{role.lower()}-{secrets.token_hex(6)}@example.com",
        full_name=role,
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    if company_id is not None:
        session.add(
            CompanyMember(company_id=company_id, user_id=user.id, human_role=role)
        )
        session.commit()
    token = auth_tokens.create_access_token(user_id=user.id, email=user.email)
    return {"Authorization": f"Bearer {token}"}


def _company(session):
    company = Company(company_name=f"Review {secrets.token_hex(3)}")
    session.add(company)
    session.commit()
    return company.id


def _run(session, company_id, **fields):
    artifact = fields.pop("artifact_text", "the answer")
    run = AgentRun(
        company_id=company_id,
        external_ref=fields.pop("external_ref", f"task-{secrets.token_hex(3)}"),
        producer=fields.pop("producer", "le-fabrique-task-worker"),
        artifact_text=artifact,
        artifact_digest=hashlib.sha256(artifact.encode()).hexdigest(),
        artifact_size=len(artifact),
        **fields,
    )
    session.add(run)
    session.commit()
    return run


def _changes(client, company_id, run_id, headers):
    return client.get(
        f"/v1/agent_runs/{run_id}/changes",
        params={"company_id": company_id},
        headers=headers,
    )


def test_a_task_run_shows_what_the_daemon_observed(
    sqlite_session, test_client, tmp_path
):
    company_id = _company(sqlite_session)
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    writes = daemon.write_files(
        str(tmp_path),
        [
            {"path": "app.py", "content": "a = 2\n"},
            {"path": "tests/test_app.py", "content": "def test():\n    assert True\n"},
            {"path": "../outside.txt", "content": "no"},
        ],
    )
    run = _run(
        sqlite_session,
        company_id,
        claims={
            "files": ["app.py", "tests/test_app.py", "../outside.txt", "README.md"]
        },
    )
    verification = Verification(
        company_id=company_id,
        agent_run_id=run.id,
        verdict="refuted",
        checks=[],
        evidence={"workspace_writes": writes},
    )
    sqlite_session.add(verification)
    sqlite_session.commit()

    response = _changes(
        test_client, company_id, run.id, _member(sqlite_session, company_id, "Viewer")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "workspace"
    assert body["observed_by"]["verification_id"] == verification.id
    assert [(f["path"], f["change"]) for f in body["files"]] == [
        ("app.py", "modified"),
        ("tests/test_app.py", "added"),
        ("../outside.txt", "not_written"),
    ]
    assert body["files"][0]["patch"] == "@@ -1 +1 @@\n-a = 1\n+a = 2"
    assert body["files"][2]["status"] == "rejected"
    assert body["totals"] == {"files": 3, "additions": 3, "deletions": 1}
    # Declared, but no observation covers it: kept apart, never shown as a change.
    assert body["declared_not_observed"] == ["README.md"]


def test_a_delivery_from_before_diffs_says_its_change_was_not_recorded(
    sqlite_session, test_client
):
    company_id = _company(sqlite_session)
    run = _run(sqlite_session, company_id, claims={"files": ["app.py"]})
    sqlite_session.add(
        Verification(
            company_id=company_id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[],
            evidence={
                "workspace_writes": [
                    {"path": "app.py", "status": "written", "sha256": "ab"}
                ]
            },
        )
    )
    sqlite_session.commit()

    body = _changes(
        test_client, company_id, run.id, _member(sqlite_session, company_id, "Viewer")
    ).json()

    assert body["files"] == [
        {
            "path": "app.py",
            "old_path": None,
            "change": None,
            "additions": 0,
            "deletions": 0,
            "patch": None,
            "omitted": "not_recorded",
        }
    ]
    assert body["declared_not_observed"] == []


def test_a_run_with_no_report_says_so(sqlite_session, test_client):
    company_id = _company(sqlite_session)
    run = _run(sqlite_session, company_id, claims={"files": ["app.py"]})

    body = _changes(
        test_client, company_id, run.id, _member(sqlite_session, company_id, "Viewer")
    ).json()

    assert body["source"] is None
    assert body["reason"] == "no report from the workspace daemon"
    assert body["files"] == []
    assert body["declared_not_observed"] == ["app.py"]


def test_a_pull_request_run_shows_its_diff(sqlite_session, test_client):
    company_id = _company(sqlite_session)
    run = _run(
        sqlite_session,
        company_id,
        producer="copilot",
        external_ref="github:acme/app#7",
        artifact_text=PULL_REQUEST_DIFF,
        subject={"pr_url": "https://github.com/acme/app/pull/7"},
        claims={"title": "Change b"},
    )

    body = _changes(
        test_client, company_id, run.id, _member(sqlite_session, company_id, "Viewer")
    ).json()

    assert body["source"] == "pull_request"
    assert body["observed_by"] is None
    assert body["totals"] == {"files": 5, "additions": 3, "deletions": 2}


def test_a_diff_sent_as_the_artifact_is_labelled_as_the_producer_s(
    sqlite_session, test_client
):
    company_id = _company(sqlite_session)
    run = _run(
        sqlite_session,
        company_id,
        producer="claude-code",
        artifact_text=PULL_REQUEST_DIFF,
    )

    body = _changes(
        test_client, company_id, run.id, _member(sqlite_session, company_id, "Viewer")
    ).json()

    assert body["source"] == "artifact"
    assert body["totals"]["files"] == 5


def test_the_daemon_s_observation_wins_over_a_diff_in_the_answer(
    sqlite_session, test_client, tmp_path
):
    company_id = _company(sqlite_session)
    writes = daemon.write_files(str(tmp_path), [{"path": "a.py", "content": "x\n"}])
    run = _run(sqlite_session, company_id, artifact_text=PULL_REQUEST_DIFF)
    sqlite_session.add(
        Verification(
            company_id=company_id,
            agent_run_id=run.id,
            verdict="verified",
            checks=[],
            evidence={"workspace_writes": writes},
        )
    )
    sqlite_session.commit()

    body = _changes(
        test_client, company_id, run.id, _member(sqlite_session, company_id, "Viewer")
    ).json()

    assert body["source"] == "workspace"
    assert [f["path"] for f in body["files"]] == ["a.py"]


def test_only_the_company_reads_its_changes(sqlite_session, test_client):
    company_id = _company(sqlite_session)
    other_id = _company(sqlite_session)
    run = _run(sqlite_session, company_id)

    assert _changes(test_client, company_id, run.id, {}).status_code == 401
    outsider = _member(sqlite_session, other_id, "Owner")
    assert _changes(test_client, company_id, run.id, outsider).status_code == 403
    # Its own company's id does not open another company's run.
    assert _changes(test_client, other_id, run.id, outsider).status_code == 404
