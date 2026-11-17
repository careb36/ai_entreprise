"""What follows a cycle's run (``scripts/rsi/cycle.py``): its message, ``verify`` and ``record``.

``say`` prints the decision: on ``keep`` that the pull request is eligible
for a person's merge pinned to C's sha (REQ-17), never a merge; on
``revert`` or ``unmeasured`` that it is not to be merged, with the reasons
(REQ-18). ``cmd_verify`` hashes the kept files again. ``cmd_record`` turns a
``keep`` record into the one ``scripts/rsi/watch.py`` reads, once a person
merged: ``merged_sha`` must contain C, ``merged_at`` is git's committer date
of that commit, the kept files must be unchanged, and the result must pass
``cycle_record.validate``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cycle_record
import cycle_steps as steps_mod
from cycle_steps import CycleError, git


def say(record: dict[str, Any]) -> None:
    decision, pr = record.get("decision"), record.get("pull_request")
    if record.get("status") == "dry_run":
        print("Dry run: no bench, no network. The plan:")
        print("\n".join(f"  {i + 1}. {line}" for i, line in enumerate(record["plan"])))
        return
    if record.get("status") == "refused":
        print(f"Refused ({record['refusal']['reason']}): {record['refusal']['detail']}")
        return
    print(f"Decision: {decision}")
    for reason in record.get("reasons", []):
        print(
            f"  - {reason['guard']} {reason['outcome']} {reason['code']}: {reason['detail']}"
        )
    if decision == "keep":
        print(
            f"Pull request {f'#{pr} ' if pr else ''}is eligible for a person's merge, "
            f"pinned to C's sha {record['candidate_sha']} (REQ-17). This script does not merge."
        )
    else:
        print(
            f"{decision}: the pull request is not to be merged; close it unmerged (REQ-18)."
        )


def _load(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: not a cycle record")
    return data


def cmd_verify(args: argparse.Namespace) -> int:
    record = _load(args.record)
    problems = steps_mod.verify_kept(
        Path(record.get("work_dir", ".")), record.get("kept_files", [])
    )
    print(
        "\n".join(problems)
        or f"{len(record.get('kept_files', []))} kept files unchanged"
    )
    return 1 if problems else 0


def cmd_record(args: argparse.Namespace) -> int:
    """The record the watch reads, once a person merged C (REQ-17, REQ-19)."""
    record = _load(args.record)
    repo = Path(args.repo).resolve()
    problems = []
    if record.get("decision") != "keep":
        problems.append(f"the cycle decided {record.get('decision')!r}, not keep")
    problems += steps_mod.verify_kept(
        Path(record.get("work_dir", ".")), record.get("kept_files", [])
    )
    try:
        merged = steps_mod.resolve(repo, args.merged_sha)
        merged_at = git(repo, "show", "-s", "--format=%cI", merged).strip()
    except CycleError as exc:
        raise SystemExit(str(exc)) from exc
    if not steps_mod.is_ancestor(repo, str(record.get("candidate_sha")), merged):
        problems.append(
            f"C {record.get('candidate_sha')} is not in the merged commit {merged}"
        )
    final = {**record, "merged_sha": merged, "merged_at": merged_at}
    problems += cycle_record.validate(final)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    print(f"{args.out}: a person commits it ({cycle_record.CYCLES_DIR} is protected)")
    return 0
