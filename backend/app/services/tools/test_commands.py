"""What ``workspace__run_tests`` runs for the command a model asked (issue #766).

An audit of 108 runs (2026-11-24) found that all 20 calls asking for a
narrower test command ran the folder's whole suite instead (4 to 9 minutes,
the output cut). The folder's command was ``PYTHONPATH=.:app poetry run python
-m pytest -q``: its leading environment assignment was compared as if it were
the runner, so even that exact prefix with a narrower path was refused. The
models also wrote ``cd backend && ...``, ``| tail -n 40`` and ``; echo
EXIT=$?``. Now:

- leading ``VAR=value`` assignments are set aside on both sides before the
  wrapper (``poetry run``...) and the runner are compared. The narrowed command
  gets the folder command's own assignments back, never the requested ones:
  an assignment the model wrote (``PATH=...``, ``PYTHONPATH=...``) could change
  what runs, so it is dropped and the reply says so;
- a leading ``cd <folder> &&`` is dropped when ``<folder>`` is the folder the
  tests run in (or, with no ``folder`` argument, names one that has a test
  command): the tool already runs there. Any other folder is refused;
- a missing wrapper is put back (the tests run in the folder's environment),
  and ``pytest`` / ``python -m pytest`` / ``python3 -m pytest`` stand for one
  another: the folder's own spelling runs;
- a trailing ``| tail -n N``, ``2>&1`` or ``; echo ...$?`` is dropped, not
  refused. Each only changes what is printed: the reply already carries the
  exit status and the output's end, and refusing would run the whole suite,
  the very cost the audit found. Only those exact shapes are dropped; what is
  left must still hold no shell operator (``; & | ` $ > <``, a newline), so a
  second command never runs.

Every change is said before the output (``developer_tool_replies.tests_text``).
Anything else keeps the folder's command, with the reason when there is one.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from services.tools import test_runs

# The command the model may narrow the test run to must start with the
# project's own runner: never an arbitrary shell command.
RUNNERS = (
    "python -m pytest",
    "pytest",
    "npm test",
    "npx vitest",
    "npx jest",
    "go test",
    "cargo test",
)
# Spellings of one runner: the folder's own is the one that runs.
PYTEST = ("python -m pytest", "python3 -m pytest", "pytest")

# What may run a runner inside the project's environment: a narrower command
# keeps the same one (``poetry run python -m pytest`` stays under Poetry).
WRAPPERS = ("poetry run ", "pipenv run ", "uv run ")

SHELL_OPERATORS = ";&|`$><\n"
_ENV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*=[^ ]*) +")
_CD = re.compile(r"cd +([A-Za-z0-9_./-]+) *&& *(.+)", re.DOTALL)
# Trailing output plumbing, dropped (see the module's docstring).
_NOISE = (
    re.compile(r" *\| *tail(?: +-n)? +-?[0-9]{1,6}$"),
    re.compile(r" *(?:;|&&|\|\|) *echo +[\"']?[A-Za-z0-9_ =:.-]*\$\?[\"']?$"),
    re.compile(r" +2>&1$"),
)
_DROPPED = "was dropped (this reply gives the exit status and the output's end)"


def runs_with(command: str, runner: str) -> bool:
    """Whether *command* is *runner* itself or *runner* followed by arguments:
    ``python -m pytest_custom`` is not ``python -m pytest``."""
    return command == runner or command.startswith(runner + " ")


def strip_noise(command: str) -> tuple[str, list[str]]:
    """*command* without its trailing ``| tail``, ``2>&1`` or ``; echo $?``."""
    notes: list[str] = []
    changed = True
    while changed:
        changed = False
        for pattern in _NOISE:
            found = pattern.search(command)
            if found and found.start() > 0:
                notes.insert(0, f"`{found.group(0).strip()}` {_DROPPED}")
                command, changed = command[: found.start()].rstrip(" "), True
    return command, notes


def split_cd(command: str) -> tuple[str | None, str]:
    """``(folder, rest)`` for ``cd <folder> && rest``, else ``(None, command)``."""
    found = _CD.fullmatch(command)
    if not found:
        return None, command
    folder = posixpath.normpath(found.group(1)).strip("/")
    return ("" if folder == "." else folder), found.group(2)


def split_env(command: str) -> tuple[list[str], str]:
    """The leading ``VAR=value`` words and the command after them."""
    env: list[str] = []
    while found := _ENV.match(command):
        env.append(found.group(1))
        command = command[found.end() :]
    return env, command


def narrow(
    requested: Any, base: str | None, folder: str = ""
) -> tuple[str, list[str], str | None]:
    """``(command, notes, why)``: the narrower command and what was changed to
    run it, or the folder's *base* command and why the request was refused
    (``None`` when the general reason applies)."""
    base = str(base or "")
    asked = str(requested or "").strip()
    if not asked:
        return base, [], None
    wanted, notes = strip_noise(asked)
    cd, wanted = split_cd(wanted)
    if cd is not None:
        if cd != folder:
            where = f"`{folder}`" if folder else "the project root"
            return (
                base,
                [],
                (
                    f"`cd {cd}` is not the folder the tests run in ({where}); name the "
                    "folder with the tool's `folder` argument"
                ),
            )
        notes.append(f"`cd {cd} &&` was dropped: the tests run in `{cd}` already")
    if any(ch in wanted for ch in SHELL_OPERATORS):
        return (
            base,
            [],
            (
                "it holds a shell operator (; & | ` $ > < or a newline) beyond a "
                "leading `cd <folder> &&` or a trailing `| tail -n N` / `; echo $?`"
            ),
        )
    base_env, base_command = split_env(base)
    wanted_env, wanted = split_env(wanted)
    wrapper = next((w for w in WRAPPERS if base_command.startswith(w)), "")
    runner = next(
        (r for r in RUNNERS if runs_with(base_command[len(wrapper) :], r)), None
    )
    own = next((w for w in WRAPPERS if wanted.startswith(w)), "")
    if runner is None or own not in ("", wrapper):
        return base, [], None
    inner = wanted[len(own) :]
    spelled = next(
        (r for r in PYTEST if runner in PYTEST and runs_with(inner, r)),
        runner if runs_with(inner, runner) else None,
    )
    if spelled is None:
        return base, [], None
    if wanted_env != base_env:
        if wanted_env:
            notes.append(
                f"the environment assignment(s) `{' '.join(wanted_env)}` asked "
                "for were not applied"
            )
        if base_env:
            notes.append(f"the folder's own `{' '.join(base_env)}` was applied")
    if wrapper and not own:
        notes.append(f"it runs under the folder's `{wrapper.strip()}`")
    if spelled != runner:
        notes.append(f"`{spelled}` runs as the folder's `{runner}`")
    if not notes:  # Nothing changed: the command exactly as asked.
        return asked, [], None
    rest = inner[len(spelled) :]
    return " ".join([*base_env, f"{wrapper}{runner}{rest}"]), notes, None


def resolve(
    plans: dict[str, dict[str, str | None]], arguments: dict[str, Any]
) -> tuple[str, dict[str, str | None], str, list[str], str | None]:
    """``(folder, plan, command, notes, why)`` for a ``run_tests`` call. With
    no ``folder`` argument, a leading ``cd <folder> &&`` naming a folder with
    a test command picks that folder."""
    asked_folder, command = arguments.get("folder"), arguments.get("command")
    if not str(asked_folder or "").strip():
        cd, _rest = split_cd(strip_noise(str(command or "").strip())[0])
        if cd and cd in plans:
            asked_folder = cd
    folder, plan = test_runs.target(plans, asked_folder)
    command, notes, why = narrow(command, plan.get("test"), folder)
    return folder, plan, command, notes, why
