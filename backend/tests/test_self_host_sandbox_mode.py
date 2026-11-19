"""The self-hosting run's sandbox, as the daemon reported it (issue #701).

The harness labels a run with the mode the daemon's replies to its test runs
show, never with the mode it asked for; a ``--container-mode`` run the daemon
did not report in a container is refused that name (exit 5). The replies are
fixtures in the daemon's own shape: the host one is the protocol's recorded
reply (``vscode-daemon/protocol/fixtures/v1/workspace.execute``), the
container one is what ``vscode-daemon/sandbox.report`` writes for a container.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SELF_HOST = REPO / "scripts" / "self_host"
HOST_REPLY = json.loads(
    (
        REPO
        / "vscode-daemon/protocol/fixtures/v1/workspace.execute/assistant-on-the-host.json"
    ).read_text()
)["reply"]["sandbox"]
CONTAINER_REPLY = {
    "mode": "container",
    "forced_by_daemon": False,
    "isolated": True,
    "network": "off",
    "env": [],
    "workdir": "/workspace",
    "runtime": "docker",
    "image": "fabrique-e2e-sandbox:1",
    "cpus": 2.0,
    "memory_mb": 2048,
    "pids_limit": 256,
    "capabilities": "all dropped",
    "no_new_privileges": True,
    "image_identity": {
        "id": "sha256:" + "ab" * 32,
        "digest": None,
        "digest_reason": "a local build has no registry digest",
        "observed": "before_run",
    },
}


def _script(path: Path, name: str):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sandbox_mode = _script(SELF_HOST / "sandbox_mode.py", "self_host_sandbox_mode")
self_host = _script(SELF_HOST / "self_host.py", "self_host_harness_sandbox")


def _verification(vid: int, sandbox=None, *, ran: bool = True) -> dict:
    run = {"command": "python -m pytest -q", "exit_code": 0} if ran else {}
    if sandbox is not None:
        run["sandbox"] = sandbox
    return {"id": vid, "evidence": {"test_run": run} if ran else {}, "checks": []}


class FakeApi:
    """The two read routes the step calls, answering from fixtures."""

    def __init__(self, verifications: dict[int, list[dict]]):
        self.verifications = verifications
        self.paths: list[str] = []

    def get(self, path, raw=False):
        self.paths.append(path)
        if path.startswith("/v1/agent_runs?"):
            assert "company_id=7" in path and "sprint_id=11" in path
            return {"agent_runs": [{"id": rid} for rid in self.verifications]}
        rid = int(path.split("/")[3])
        return {"verifications": self.verifications[rid]}


def _run(tmp_path, monkeypatch, capsys, verifications, *extra):
    checkout = tmp_path / "snap"
    state = Path(f"{checkout}.self_host.json")
    state.write_text(json.dumps({"company_id": 7, "sprint_id": 11}))
    api = FakeApi(verifications)
    monkeypatch.setattr(self_host, "Api", lambda base, token: api)
    code = self_host.main(["--checkout", str(checkout), "--steps", "sandbox", *extra])
    return code, json.loads(capsys.readouterr().out)


def test_when_the_daemon_reports_a_container_the_run_is_recorded_as_container_mode(
    tmp_path, monkeypatch, capsys
):
    code, report = _run(
        tmp_path,
        monkeypatch,
        capsys,
        {
            1: [_verification(10, CONTAINER_REPLY)],
            2: [_verification(20, CONTAINER_REPLY)],
        },
        "--container-mode",
    )
    seen = report["sandbox"]
    assert code == 0
    assert seen["observed"] == "container" and seen["container_mode"] is True
    assert seen["observed_by"] == "daemon" and seen["asked"] == "container"
    assert seen["replies"] == 2 and seen["runs_looked_at"] == 2
    assert seen["images"] == ["sha256:" + "ab" * 32]
    assert [r["run_id"] for r in seen["runs"]] == [1, 2]
    assert "note" not in seen and "notes" not in report


def test_when_the_daemon_reports_the_host_the_run_is_labelled_host_and_the_harness_says_so(
    tmp_path, monkeypatch, capsys
):
    # Asked for a container (the default --sandbox), the daemon ran it on the host.
    code, report = _run(
        tmp_path,
        monkeypatch,
        capsys,
        {1: [_verification(10, HOST_REPLY)]},
        "--container-mode",
    )
    seen = report["sandbox"]
    assert code == sandbox_mode.EXIT_NOT_CONTAINER == 5
    assert seen["asked"] == "container" and seen["observed"] == "host"
    assert seen["container_mode"] is False
    assert "labelled host, not container mode" in seen["note"]
    assert any("not a container-mode run" in n for n in report["notes"])


def test_when_the_daemon_reports_no_sandbox_the_mode_is_not_observed(
    tmp_path, monkeypatch, capsys
):
    # An older daemon's reply (no sandbox key), and a run whose tests never ran.
    code, report = _run(
        tmp_path,
        monkeypatch,
        capsys,
        {1: [_verification(10)], 2: [_verification(20, ran=False)]},
        "--container-mode",
    )
    seen = report["sandbox"]
    assert code == 5
    assert seen["observed"] == sandbox_mode.NOT_OBSERVED == "not observed"
    assert seen["observed_by"] is None and seen["container_mode"] is False
    assert seen["runs_looked_at"] == 2 and seen["replies"] == 0
    assert "not labelled with any mode" in seen["note"]


def test_without_container_mode_a_host_run_is_recorded_as_host_and_exits_zero(
    tmp_path, monkeypatch, capsys
):
    code, report = _run(
        tmp_path,
        monkeypatch,
        capsys,
        {1: [_verification(10, HOST_REPLY)]},
        "--sandbox",
        "host",
    )
    assert code == 0
    assert report["sandbox"]["observed"] == "host" and "note" not in report["sandbox"]


def test_replies_that_disagree_are_mixed_and_never_container_mode():
    runs = [
        {"run_id": 1, **sandbox_mode.applied(_verification(10, CONTAINER_REPLY))},
        {"run_id": 2, **sandbox_mode.applied(_verification(20, HOST_REPLY))},
    ]
    seen = sandbox_mode.summarize("container", runs, 2)
    assert seen["observed"] == "mixed" and seen["container_mode"] is False
    assert seen["modes"] == {"container": 1, "host": 1}


def test_a_container_reply_that_was_not_isolated_is_not_container_mode():
    reply = {**CONTAINER_REPLY, "isolated": False}
    runs = [{"run_id": 1, **sandbox_mode.applied(_verification(10, reply))}]
    seen = sandbox_mode.summarize("container", runs, 1)
    assert (
        seen["observed"] == "container (not isolated)"
        and seen["container_mode"] is False
    )


def test_no_sprint_is_not_observed(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "snap"
    Path(f"{checkout}.self_host.json").write_text(json.dumps({"company_id": 7}))
    monkeypatch.setattr(self_host, "Api", lambda base, token: FakeApi({}))
    code = self_host.main(
        ["--checkout", str(checkout), "--steps", "sandbox", "--container-mode"]
    )
    seen = json.loads(capsys.readouterr().out)["sandbox"]
    assert (
        code == 5 and seen["observed"] == "not observed" and "no sprint" in seen["note"]
    )


def test_container_mode_configures_the_run_as_the_e2e_container_job_does():
    parser = self_host.argparse.ArgumentParser()
    sandbox_mode.add_arguments(parser)
    parser.add_argument("--steps", default="all")
    args = parser.parse_args(["--container-mode"])
    steps = sandbox_mode.apply(parser, args, self_host.STEPS)
    assert (args.sandbox, args.profile) == ("container", "sprint-tests")
    # The image's own python, as scripts/e2e/run.sh's TEST_COMMAND in container mode.
    assert args.test_command == "python -m pytest -q -p no:cacheprovider tests"
    assert steps.index("sandbox") == steps.index("wait") + 1
    named = parser.parse_args(["--container-mode", "--steps", "issue,wait,review"])
    assert sandbox_mode.apply(parser, named, self_host.STEPS) == (
        "issue",
        "wait",
        "sandbox",
        "review",
    )
    # Without wait there is no sprint to observe: nothing is added.
    setup = parser.parse_args(["--container-mode", "--steps", "checkout,company"])
    assert sandbox_mode.apply(parser, setup, self_host.STEPS) == ("checkout", "company")
    with pytest.raises(SystemExit):
        sandbox_mode.apply(
            parser,
            parser.parse_args(["--container-mode", "--sandbox", "host"]),
            self_host.STEPS,
        )


def test_without_container_mode_the_defaults_are_unchanged():
    parser = self_host.argparse.ArgumentParser()
    sandbox_mode.add_arguments(parser)
    parser.add_argument("--steps", default="checkout")
    args = parser.parse_args([])
    assert sandbox_mode.apply(parser, args, self_host.STEPS) == ("checkout",)
    assert (args.sandbox, args.profile, args.container_mode) == (
        "container",
        "folders",
        False,
    )
