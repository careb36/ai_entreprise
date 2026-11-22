"""A routine's variables (``services/contracts/routine_variables.py``):
declared inputs, values over defaults, and the text filled in one pass."""

import pytest
from services.contracts import routine_variables as rv

DECLARED = rv.validate(
    [
        {"name": "scope", "default": "backend", "options": ["backend", "frontend"]},
        {"name": "ticket", "required": True},
        {"name": "note"},
    ]
)


def test_values_over_defaults_and_the_text_filled():
    values = rv.resolve(DECLARED, {"ticket": "#42"})
    assert values == {"scope": "backend", "ticket": "#42", "note": ""}
    assert rv.render("Audit {{scope}} for {{ ticket }}.", values) == (
        "Audit backend for #42."
    )


def test_a_value_is_inserted_once_never_expanded_again():
    values = rv.resolve(DECLARED, {"ticket": "{{scope}}"})
    assert rv.render("{{ticket}}", values) == "{{scope}}"


@pytest.mark.parametrize(
    "given",
    [
        {},  # ticket is required
        {"ticket": "#1", "scope": "mobile"},  # not an option
        {"ticket": "#1", "extra": "x"},  # not declared
        {"ticket": "x" * (rv.VALUE_MAX + 1)},
        {"ticket": 5},
    ],
)
def test_values_the_backend_refuses(given):
    with pytest.raises(rv.InvalidVariables):
        rv.resolve(DECLARED, given)


@pytest.mark.parametrize(
    "raw",
    [
        "scope",
        [{"name": "Scope"}],
        [{"name": "1st"}],
        [{"name": "a"}, {"name": "a"}],
        [{"name": "a", "default": "z", "options": ["x", "y"]}],
        [{"name": "a", "required": "yes"}],
        [{"name": "a", "type": "number"}],
        [{"name": f"v{i}"} for i in range(rv.MAX_VARIABLES + 1)],
    ],
)
def test_declarations_the_backend_refuses(raw):
    with pytest.raises(rv.InvalidVariables):
        rv.validate(raw)


def test_an_undeclared_placeholder_is_refused():
    rv.check_placeholders("Audit {{scope}}", DECLARED)
    with pytest.raises(rv.InvalidVariables, match="undeclared variable"):
        rv.check_placeholders("Audit {{scope}} in {{region}}", DECLARED)
