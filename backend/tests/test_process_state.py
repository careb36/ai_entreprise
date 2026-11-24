"""What a module of ``services`` keeps between calls, held to the fixture.

``sqlite_session`` empties the database after every test, so the ids start
again, and a container a module keeps at its top level (by a task's, sprint's
or company's id, or any key a later test reuses) leaks from one test into the
next unless ``conftest.forget_process_state`` empties it. Every empty
container or cache a module of ``backend/app/services`` creates at its top
level is in ``conftest.PROCESS_STATE`` or in ``SAFE`` below, with the reason.
Also ``conftest.when_loaded``, by which a fixture acts on a module only once
the test has loaded it.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import conftest
from conftest import PROCESS_STATE, empty_process_state, when_loaded

APP = Path(__file__).resolve().parents[1] / "app"
CONTAINERS = {"dict", "list", "set", "OrderedDict", "defaultdict", "deque"}
CONTAINERS |= {"WeakKeyDictionary", "WeakValueDictionary", "WeakSet"}
CACHES = {"lru_cache", "cache"}

# What a module keeps that no later test can read as its own.
SAFE = {
    "services.contracts.tool_steps.ROLE_DENY": "a constant table of the code; tests change it only through monkeypatch",
    "services.evidence.protected_check.patterns_in_force": "the backend's own protected list and test map, shipped with the code",
    "services.platform.hooks._HANDLERS": "handlers installed once a process, by event name",
    "services.platform.wstg_catalogue.catalogue": "a cache of a file shipped with the code",
    "services.platform.run_window_setting._calendar_files": "a cache of the holiday calendars shipped with the code",
    "services.platform.wstg_catalogue._by_short": "a cache of a file shipped with the code",
    "services.release.integration._SLOTS": "weak, by event loop: gone with the test's loop",
    "services.release.integration._LOCK_ENGINES": "an engine per database URL, holds no row",
    "services.tools.code_graph._SYNCS": "a running background sync, removed by its own thread when it ends; a test that starts one waits for it (wait_for_persists)",
}


def _name(node: ast.AST) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    return getattr(target, "id", "")


def _kept(tree: ast.Module) -> list[str]:
    """The names a module's top level binds to an empty container or a cache."""
    found = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            value = node.value
            empty_literal = isinstance(value, (ast.Dict, ast.List, ast.Set)) and not (
                getattr(value, "keys", None) or getattr(value, "elts", None)
            )
            empty_call = (
                isinstance(value, ast.Call)
                and _name(value) in CONTAINERS
                and not (_name(value) in {"dict", "list", "set"} and value.args)
            )
            if empty_literal or empty_call:
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                found += [t.id for t in targets if isinstance(t, ast.Name)]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_name(d) in CACHES for d in node.decorator_list):
                found.append(node.name)
    return found


def _services_state() -> set[str]:
    out = set()
    for path in sorted((APP / "services").rglob("*.py")):
        module = ".".join(path.relative_to(APP).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out |= {f"{module.removesuffix('.__init__')}.{name}" for name in _kept(tree)}
    return out


def test_every_module_level_container_of_services_is_cleared_or_safe():
    unknown = sorted(_services_state() - set(PROCESS_STATE) - set(SAFE))
    assert not unknown, (
        "Add each to conftest.PROCESS_STATE (emptied after every test) or, "
        f"if no later test can read it as its own, to SAFE with why: {unknown}"
    )


def test_every_listed_container_exists():
    for dotted in [*PROCESS_STATE, *SAFE]:
        name, attr = dotted.rsplit(".", 1)
        assert hasattr(importlib.import_module(name), attr), dotted
    assert not set(PROCESS_STATE) & set(SAFE)


def test_the_fixture_empties_what_a_loaded_module_keeps():
    from services.platform import locks

    locks._local_held.add(1)
    empty_process_state()
    assert locks._local_held == set()


def test_when_loaded_acts_now_or_right_after_a_later_import(tmp_path, monkeypatch):
    (tmp_path / "process_state_probe.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "process_state_probe", raising=False)
    seen = []

    when_loaded("process_state_probe", lambda module: seen.append(module.VALUE))
    assert seen == []
    import process_state_probe

    assert seen == [1]
    when_loaded("process_state_probe", lambda module: seen.append(2))
    assert seen == [1, 2]
    del sys.modules[process_state_probe.__name__]


def test_hooks_loaded_while_integration_jobs_is_still_importing(monkeypatch):
    # A test whose first import is integration_jobs loads hooks from inside
    # it: the after-import action then finds the jobs module in sys.modules
    # without install() yet, and must register the stand-in, not call it.
    import types

    calls = []

    class Hooks:
        SPRINT_CLOSED = "sprint.closed"

        def on(self, event, handler):
            calls.append(("on", handler))

        def off(self, event, handler):
            calls.append(("off", handler))

    half = types.ModuleType("services.release.integration_jobs")
    monkeypatch.setitem(sys.modules, "services.release.integration_jobs", half)
    conftest._listen_for_closed_sprints(Hooks())
    assert calls == [("on", conftest._integrate_after_close)]
