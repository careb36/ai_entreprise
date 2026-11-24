"""The programs a project declares, their typed parameters and the metrics read.

``services/contracts/programs.py`` and ``program_metrics.py``
(``docs/design/project-programs-and-screenshots.md``, slice 1): the model
never writes command text, each parameter is typed and bounded, and only the
platform reads a metric. Checked again when the stack profile is saved
(``workspace/stack_roots.validate_overrides``).
"""

from __future__ import annotations

import math

import pytest
from services.contracts import program_metrics, programs
from services.workspace import stack_profiles, stack_roots

EVALUATE = {
    "name": "evaluate",
    "kind": "evaluation",
    "folder": "ml",
    "entry": "ml/evaluate.py",
    "argv": ["python3", "-m", "ml.evaluate"],
    "params": {
        "split": {"type": "enum", "values": ["val", "test"], "default": "val"},
        "epochs": {"type": "int", "min": 1, "max": 50, "default": 5},
        "lr": {"type": "float", "min": 1.0e-6, "max": 1.0},
        "fast": {"type": "bool", "default": False},
        "tag": {"type": "string", "pattern": "[a-z0-9-]{1,20}", "default": "run"},
    },
    "metrics": {
        "file": "metrics.json",
        "tolerance": 0.01,
        "thresholds": {"accuracy": {"min": 0.8}},
    },
}

# Every shell metacharacter the design lists, each in a value of every type.
METACHARACTERS = [
    ";",
    "|",
    "&",
    "$",
    "`",
    "(",
    ")",
    "<",
    ">",
    "*",
    "?",
    "[",
    "'",
    '"',
    "\\",
    "\n",
    " ",
]


def _program():
    return programs.validate_program(EVALUATE)


def test_a_declaration_renders_one_word_per_parameter():
    words, values = programs.render(_program(), {"lr": 0.001, "split": "test"})
    assert words == [
        "python3",
        "-m",
        "ml.evaluate",
        "--split=test",
        "--epochs=5",
        "--lr=0.001",
        "--fast=false",
        "--tag=run",
    ]
    assert values["lr"] == 0.001 and values["split"] == "test"
    assert all(programs.plain_word(w) for w in words)


@pytest.mark.parametrize("char", METACHARACTERS)
@pytest.mark.parametrize("param", ["split", "epochs", "lr", "fast", "tag"])
def test_a_shell_metacharacter_in_any_parameter_is_refused(param, char):
    value = f"val{char}id" if param != "lr" else f"0.1{char}1"
    with pytest.raises(ValueError):
        programs.render(_program(), {"lr": 0.01, param: value})


@pytest.mark.parametrize(
    ("given", "says"),
    [
        ({"lr": 2.0}, "out of bounds"),
        ({"lr": 0.01, "epochs": 0}, "out of bounds"),
        ({"lr": 0.01, "epochs": 51}, "out of bounds"),
        ({"lr": math.nan}, "finite"),
        ({"lr": math.inf}, "finite"),
        ({"lr": 0.01, "epochs": 2.5}, "int"),
        ({"lr": 0.01, "epochs": True}, "int"),
        ({"lr": 0.01, "split": "train"}, "one of val, test"),
        ({"lr": 0.01, "fast": "yes"}, "true or false"),
        ({"lr": 0.01, "tag": "x" * 30}, "at most"),
        ({"lr": 0.01, "seed": 1}, "unknown parameter 'seed'"),
        ({}, "lr is required"),
    ],
)
def test_out_of_bounds_unknown_and_missing_parameters_are_refused(given, says):
    with pytest.raises(ValueError, match=says):
        programs.render(_program(), given)


@pytest.mark.parametrize(
    "argv",
    [
        ["python3", "x.py;", "rm"],
        ["python3", "a b"],
        ["python3", "../x.py"],
        ["/usr/bin/python3"],
        ["python3", "$(id)"],
    ],
)
def test_free_command_text_is_refused_in_argv(argv):
    with pytest.raises(ValueError, match="argv word"):
        programs.validate_program({**EVALUATE, "argv": argv})


def test_a_command_string_instead_of_words_is_refused():
    with pytest.raises(ValueError, match="argv must list"):
        programs.validate_program(
            {**EVALUATE, "argv": "python3 -m ml.evaluate; curl x"}
        )


@pytest.mark.parametrize(
    ("field", "value", "says"),
    [
        ("kind", "notebook", "kind must be one of"),
        ("code", "task", "code must be committed"),
        ("entry", "../evil.py", "entry must be a relative path"),
        ("name", "Bad Name", "name must match"),
        ("limits", {"seconds": 10**6}, "limits.seconds"),
        ("metrics", {"line_prefix": "$(id)"}, "line_prefix"),
        ("command", "python3 x.py", "unknown program field"),
    ],
)
def test_a_bad_declaration_is_refused(field, value, says):
    with pytest.raises((ValueError, TypeError), match=says):
        programs.validate_program({**EVALUATE, field: value})


def test_the_stack_profile_save_validates_programs():
    saved = stack_roots.validate_overrides({"test": "pytest", "programs": [EVALUATE]})
    assert saved["programs"][0]["name"] == "evaluate"
    with pytest.raises(ValueError, match="out of bounds"):
        stack_roots.validate_overrides(
            {
                "programs": [
                    {
                        **EVALUATE,
                        "params": {
                            "n": {"type": "int", "min": 1, "max": 3, "default": 9}
                        },
                    }
                ]
            }
        )
    with pytest.raises(ValueError, match="unique"):
        stack_roots.validate_overrides({"programs": [EVALUATE, EVALUATE]})


def test_the_resolved_profile_carries_only_programs_a_person_declared():
    resolved = stack_profiles.resolve(
        {"stack": "python", "programs": ["detected"]}, {"programs": [EVALUATE]}
    )
    assert [p["name"] for p in resolved["programs"]] == ["evaluate"]
    assert stack_profiles.resolve({"stack": "python"}, None)["programs"] == []
    # A stored row that no longer passes declares nothing and stops no run.
    assert stack_profiles.resolve(None, {"programs": [{"name": "x"}]})["programs"] == []


# ---------------------------------------------------------------------------
# Metrics: the platform's reading, and the agent's claims
# ---------------------------------------------------------------------------


def test_metric_lines_are_read_with_the_prefix_and_finite_values_only():
    lines = [
        'fabrique-metric: {"name": "accuracy", "value": 0.7, "split": "val"}',
        "accuracy 0.99",
        'fabrique-metric: {"name": "loss", "value": NaN, "split": "train"}',
        'fabrique-metric: {"name": "f1", "value": "0.9"}',
        "fabrique-metric: not json",
    ]
    stdout = "\n".join(lines)
    parsed = program_metrics.parse_lines(stdout, programs.DEFAULT_PREFIX)
    assert [(m["name"], m["value"], m["line"]) for m in parsed["metrics"]] == [
        ("accuracy", 0.7, 1)
    ]
    assert len(parsed["dropped"]) == 3 and not parsed["metrics_truncated"]


def test_more_than_500_metric_lines_marks_the_record_truncated():
    line = 'fabrique-metric: {"name": "loss", "value": 1, "split": "train"}'
    parsed = program_metrics.parse_lines(
        "\n".join([line] * 501), programs.DEFAULT_PREFIX
    )
    assert len(parsed["metrics"]) == 500 and parsed["metrics_truncated"] is True


def test_a_metrics_file_that_fails_the_schema_gives_no_metrics_and_ignores_a_sha256():
    good = b'{"metrics": [{"name": "accuracy", "value": 0.7, "split": "val"}], "sha256": "ab"}'
    parsed = program_metrics.parse_file(good, "metrics.json")
    assert parsed["metrics"] == [
        {"name": "accuracy", "value": 0.7, "split": "val", "source": "file"}
    ]
    bad = program_metrics.parse_file(
        b'{"metrics": [{"name": "a", "value": "x"}]}', "metrics.json"
    )
    assert bad["metrics"] == [] and "fails the schema" in bad["reason"]


def test_claims_come_from_claimed_metric_lines():
    text = 'Done.\nclaimed-metric: {"name": "accuracy", "value": 0.99, "split": "val"}\naccuracy is 0.99'
    assert program_metrics.parse_claims(text) == [
        {"name": "accuracy", "value": 0.99, "split": "val"}
    ]


@pytest.mark.parametrize(
    ("source", "tier", "ok"),
    [
        ("program_output", "low", False),
        ("committed_evaluator", "low", True),
        ("committed_evaluator", "medium", True),
        ("committed_evaluator", "high", False),
        ("platform_scorer", "high", True),
    ],
)
def test_the_tier_rule_of_decision_2(source, tier, ok):
    assert program_metrics.supports_quality(source, tier)[0] is ok
