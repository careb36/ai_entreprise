"""The protected-paths CI check (``scripts/protected_paths_check.py``, ``protected-paths.yml``).

docs/design/rsi-loop.md §3.3 layer 4, REQ-12 and REQ-14: a pull request whose
git diff matches a protected pattern fails unless a code owner approved its
head sha. These tests hold:

- the decision: no match passes without reading a review; a match with no
  approval, an approval of an older sha, a non-owner's approval, a later
  "changes requested", a dismissal or the author's own approval refuses; an
  owner's approval of the head passes and the summary names who and which sha;
- the inputs: the changed paths come from ``git diff base...head``, the list,
  the test map and CODEOWNERS from ``--root`` (the base commit), and a check
  that cannot read one of them refuses (exit 2);
- CODEOWNERS read as GitHub reads it, last matching line winning;
- the workflow: its triggers, its least-privilege token, the base checked out
  and the head only fetched, the base's script run in isolated mode.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "protected-paths.yml"
_spec = importlib.util.spec_from_file_location(
    "protected_paths_check", REPO / "scripts" / "protected_paths_check.py"
)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

HEAD = "b" * 40
OLD = "a" * 40
RULES = check.parse_codeowners(
    "*            @owner\n"
    "/bench/      @owner @second\n"
    "/docs/POSITIONING.md @org/stewards\n"
    "/scripts/orphan.py\n"
)
PROTECTED = check.load_protected(REPO)


def _review(login: str, state: str = "APPROVED", sha: str = HEAD):
    return check.Review(login, state, sha)


def _decide(changed, reviews, author="author", team_check=None, patterns=None):
    return check.decide(
        changed,
        patterns or ("bench/**", "docs/POSITIONING.md", "scripts/orphan.py"),
        PROTECTED,
        RULES,
        reviews,
        HEAD,
        author,
        team_check,
    )


# --- the decision --------------------------------------------------------------------


def test_no_protected_path_passes_without_reading_a_review():
    def unread():
        raise AssertionError("the reviews were read")

    decision = _decide(["frontend/src/App.tsx", "README.md"], unread)
    assert decision.passed and decision.paths == []
    assert "no protected path changed" in check.render(decision, OLD, HEAD)


def test_a_match_with_no_approval_is_refused():
    decision = _decide(["bench/specs/x/acceptance/test_x.py", "README.md"], [])
    assert not decision.passed
    assert [(p.path, p.pattern, p.approved_by) for p in decision.paths] == [
        ("bench/specs/x/acceptance/test_x.py", "bench/**", None)
    ]
    summary = check.render(decision, OLD, HEAD)
    assert (
        "**Refused**" in summary and "`bench/specs/x/acceptance/test_x.py`" in summary
    )


def test_an_approval_of_an_older_sha_does_not_count():
    decision = _decide(["bench/x.py"], [_review("owner", sha=OLD)])
    assert not decision.passed


def test_an_approval_by_a_reviewer_who_is_not_an_owner_does_not_count():
    decision = _decide(["bench/x.py"], [_review("someone")])
    assert not decision.passed


def test_an_owner_s_approval_of_the_head_passes_and_is_named():
    decision = _decide(
        ["bench/x.py", "README.md"],
        [_review("someone"), _review("Second", "COMMENTED"), _review("second")],
    )
    assert decision.passed
    assert decision.paths[0].approved_by == _review("second")
    summary = check.render(decision, OLD, HEAD)
    assert f"@second at `{HEAD}`" in summary and "**Pass**" in summary


def test_the_latest_decisive_review_of_each_reviewer_is_the_one_read():
    # A later "changes requested" or a dismissal withdraws an approval.
    for later in ("CHANGES_REQUESTED", "DISMISSED"):
        assert not _decide(
            ["bench/x.py"], [_review("owner"), _review("owner", later)]
        ).passed
    # A comment changes nothing; a new approval of the head replaces an old one.
    assert _decide(
        ["bench/x.py"], [_review("owner"), _review("owner", "COMMENTED")]
    ).passed
    assert _decide(["bench/x.py"], [_review("owner", sha=OLD), _review("owner")]).passed
    assert not _decide(
        ["bench/x.py"], [_review("owner"), _review("owner", sha=OLD)]
    ).passed


def test_the_author_s_own_approval_does_not_count():
    assert not _decide(["bench/x.py"], [_review("owner")], author="Owner").passed


def test_every_matched_path_needs_one_of_its_own_owners():
    # @second owns bench/ but not docs/POSITIONING.md, which a team owns.
    changed = ["bench/x.py", "docs/POSITIONING.md"]
    decision = _decide(changed, [_review("second")], team_check=lambda *_: False)
    assert [p.approved_by is not None for p in decision.paths] == [True, False]
    assert not decision.passed
    assert _decide(changed, [_review("second")], team_check=lambda *_: True).passed


def test_a_team_the_token_cannot_read_counts_as_not_a_member_and_says_so():
    decision = _decide(
        ["docs/POSITIONING.md"], [_review("x")], team_check=lambda *_: None
    )
    assert not decision.passed
    assert any("@org/stewards" in note for note in decision.notes)


def test_a_path_without_an_owner_cannot_be_approved():
    decision = _decide(["scripts/orphan.py"], [_review("owner")])
    assert not decision.passed
    assert decision.notes == ["scripts/orphan.py has no owner in .github/CODEOWNERS"]


# --- CODEOWNERS ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "hit"),
    [
        ("*", "any/file.py", True),
        ("/bench/", "bench/specs/x.py", True),
        ("/bench/", "other/bench/x.py", False),
        ("apps/", "deep/apps/x.js", True),
        ("*.js", "a/b/c.js", True),
        ("/docs", "docs/a/b.md", True),
        ("docs/*", "docs/a.md", True),
        ("docs/*", "docs/a/b.md", False),
        ("/backend/tests/test_bench_*.py", "backend/tests/test_bench_x.py", True),
        ("/backend/tests/test_bench_*.py", "backend/tests/test_bench_x/y.py", False),
        ("**/logs", "a/logs/x.txt", True),
        ("/docs/POSITIONING.md", "docs/POSITIONING.md", True),
        ("/docs/POSITIONING.md", "docs/POSITIONING.md.bak", False),
    ],
)
def test_codeowners_patterns_read_as_github_reads_them(pattern, path, hit):
    assert bool(check.codeowners_regex(pattern).fullmatch(path)) is hit


def test_the_last_matching_line_wins_even_without_owners():
    assert check.owners_of(RULES, "bench/x.py") == ["@owner", "@second"]
    assert check.owners_of(RULES, "README.md") == ["@owner"]
    assert check.owners_of(RULES, "scripts/orphan.py") == []


def test_the_repository_s_codeowners_owns_every_protected_example():
    rules = check.parse_codeowners((REPO / check.CODEOWNERS).read_text())
    for path in (
        "bench/specs/x.py",
        ".github/workflows/protected-paths.yml",
        "scripts/protected_paths_check.py",
        "backend/app/services/contracts/protected_paths.py",
    ):
        owners = check.owners_of(rules, path)
        assert owners and all(o.startswith("@") for o in owners), path


# --- the command: git, the base's list, failures -------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def pull(tmp_path):
    """A repository with a base and a head commit that changes ``bench/x.py``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "bench").mkdir()
    (repo / "bench" / "x.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "head")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def _root(tmp_path: Path, codeowners: str = "/bench/ @owner\n") -> Path:
    """A base tree: the repository's list and test map, and a CODEOWNERS."""
    root = tmp_path / "base"
    for name in (check.MODULE, check.CONTEXTS):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / name, root / name)
    (root / check.CODEOWNERS).parent.mkdir(parents=True, exist_ok=True)
    (root / check.CODEOWNERS).write_text(codeowners)
    return root


def _main(tmp_path, repo, base, head, root, reviews) -> tuple[int, str]:
    reviews_file = tmp_path / "reviews.json"
    reviews_file.write_text(json.dumps(reviews))
    summary = tmp_path / "summary.md"
    code = check.main(
        [
            "--base", base, "--head", head,
            "--root", str(root), "--repo", str(repo),
            "--reviews-file", str(reviews_file),
            "--summary", str(summary),
            "--author", "author",
        ]
    )  # fmt: skip
    return code, summary.read_text()


def _api(login: str, state: str, sha: str) -> dict:
    return {"user": {"login": login}, "state": state, "commit_id": sha}


def test_the_command_diffs_git_and_requires_the_head_s_approval(tmp_path, pull):
    repo, base, head = pull
    root = _root(tmp_path)
    code, summary = _main(tmp_path, repo, base, head, root, [])
    assert code == check.REFUSED and "`bench/x.py`" in summary
    code, summary = _main(
        tmp_path, repo, base, head, root, [_api("owner", "APPROVED", base)]
    )
    assert code == check.REFUSED
    code, summary = _main(
        tmp_path, repo, base, head, root, [_api("owner", "APPROVED", head)]
    )
    assert code == check.PASS and f"@owner at `{head}`" in summary


def test_the_list_in_force_is_the_base_s_copy(tmp_path, pull):
    # A base whose list does not name bench/ passes the same diff: what the
    # head's copy of the module says is never read.
    repo, base, head = pull
    root = _root(tmp_path)
    module = root / check.MODULE
    module.write_text(module.read_text().replace('"bench/**"', '"nothing-here/**"'))
    code, summary = _main(tmp_path, repo, base, head, root, [])
    assert code == check.PASS and "no protected path changed" in summary


def test_a_diff_git_cannot_make_refuses(tmp_path, pull):
    repo, base, _ = pull
    code, summary = _main(tmp_path, repo, base, "c" * 40, _root(tmp_path), [])
    assert code == check.UNDECIDED and "could not decide" in summary


def test_a_base_without_the_list_refuses(tmp_path, pull):
    repo, base, head = pull
    root = _root(tmp_path)
    (root / check.MODULE).unlink()
    code, summary = _main(tmp_path, repo, base, head, root, [])
    assert code == check.UNDECIDED and check.MODULE in summary


def test_the_reviews_are_read_page_by_page(monkeypatch):
    pages = {
        1: [_api(f"u{i}", "COMMENTED", HEAD) for i in range(check.PER_PAGE)],
        2: [_api("owner", "APPROVED", HEAD)],
    }
    seen = []

    def fake_get(url, token):
        page = int(url.rsplit("page=", 1)[1])
        seen.append(url)
        return 200, json.dumps(pages[page]).encode()

    monkeypatch.setattr(check, "_get", fake_get)
    raw = check.fetch_reviews("https://api.example.invalid", "o/r", 7, "")
    assert len(raw) == check.PER_PAGE + 1 and len(seen) == 2
    assert "/repos/o/r/pulls/7/reviews?per_page=100&page=1" in seen[0]
    monkeypatch.setattr(check, "_get", lambda url, token: (403, b""))
    with pytest.raises(check.CheckError):
        check.fetch_reviews("https://api.example.invalid", "o/r", 7, "")


def test_only_https_api_urls_are_called():
    with pytest.raises(check.CheckError):
        check._get("http://api.example.invalid/x", "")


# --- the workflow --------------------------------------------------------------------


def _workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text())
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_the_workflow_runs_on_pull_requests_and_on_reviews():
    on = _workflow()["on"]
    assert set(on) == {"pull_request", "pull_request_review"}
    assert on["pull_request_review"]["types"] == ["submitted", "dismissed"]
    assert "synchronize" in on["pull_request"]["types"]


def test_the_workflow_token_is_least_privilege():
    data = _workflow()
    expected = {"contents": "read", "pull-requests": "read"}
    assert data["permissions"] == expected
    assert data["jobs"]["check"]["permissions"] == expected


def test_the_workflow_runs_the_base_s_script_and_never_checks_out_the_head():
    steps = _workflow()["jobs"]["check"]["steps"]
    checkouts = [
        s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    run = "\n".join(str(s.get("run", "")) for s in steps)
    assert 'git fetch --no-tags --quiet origin "$HEAD_SHA"' in run
    assert "python3 -I scripts/protected_paths_check.py" in run
    assert '--root "$GITHUB_WORKSPACE"' in run
    assert "git show" not in run and "git checkout" not in run
    assert _workflow()["jobs"]["check"]["name"] == "protected-paths"
    final = steps[-1]
    assert final["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert final["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"


def test_the_check_and_its_workflow_are_protected():
    for path in (
        ".github/workflows/protected-paths.yml",
        "scripts/protected_paths_check.py",
        "backend/tests/test_protected_paths_check.py",
    ):
        assert PROTECTED.matches([path]), path
