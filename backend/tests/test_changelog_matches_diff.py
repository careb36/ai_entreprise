"""A changelog fragment says only what the diff shows (``evidence/changelog_diff``).

Built from two real cases. Sprint 8 (2026-11-24, issue #701): a fragment
under ``Added`` claimed ``--container-mode``, ``add_arguments`` and ``apply``
in ``scripts/self_host/self_host.py`` and ``sandbox_mode.py``; all three were
on the base and the sprint's diff was a test file, a README and the fragment.
Sprint 7: a fragment named issue #780 while the sprint's issue was #756.
"""

from __future__ import annotations

from services.evidence import changelog_diff, delivery_gate, risk
from verification_helpers import _ingest, _make_company, _payload, _verify

S8_FRAGMENT = "changelog.d/701-container-sandbox-mode.md"
S8_CHANGED = [
    "backend/tests/test_self_host_sandbox_mode.py",
    S8_FRAGMENT,
    "scripts/self_host/README.md",
]
S8_OBJECTIVE = (
    "Issue #701 (self-hosting in container sandbox mode). Add a container-mode option."
)
# The fragment task 37 wrote (``fabrique/sprint-8``), whole.
S8_BAD = (
    "Added\n\n* **A self-hosting run can hold itself to the container sandbox "
    "(issue #701).** `scripts/self_host/self_host.py` gains "
    "`--container-mode` (`scripts/self_host/sandbox_mode.py`, "
    "`add_arguments` and `apply`): it sets the company's sandbox to "
    "`container`, one root command run with the sandbox image's own `python` "
    "(`--profile sprint-tests`, `python -m pytest -q -p no:cacheprovider "
    "tests`; `--profile` and `--test-command` still override), and the "
    "`sandbox` step after `wait`. The `sandbox` step reads, from the "
    "daemon's reply to each of the sprint's test runs "
    "(`evidence.test_run.sandbox`), the mode the replies report — "
    "`container`, `host`, `bwrap`, `mixed`, or `not observed` — and keeps it "
    "apart from `asked`, the mode the harness configured, in the report's "
    "`sandbox` (`asked`/`observed`/`observed_by: daemon`, `container_mode`, "
    "`modes`, `images`, `container_mode_asked`); the daemon's announced "
    "abilities are a claim and never label a run. A `--container-mode` run "
    "whose replies are anything but `container` — `host`, `bwrap`, a mode "
    "reported `(not isolated)`, `mixed`, or `not observed` — is never "
    "labelled container mode: the harness exits with status 5 and adds the "
    "reason to `notes`. `backend/tests/test_self_host_sandbox_mode.py` "
    "proves the flag reaches the sandbox setting and that a non-container "
    "observation fails the run, against replies in the daemon's own fixture "
    "shape. `scripts/self_host/README.md` documents the flag, the "
    "observed/asked split, and the stack-side settings an owner must set, as "
    "the e2e's `container` job does.\n"
)
# The corrected fragment (``fix/sprint-8-changelog``), whole.
S8_GOOD = (
    "Changed\n\n* **The self-hosting harness's container mode is tested and "
    "documented more precisely (issue #701).** `--container-mode` already "
    "existed (`scripts/self_host/sandbox_mode.py`). "
    "`backend/tests/test_self_host_sandbox_mode.py` now also checks that the "
    "report keeps the request (`container_mode_asked`) apart from what the "
    "daemon observed (`container_mode`), whether the daemon reported a "
    "container or the host. `scripts/self_host/README.md` names the "
    "`sandbox` step's full query and lists the observed modes that are never "
    "labelled container mode (`host`, `bwrap`, `(not isolated)`, `mixed`, "
    "`not observed`), which exit with status 5. The run itself, and its run "
    "ids, are still to be recorded.\n"
)
# The base's files, cut to the parts that matter (the merge base of sprint 8).
S8_BASE = {
    "scripts/self_host/sandbox_mode.py": (
        'TEST_COMMAND = "python -m pytest -q -p no:cacheprovider tests"\n\n'
        "def add_arguments(parser: argparse.ArgumentParser) -> None:\n"
        '    parser.add_argument("--sandbox", choices=("host", "container", "bwrap"))\n'
        '    parser.add_argument("--profile", choices=("folders", "sprint-tests"))\n'
        '    parser.add_argument("--test-command", default=None)\n'
        '    parser.add_argument("--container-mode", action="store_true")\n\n'
        "def apply(parser, args, all_steps):\n"
        "    if args.container_mode:\n"
        '        args.sandbox = "container"\n\n'
        "def summarize(verification, asked):\n"
        '    return {"asked": asked, "container_mode_asked": asked}\n'
    ),
    "scripts/self_host/self_host.py": (
        "import sandbox_mode\n\n"
        "def main(argv=None):\n"
        "    sandbox_mode.add_arguments(parser)\n"
        "    steps = sandbox_mode.apply(parser, args, STEPS)\n"
    ),
    "backend/tests/test_self_host_sandbox_mode.py": (
        "def test_container_mode_sets_the_sandbox():\n"
        '    args = parse(["--container-mode"])\n'
    ),
    "scripts/self_host/README.md": "# Self-hosting\n\n`--container-mode` runs in a container.\n",
}
S7_FRAGMENT = "changelog.d/780-conditional-provider-secrets-bench-real.md"
# The fragment task 30 wrote in sprint 7, whole.
S7_TEXT = (
    "Changed\n\n* **Bench real: require only `DEEPSEEK_API_KEY`, map the other "
    "provider keys only when their secrets are set (issue #780).** The CI workflow "
    "`.github/workflows/bench.yml` now fails the `bench-real` required-secrets check "
    "exclusively when `DEEPSEEK_API_KEY` is absent, and passes each remaining provider "
    "key to the job environment only if the corresponding secret exists, preserving "
    "existing key names and the DeepSeek path.\n"
)
S7_OBJECTIVE = "Issue #756: in .github/workflows/bench.yml, job bench-real, ..."


def _observed(path, text, changed, base, objective, cut=False):
    """The evidence the loop records (``loop/changelog_observe.py``), built
    with the evidence's own functions from the daemon's bytes."""
    parsed = [changelog_diff.parse(path, text)]
    wanted, base_cut = changelog_diff.base_paths(parsed, changed)
    names = changelog_diff.names_to_search(parsed)
    base_files = []
    for p in wanted:
        if p in base:
            found = changelog_diff.found_in(base[p], names)
            base_files.append({"path": p, "status": "present", "found": found})
        else:
            base_files.append({"path": p, "status": "missing"})
    return {
        "workspace_writes": [{"path": path, "status": "written", "sha256": "0" * 64}],
        changelog_diff.EVIDENCE_KEY: {
            "base": "main",
            "merge_base": "a" * 40,
            "commit": "b" * 40,
            "changed": list(changed),
            "changed_truncated": cut,
            "fragments": [{"path": path, "status": "present", "text": text}],
            "base_files": base_files,
            "base_cut": base_cut,
            "names_searched": names,
            "sprint_issue": changelog_diff.sprint_issue(objective),
        },
    }


def _s8(text):
    return changelog_diff.check(
        _observed(S8_FRAGMENT, text, S8_CHANGED, S8_BASE, S8_OBJECTIVE)
    )


def test_sprint_8_fragment_claiming_existing_work_is_refuted():
    result = _s8(S8_BAD)
    assert result["outcome"] == "refuted", result
    text = result["observation"]
    assert "`scripts/self_host/self_host.py`" in text
    assert "`scripts/self_host/sandbox_mode.py`" in text
    assert "claims to add what the base already has" in text
    for name in ("`--container-mode`", "`add_arguments`", "`apply`"):
        assert name in text, name
    # Paths the diff did change are not named as refuted.
    first = result["refuted"][0]
    assert "README.md" not in first and "test_self_host" not in first


def test_sprint_8_corrected_fragment_passes():
    result = _s8(S8_GOOD)
    assert result["outcome"] == "pass", result
    assert result["refuted"] == [] and result["unchecked"] == []


def test_sprint_7_fragment_for_another_issue_is_refuted():
    changed = [".github/workflows/bench.yml", "bench/README.md", S7_FRAGMENT]
    base = {".github/workflows/bench.yml": "jobs:\n  bench-real:\n"}
    result = changelog_diff.check(
        _observed(S7_FRAGMENT, S7_TEXT, changed, base, S7_OBJECTIVE)
    )
    assert result["outcome"] == "refuted", result
    assert "#780" in result["observation"] and "#756" in result["observation"]


def test_an_unknown_sprint_issue_is_a_finding_only():
    changed = [".github/workflows/bench.yml", S7_FRAGMENT]
    result = changelog_diff.check(
        _observed(S7_FRAGMENT, S7_TEXT, changed, {}, "Split a test file.")
    )
    assert result["outcome"] == "pass", result
    assert any("names no issue" in f for f in result["findings"])


def test_context_words_excuse_an_unchanged_path():
    text = "Changed\n\n* **X (issue #701).** It uses the existing `scripts/a.py`.\n"
    base = {"scripts/a.py": "x = 1\n"}
    result = changelog_diff.check(
        _observed(S8_FRAGMENT, text, [S8_FRAGMENT], base, S8_OBJECTIVE)
    )
    assert result["outcome"] == "pass", result
    plain = text.replace("the existing ", "")
    result = changelog_diff.check(
        _observed(S8_FRAGMENT, plain, [S8_FRAGMENT], base, S8_OBJECTIVE)
    )
    assert result["outcome"] == "refuted" and "`scripts/a.py`" in result["observation"]


def test_a_name_new_in_the_diff_passes_under_added():
    text = "Added\n\n* **X (issue #701).** `scripts/a.py` gains `--strict` and `check_all()`.\n"
    changed = ["scripts/a.py", S8_FRAGMENT]
    base = {"scripts/a.py": "def run():\n    return 1\n"}
    result = changelog_diff.check(
        _observed(S8_FRAGMENT, text, changed, base, S8_OBJECTIVE)
    )
    assert result["outcome"] == "pass", result


def test_a_plain_word_counts_only_as_a_definition():
    assert changelog_diff.found_in("def apply(x):\n", ["apply"]) == ["apply"]
    assert changelog_diff.found_in("we apply it\n", ["apply"]) == []
    assert changelog_diff.found_in('flag = "--strict-mode"\n', ["--strict"]) == []


def test_what_cannot_be_decided_is_unverifiable_and_held_for_a_person():
    written = {"workspace_writes": [{"path": S8_FRAGMENT, "status": "written"}]}
    result = changelog_diff.check(written)
    assert result["outcome"] == "unverifiable"
    errored = {**written, changelog_diff.EVIDENCE_KEY: {"error": "no daemon"}}
    assert changelog_diff.check(errored)["outcome"] == "unverifiable"
    cut = _observed(S8_FRAGMENT, S8_GOOD, ["x.py"], S8_BASE, S8_OBJECTIVE, cut=True)
    assert changelog_diff.check(cut)["outcome"] == "unverifiable"
    reason = changelog_diff.hold_reason(result)
    held = risk.held_for_a_person({"tier": "low", "reasons": []}, reason, "changelog")
    assert held["tier"] == "high"
    assert changelog_diff.hold_reason(_s8(S8_GOOD)) is None


def test_no_fragment_no_check():
    readme = {
        "workspace_writes": [{"path": "changelog.d/README.md", "status": "written"}]
    }
    assert changelog_diff.check(readme) is None
    assert changelog_diff.check({"workspace_writes": []}) is None


def test_the_gate_sends_a_refuted_fragment_back_before_qa():
    assert delivery_gate._GATE_PROBLEMS[changelog_diff.CHECK_NAME]
    assert changelog_diff.CHECK_NAME not in str(delivery_gate.CODE_PROBLEMS)


def test_the_verifier_refutes_the_sprint_8_fragment(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    artifact = f"```markdown path={S8_FRAGMENT}\n{S8_BAD}```\n"
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, artifact=artifact, claims={"files": [S8_FRAGMENT]}),
    ).json()
    evidence = _observed(S8_FRAGMENT, S8_BAD, S8_CHANGED, S8_BASE, S8_OBJECTIVE)
    body = _verify(test_client, run["id"], company_id, evidence).json()
    checks = {c["name"]: c for c in body["checks"]}
    assert checks[changelog_diff.CHECK_NAME]["outcome"] == "refuted"
    assert body["verdict"] == "refuted"


def test_the_verifier_holds_an_unobserved_fragment_for_a_person(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    artifact = f"```markdown path={S8_FRAGMENT}\n{S8_GOOD}```\n"
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, artifact=artifact, claims={"files": [S8_FRAGMENT]}),
    ).json()
    evidence = {"workspace_writes": [{"path": S8_FRAGMENT, "status": "written"}]}
    body = _verify(test_client, run["id"], company_id, evidence).json()
    checks = {c["name"]: c for c in body["checks"]}
    assert checks[changelog_diff.CHECK_NAME]["outcome"] == "unverifiable"
    assert checks["risk_tier"]["tier"] == "high"
    assert "changelog" in checks["risk_tier"]["observation"]
