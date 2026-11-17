#!/usr/bin/env python3
"""One self-improvement cycle, the steps of rsi-loop §7 scripted, with no merge.

    python3 scripts/rsi/cycle.py run --spec scripts/self_host/specs/<name>-specification.json \\
        --findings findings.json --cycle-id cycle-1-folder-lint \\
        --baseline origin/main (--candidate C | --pr N) --repository OWNER/NAME \\
        --python <backend python> --work /tmp/rsi/cycle-1 --out /tmp/rsi/cycle-1.json \\
        [--k 3] [--retry-by NAME] [--finding-id F5] [--close-pr] [--dry-run]
    python3 scripts/rsi/cycle.py verify --record /tmp/rsi/cycle-1.json
    python3 scripts/rsi/cycle.py record --record /tmp/rsi/cycle-1.json \\
        --merged-sha <sha a person merged> --out rsi/cycles/cycle-1-folder-lint.json

``run`` (``docs/design/rsi-loop.md`` §7 steps 2, 6 and 7; REQ-1, REQ-8,
REQ-17, REQ-18, REQ-20, REQ-26):

1. B is the sha ``--baseline`` resolves to (REQ-1); C is ``--candidate`` or
   the head of ``--pr`` read from GitHub's API. Both must be in ``--repo``.
2. The specification is read from B's objects with its target measure
   (REQ-2); the findings' state (``--findings``, the platform's
   ``GET /v1/findings`` saved to a file) refuses a specification whose cycle
   was reverted unless ``--retry-by`` names a person (REQ-20).
3. From git alone: the graders' trees on B and C, and the protected paths C
   changes, matched with B's list. When C changed the graders or touches a
   protected path without a code owner's approval of C, the bench is not run:
   the decision cannot be ``keep``.
4. B and C are cloned into ``--work`` at their shas. B's copy of the target
   measure runs on B and must fail (exit 3); otherwise the cycle stops.
5. The real-model bench runs K times per side, alternating B, C, each with
   ``run.sh --graders <B>``, then the fake bench (the circuit check) on C. A
   card not graded by B's tests stops the cycle. B's Verifier bench cases
   run on each side's Verifier, and each side's backend tests are collected.
6. B's target measure runs on C; the check-runs of B and of C are read by sha
   from GitHub's API (REQ-8), every page, and a run whose ``head_sha`` is not
   the side's sha is refused; then B's copy of ``scripts/rsi/decide.py``
   decides.
7. With ``--pr``, the head is read again: if it moved, the decision is void
   (``unmeasured``, REQ-17).

Each command, its exit status and the sha256 of its output, and the sha256 of
every file kept (scorecards, listings, measure reports, the decision), are
computed here (REQ-26, REQ-27) and written into the record ``--out``. The
script never merges, never pushes and never approves: on ``keep`` it says the
pull request is eligible for a person's merge pinned to C (REQ-17); on
``revert`` or ``unmeasured`` it says so with the reasons, and closes the pull
request only with ``--close-pr`` (REQ-18). ``--dry-run`` checks the inputs
from git and the files and prints the plan, with no bench and no network.

``verify`` hashes the kept files again (exit 1 when one changed). ``record``,
after a person merged, adds ``merged_sha`` (C must be its ancestor) and git's
``merged_at`` to a ``keep`` record, checks the kept files and writes the
record ``scripts/rsi/watch.py`` reads, validated by ``cycle_record.py``.

Exit status: 0 keep, 1 revert, 2 unmeasured (or a usage error), 3 refused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import cycle_gate as gate
import cycle_github as github
import cycle_record
import cycle_steps as steps_mod
from cycle_after import cmd_record, cmd_verify, say
from cycle_steps import CycleError, Steps, git

EXIT = {"keep": 0, "revert": 1, "unmeasured": 2, "refused": 3}


class Refused(Exception):
    """The cycle stops; ``reason`` is a code, ``detail`` says why."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason, self.detail = reason, detail


class Cycle:
    """One cycle's state, shared by its steps."""

    def __init__(self, args: argparse.Namespace, runner=None) -> None:
        self.args = args
        self.repo = Path(args.repo).resolve()
        self.work = Path(args.work).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.steps = Steps(self.work, runner)
        self.record: dict[str, Any] = {
            "schema_version": cycle_record.SCHEMA_VERSION,
            "cycle_id": args.cycle_id,
            "status": "running",
            "started_at": steps_mod.now(),
            "work_dir": str(self.work),
            "k": args.k,
            "specification": Path(args.spec)
            .name.removesuffix(".json")
            .removesuffix("-specification"),
            "specification_path": args.spec,
        }
        self.b_sha = self.c_sha = ""
        self.inputs: dict[str, Path] = {}
        # The grade is the sandbox's, never in-process (PR 12).
        self.grading = steps_mod.grading_env(getattr(args, "grading_image", None))
        self.isolation: dict[str, str | None] = {}
        self.not_sandboxed: list[str] = []

    # ── preflight: git and files only ────────────────────────────────────────

    def shas(self) -> None:
        args = self.args
        if not cycle_record.cycle_id_ok(args.cycle_id):
            raise Refused("bad_cycle_id", f"{args.cycle_id!r} is not a branch-safe id")
        self.b_sha = steps_mod.resolve(self.repo, args.baseline)
        self.record.update(baseline_ref=args.baseline, baseline_sha=self.b_sha)
        self.record["graders_sha"] = self.b_sha
        if args.candidate:
            self.c_sha = steps_mod.resolve(self.repo, args.candidate)
        elif not args.dry_run:
            head, url = github.pull_head(args.repository, args.pr)
            self.steps.note("pull_request_head", command=["GET", url], head_sha=head)
            self.c_sha = steps_mod.resolve(self.repo, head)
        if args.pr:
            self.record["pull_request"] = args.pr
        if self.c_sha:
            self.record["candidate_sha"] = self.c_sha
            if self.c_sha == self.b_sha:
                raise Refused("same_sha", "B and C are the same commit")

    def specification(self) -> None:
        try:
            text = steps_mod.file_at(self.repo, self.b_sha, self.args.spec)
        except CycleError as exc:
            raise Refused("no_specification", f"{self.args.spec} at B: {exc}") from exc

        def at_b(path: str) -> bool:
            try:
                git(self.repo, "cat-file", "-e", f"{self.b_sha}:{path}")
            except CycleError:
                return False
            return True

        try:
            self.spec = gate.read_spec(text, at_b)
            rows = gate.load_findings(self.args.findings)
        except gate.GateRefused as exc:
            raise Refused("specification_refused", str(exc)) from exc
        findings_copy = self.work / "inputs" / "findings.json"
        findings_copy.parent.mkdir(parents=True, exist_ok=True)
        findings_copy.write_bytes(Path(self.args.findings).read_bytes())
        self.steps.keep(findings_copy)
        found = gate.findings_gate(self.spec["sha256"], rows, self.args.retry_by)
        self.record["findings_gate"] = found
        self.record["target_measure"] = self.spec["target_measure"]
        self.record["prompt_targeted"] = self.spec["prompt_targeted"]
        finding = found["finding"] or {}
        finding_id = (
            self.args.finding_id or finding.get("source_ref") or finding.get("id")
        )
        if finding_id is not None:
            self.record["finding_id"] = str(finding_id)
        if not found["allowed"]:
            raise Refused(
                "specification_reverted",
                f"the specification's text is that of reverted finding(s) "
                f"{found['reverted_findings']}; a person may ask with --retry-by (REQ-20)",
            )

    def graders_and_protected(self, reviews) -> bool:
        """The graders' trees and the protected match; False when the bench
        cannot change the outcome (graders differ, or an unapproved path)."""
        b = steps_mod.grader_trees(self.repo, self.b_sha)
        c = steps_mod.grader_trees(self.repo, self.c_sha)
        graders = {
            "baseline_tree": b["digest"],
            "candidate_tree": c["digest"],
            "baseline_paths": b["trees"],
            "candidate_paths": c["trees"],
        }
        self.inputs["graders"] = self.steps.write_json(
            self.work / "graders.json", graders
        )
        b_tree = self.tree("B", self.b_sha)
        try:
            protected = gate.protected_match(
                b_tree, self.repo, self.b_sha, self.c_sha, reviews, self.args.author
            )
        except gate.GateRefused as exc:
            raise Refused("protected_unreadable", str(exc)) from exc
        self.inputs["protected"] = self.steps.write_json(
            self.work / "protected.json", protected
        )
        self.steps.note(
            "protected_match",
            touched=[t["path"] for t in protected["touched"]],
            approval=protected["approval"],
        )
        approved = set((protected["approval"] or {}).get("paths") or [])
        unapproved = [t for t in protected["touched"] if t["path"] not in approved]
        return b["digest"] == c["digest"] and not unapproved

    def tree(self, side: str, sha: str) -> Path:
        return steps_mod.make_tree(self.repo, sha, self.work / "trees" / side)

    # ── the measurements ─────────────────────────────────────────────────────

    def measure(self, side: str, build: Path) -> str:
        report = self.work / "measure" / f"{side}-measure.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        argv = steps_mod.measure_argv(
            self.work / "trees" / "B", build, self.args.spec, self.args.python, report
        )
        status, _ = self.steps.command(
            f"measure_{side}", argv, self.work / "trees" / "B", env=self.grading
        )
        if report.is_file():
            self.steps.keep(report)
        result = steps_mod.measure_result(status)
        isolation = steps_mod.measure_isolation(report)
        self.isolation[side] = isolation
        if result in ("pass", "fail") and isolation != steps_mod.SANDBOX:
            # Never a grade outside the sandbox (PR 12): unknown, not a result.
            self.steps.note(f"measure_{side}_not_sandboxed", isolation=isolation)
            return "error"
        return result

    def bench(self, b_tree: Path, c_tree: Path) -> dict[str, Any]:
        if not os.environ.get("BENCH_LLM_URL"):
            raise Refused(
                "no_real_model", "BENCH_LLM_URL is empty: the fake measures no factory"
            )
        dirs: dict[str, list[Path]] = {"B": [], "C": []}
        for i in range(self.args.k):  # alternately, B then C (rsi-loop §2.5)
            for side, tree in (("B", b_tree), ("C", c_tree)):
                out = self.work / "bench" / f"{side}-real-{i + 1}"
                env = {
                    **self.grading,
                    "BENCH_WORKDIR": str(
                        self.work / "bench-work" / f"{side}-real-{i + 1}"
                    ),
                }
                self.steps.command(
                    f"bench_{side}_real_{i + 1}",
                    steps_mod.bench_argv(tree, out, self.b_sha),
                    tree,
                    env=env,
                )
                dirs[side].append(out)
        fake = self.work / "bench" / "C-fake"
        self.steps.command(
            "bench_C_fake",
            steps_mod.bench_argv(c_tree, fake, self.b_sha),
            c_tree,
            env={
                **self.grading,
                "BENCH_WORKDIR": str(self.work / "bench-work" / "C-fake"),
                "BENCH_LLM_URL": None,
            },
        )
        found = {
            side: [c for d in ds for c in steps_mod.cards_in(d)]
            for side, ds in dirs.items()
        }
        fake_cards = steps_mod.cards_in(fake)
        every = found["B"] + found["C"] + fake_cards
        for path, _ in every:
            self.steps.keep(path)
        if problems := gate.pinned_problems(every, self.b_sha):
            raise Refused("graders_not_pinned", "; ".join(problems))
        self.not_sandboxed = steps_mod.unsandboxed(every)
        return {"dirs": dirs, "cards": found, "fake": fake_cards}

    def verifier_and_tests(self, side: str, sha: str, tree: Path) -> None:
        python = self.args.python
        backend = tree / "backend"
        bench_b = self.work / "trees" / "B" / "bench"
        _, out = self.steps.command(
            f"verifier_{side}",
            steps_mod.verifier_argv(python),
            backend,
            env={"PYTHONPATH": f".:app:{bench_b}"},
        )
        verifier = {"sha": sha, "cases": steps_mod.parse_verifier(out)}
        self.inputs[f"verifier_{side}"] = self.steps.write_json(
            self.work / f"{side}-verifier.json", verifier
        )
        _, out = self.steps.command(
            f"tests_collected_{side}",
            steps_mod.collect_argv(python),
            backend,
            env={"PYTHONPATH": ".:app"},
        )
        count = steps_mod.parse_collected(out)
        tests = {"sha": sha, "suites": {"backend": count} if count is not None else {}}
        self.inputs[f"tests_{side}"] = self.steps.write_json(
            self.work / f"{side}-tests.json", tests
        )

    def check_runs(self, side: str, sha: str) -> None:
        """REQ-8: the side's check-runs, read by sha; a foreign run refused."""
        listing, urls = github.check_runs(self.args.repository, sha)
        path = self.steps.write_json(self.work / f"{side}-ci.json", listing)
        foreign = sorted(
            str(r.get("name"))
            for r in listing["check_runs"]
            if not isinstance(r, dict) or r.get("head_sha") != sha
        )
        self.steps.note(
            f"check_runs_{side}",
            command=["GET", *urls],
            sha=sha,
            runs=len(listing["check_runs"]),
            refused=foreign,
            output_sha256=self.steps.kept[self.steps.rel(path)],
        )
        self.inputs[f"ci_{side}"] = path

    def decide(
        self, bench: dict[str, Any] | None, target: dict | None
    ) -> dict[str, Any]:
        if target is not None:
            self.inputs["target"] = self.steps.write_json(
                self.work / "target.json", target
            )
        argv = [
            sys.executable,
            str(self.work / "trees" / "B" / "scripts" / "rsi" / "decide.py"),
            "--baseline-sha", self.b_sha,
            "--candidate-sha", self.c_sha,
            "--k", str(self.args.k),
        ]  # fmt: skip
        for flag, key in (
            ("--baseline-checks", "ci_B"), ("--candidate-checks", "ci_C"),
            ("--baseline-tests", "tests_B"), ("--candidate-tests", "tests_C"),
            ("--baseline-verifier", "verifier_B"), ("--candidate-verifier", "verifier_C"),
            ("--target", "target"), ("--graders", "graders"), ("--protected", "protected"),
        ):  # fmt: skip
            if key in self.inputs:
                argv += [flag, str(self.inputs[key])]
        if bench:
            for side, flag in (("B", "--baseline-cards"), ("C", "--candidate-cards")):
                argv += [flag, *map(str, bench["dirs"][side])]
            if len(bench["fake"]) == 1:
                argv += ["--fake-card", str(bench["fake"][0][0])]
        status, out = self.steps.command("decide", argv, self.work)
        try:
            result = json.loads(out)
        except ValueError as exc:
            raise Refused(
                "decide_failed", f"decide.py exited {status}: no JSON"
            ) from exc
        self.steps.write_json(self.work / "decision.json", result)
        return result

    # ── the whole cycle ──────────────────────────────────────────────────────

    def run(self) -> dict[str, Any]:
        args = self.args
        self.shas()
        self.specification()
        if args.dry_run:
            return self.plan()
        reviews = (
            (lambda: github.reviews(args.repository, args.pr)) if args.pr else list
        )
        measurable = self.graders_and_protected(reviews)
        bench = target = None
        if measurable:
            b_tree, c_tree = self.tree("B", self.b_sha), self.tree("C", self.c_sha)
            result_b = self.measure("B", b_tree)
            if result_b != "fail":
                raise Refused(
                    f"target_{'passes' if result_b == 'pass' else 'error'}_on_baseline",
                    f"the target measure must fail on B; it was {result_b!r}",
                )
            bench = self.bench(b_tree, c_tree)
            for side, sha, tree in (
                ("B", self.b_sha, b_tree),
                ("C", self.c_sha, c_tree),
            ):
                self.verifier_and_tests(side, sha, tree)
            target = {
                "measure": self.spec["target_measure"],
                "prompt_targeted": self.spec["prompt_targeted"],
                "baseline": {"sha": self.b_sha, "result": result_b},
                "candidate": {"sha": self.c_sha, "result": self.measure("C", c_tree)},
            }
            for side, key in (("B", "baseline"), ("C", "candidate")):
                target[key]["isolation"] = self.isolation.get(side)
            for side, sha in (("B", self.b_sha), ("C", self.c_sha)):
                self.check_runs(side, sha)
        else:
            self.steps.note(
                "bench_skipped",
                reason="graders differ or a protected path is unapproved",
            )
        result = self.decide(bench, target)
        decision = result["decision"]
        reasons = [r for r in result.get("reasons", []) if r.get("outcome") != "pass"]
        if args.pr:
            head, url = github.pull_head(args.repository, args.pr)
            self.steps.note(
                "pull_request_head_after", command=["GET", url], head_sha=head
            )
            if head != self.c_sha:
                decision = "unmeasured"
                reasons.append({"guard": "head", "outcome": "unmeasured", "code": "head_moved",
                                "detail": f"the head moved from {self.c_sha} to {head} (REQ-17)"})  # fmt: skip
        if self.not_sandboxed:
            decision = "unmeasured"
            reasons.append({"guard": "bench", "outcome": "unmeasured", "code": "not_sandboxed",
                            "detail": "; ".join(self.not_sandboxed)})  # fmt: skip
        if bench:
            self.record.update(gate.baseline_summary(
                [c for _, c in bench["cards"]["B"] if isinstance(c, dict)],
                [c for _, c in bench["cards"]["C"] if isinstance(c, dict)],
                args.k,
            ))  # fmt: skip
        self.record.update(status="decided", decision=decision, reasons=reasons)
        if decision != "keep" and args.close_pr and args.pr:
            status = github.close_pull(args.repository, args.pr)
            self.steps.note(
                "close_pull_request",
                command=["PATCH", f"pulls/{args.pr}"],
                status=status,
            )
        return self.record

    def plan(self) -> dict[str, Any]:
        """The steps a run would take, with no bench and no network."""
        a, w = self.args, self.work
        c = self.c_sha or f"the head of pull request #{a.pr}, read at run time"
        graders = "C unknown until run time"
        if self.c_sha:
            same = (
                steps_mod.grader_trees(self.repo, self.b_sha)["digest"]
                == (steps_mod.grader_trees(self.repo, self.c_sha)["digest"])
            )
            graders = "equal" if same else "DIFFER: the decision cannot be keep"
        lines = [
            f"B = {self.b_sha} ({a.baseline}); C = {c}",
            f"specification {a.spec} at B, target measure {self.spec['target_measure']}",
            f"REQ-20: {self.record['findings_gate']['reason']}",
            f"graders' trees on B and C ({graders}); protected paths of C's diff, from git (B's list)",
            f"clone B and C into {w / 'trees'}",
            f"grading in the sandbox only (image {a.grading_image or 'none named: unmeasured'}); never in-process",
            "target measure (B's copy) on B: must fail (exit 3)",
            *(
                f"bench {s} real {i + 1}: run.sh --out {w}/bench/{s}-real-{i + 1} --graders {self.b_sha}"
                for i in range(a.k)
                for s in ("B", "C")
            ),  # fmt: skip
            f"bench C fake: run.sh --out {w}/bench/C-fake --graders {self.b_sha} (BENCH_LLM_URL unset)",
            "Verifier bench (B's cases) and tests collected, on B and on C",
            "target measure (B's copy) on C",
            f"check-runs of B and C: GET /repos/{a.repository or '<--repository>'}/commits/<sha>/check-runs (every page)",
            "B's scripts/rsi/decide.py; with --pr, the head read again (REQ-17)",
            f"record: {a.out}"
            + (" ; --close-pr on revert/unmeasured" if a.close_pr else ""),
        ]
        self.record.update(status="dry_run", plan=lines)
        return self.record

    def finish(self) -> dict[str, Any]:
        self.record["finished_at"] = steps_mod.now()
        self.record["steps"] = self.steps.steps
        self.record["kept_files"] = self.steps.kept_files()
        out = Path(self.args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.record, indent=2, sort_keys=True) + "\n", "utf-8"
        )
        return self.record


def cmd_run(args: argparse.Namespace, runner=None) -> int:
    if args.pr is None and not args.candidate:
        raise SystemExit("give --candidate SHA or --pr N")
    if not args.dry_run and not args.repository:
        raise SystemExit("--repository OWNER/NAME is needed to read the check-runs")
    cycle = Cycle(args, runner)
    try:
        cycle.run()
    except Refused as exc:
        cycle.record.update(status="refused", decision="refused",
                            refusal={"reason": exc.reason, "detail": exc.detail})  # fmt: skip
    except (CycleError, github.GitHubError) as exc:
        cycle.record.update(status="refused", decision="refused",
                            refusal={"reason": "step_failed", "detail": str(exc)})  # fmt: skip
    record = cycle.finish()
    say(record)
    return 0 if record["status"] == "dry_run" else EXIT[record["decision"]]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="drive one cycle and write its record")
    r.add_argument("--repo", default=str(ROOT), help="the repository holding B and C")
    r.add_argument("--baseline", default="origin/main", help="B: a ref or sha of main")
    r.add_argument("--candidate", help="C: the pull request's head sha")
    r.add_argument("--pr", type=int, help="the pull request (its head is C)")
    r.add_argument("--repository", help="OWNER/NAME on GitHub, for the API")
    r.add_argument(
        "--author",
        default="",
        help="the pull request's author (their review is not an approval)",
    )
    r.add_argument(
        "--spec", required=True, help="the specification's repository path, read at B"
    )
    r.add_argument(
        "--findings", type=Path, help="GET /v1/findings saved to a file (REQ-20)"
    )
    r.add_argument(
        "--retry-by", help="the person who asks to retry a reverted specification"
    )
    r.add_argument(
        "--finding-id",
        help="the finding's id for the record (default: from --findings)",
    )
    r.add_argument("--cycle-id", required=True)
    r.add_argument(
        "--work", required=True, help="the cycle's folder: trees, logs, kept files"
    )
    r.add_argument("--out", required=True, help="the cycle record to write")
    r.add_argument(
        "--python",
        default=sys.executable,
        help="a Python with the backend's dependencies",
    )
    r.add_argument(
        "--grading-image",
        default=os.environ.get("FABRIQUE_GRADING_IMAGE"),
        help="the sandbox's image for the measure and the bench (on this machine)",
    )
    r.add_argument("--k", type=int, default=3, choices=range(1, 10), metavar="K")
    r.add_argument(
        "--close-pr",
        action="store_true",
        help="close the pull request on revert/unmeasured",
    )
    r.add_argument(
        "--dry-run", action="store_true", help="check the inputs and print the plan"
    )
    v = sub.add_parser("verify", help="hash the kept files again")
    v.add_argument("--record", required=True)
    m = sub.add_parser(
        "record", help="the record for the watch, after a person's merge"
    )
    m.add_argument("--record", required=True)
    m.add_argument("--merged-sha", required=True)
    m.add_argument("--repo", default=str(ROOT))
    m.add_argument("--out", required=True)
    return p


def main(argv: list[str] | None = None, runner=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "run":
        return cmd_run(args, runner)
    return cmd_verify(args) if args.command == "verify" else cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
