"""What the tests of the fitness decision share (``docs/design/rsi-loop.md`` §2).

``observations()`` builds a baseline and a candidate that ``fitness.decide``
keeps: three complete paired bench runs per side with equal numbers, green
CI read at each side's sha, the same tests collected, a Verifier bench with
no regression, the fake bench at 30/30, equal graders, no protected path and
a target measure that fails on B and passes on C. Each negative test breaks
one thing. The tests are ``test_rsi_fitness.py``, ``test_rsi_fitness_keep.py``,
``test_rsi_fitness_inputs.py`` and the ``test_rsi_decide*.py`` files.
"""

import copy

from services.evidence import fitness

B_SHA = "b" * 40
C_SHA = "c" * 40
MODELS = {"Developer": ["model-a"], "QA": ["model-b"]}
TOTALS = {
    "criteria_rate": 0.9,
    "first_attempt_rate": 0.8,
    "blocked_share": 0.1,
    "rework_per_task": 0.5,
    "tokens_reported": 100_000,
    "wall_clock_seconds": 1_000.0,
    "prompt_tokens_mean": 2_000.0,
}


def run(sha, pair, **totals):
    """One complete bench run, its totals and one specification equal."""
    row = {**TOTALS, **totals}
    return {
        "produced_by": fitness.PLATFORM,
        "sha": sha,
        "pair": pair,
        "status": fitness.COMPLETE,
        "models": copy.deepcopy(MODELS),
        "totals": dict(row),
        "specs": {"01-a": dict(row)},
    }


def check_run(sha, name, conclusion="success", run_id=1, app="github-actions"):
    return {
        "id": run_id,
        "name": name,
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "app": app,
    }


def ci(sha, *runs, tests=None):
    return {
        "produced_by": fitness.PLATFORM,
        "sha": sha,
        "check_runs": list(runs)
        or [check_run(sha, "backend-tests"), check_run(sha, "frontend-build")],
        "tests_collected": {
            "produced_by": fitness.PLATFORM,
            "sha": sha,
            "suites": {"backend": 4000, "frontend": 900} if tests is None else tests,
        },
    }


def side(sha, *, fake=False):
    obs = {
        "sha": sha,
        "bench": [run(sha, pair) for pair in range(fitness.K)],
        "ci": ci(sha),
        "verifier_bench": {
            "produced_by": fitness.PLATFORM,
            "sha": sha,
            "cases": {"planted-1": "pass", "control-1": "pass"},
        },
    }
    if fake:
        obs["fake_bench"] = {
            "produced_by": fitness.PLATFORM,
            "sha": sha,
            "criteria_passed": 30,
            "criteria_total": 30,
        }
    return obs


def observations():
    """The keyword arguments of ``fitness.decide`` for a candidate it keeps."""
    return {
        "baseline": side(B_SHA),
        "candidate": side(C_SHA, fake=True),
        "graders": {
            "produced_by": fitness.PLATFORM,
            "baseline_tree": "t" * 40,
            "candidate_tree": "t" * 40,
        },
        "target": {
            "produced_by": fitness.PLATFORM,
            "measure": "f4-adr-files",
            "baseline": {"sha": B_SHA, "result": "fail"},
            "candidate": {"sha": C_SHA, "result": "pass"},
        },
        "protected": {"produced_by": fitness.PLATFORM, "sha": C_SHA, "touched": []},
    }


def decide(obs, **kwargs):
    return fitness.decide(
        obs["baseline"],
        obs["candidate"],
        graders=obs["graders"],
        target=obs["target"],
        protected=obs["protected"],
        **kwargs,
    )


def codes(result, outcome=None):
    """The reason codes of *result*, of one outcome when given."""
    return {
        r["code"]
        for r in result["reasons"]
        if outcome is None or r["outcome"] == outcome
    }
