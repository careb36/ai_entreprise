"""Which commands the daemon agrees to run: allow and deny lists (issue #357).

The daemon is the only component that runs commands on the person's machine,
so it applies the policy itself, before ``workspace.execute`` runs anything:

- the company's policy travels in the request as ``command_policy``
  (``{"mode": "allow" | "deny", "allow": [...], "deny": [...]}``), the way
  ``sandbox`` does. ``allow`` runs only the listed commands; ``deny`` runs
  everything but them. A policy the daemon cannot read is refused, never
  ignored.
- the person running the daemon may keep a deny list of their own,
  ``FABRIQUE_COMMAND_DENY`` (comma-separated patterns), which the backend
  cannot relax: it applies whatever the request says.
- with neither, every command runs as before.

How a command is read. A shell command is not one command: it is simple
commands joined by ``;``, ``&&``, ``||``, ``|``, ``&``, newlines and
parentheses, with command substitutions (``$(...)``, backticks, ``<(...)``)
that are commands too, and redirections that read or write files. The
scanner below splits all of that, honouring quotes and escapes (a single
quote keeps ``$(`` and backticks literal, a double quote does not), and
every part must pass:

- allow: each simple command's words must start with the words of a listed
  pattern, compared word by word (``git status`` allows ``git status
  --short``, never ``git push`` nor ``git statusx``). A redirection is
  refused unless it duplicates a stream (``2>&1``) or targets ``/dev/null``:
  an allow list names commands, not the files they may overwrite.
- deny: a pattern found in the command's text, or in a simple command's
  words once quotes are removed (``su""do`` is ``sudo``), refuses it.

Substitutions are checked as commands of their own, each strictly shorter
than the text it came from, and no deeper than ``MAX_DEPTH``. Text the
scanner cannot read (an unclosed quote or parenthesis) is refused.

The scanner reads POSIX ``sh`` syntax and fails closed on what it does not
model. While a policy is active, a command is refused when it carries a
here-document or here-string (``<<``), Bash ANSI-C or locale quotes
(``$'…'``, ``$"…"``), a comment (a ``#`` starting a word), or a backslash
inside a backtick body (its escapes are decoded again before running); and
on Windows, where ``cmd.exe`` and PowerShell quote differently, a policy
refuses every command rather than read it with the wrong rules.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

MODES = ("allow", "deny")
MAX_DEPTH = 8
LOCAL_DENY_ENV = "FABRIQUE_COMMAND_DENY"

SUGGESTED_DENY = [
    "rm -rf /",
    "curl | sh",
    "wget | sh",
    "sudo",
    "chmod 777",
    "git push --force",
    "git remote set-url",
]

_OPERATORS = ("&&", "||", "|&", ";;", ";", "&", "|", "\n", "(", ")")
_REDIRECTS = ("&>>", "&>", ">>", ">&", ">|", "<<<", "<<-", "<<", "<&", "<>", ">", "<")
_STREAM_DUP = re.compile(r"(?:[0-9]+-?|-)")
_STREAM_NUMBER = re.compile(r"[0-9]+")


class _Unreadable(ValueError):
    """Shell text the scanner cannot split with confidence."""


@dataclass
class _Simple:
    words: list[str] = field(default_factory=list)
    redirects: list[tuple[str, str]] = field(default_factory=list)


def _closing_backtick(text: str, start: int) -> int:
    j = start
    while j < len(text):
        if text[j] == "\\":
            # The shell decodes a backtick body's escapes before parsing it.
            raise _Unreadable("a backslash inside backticks; use $(...)")
        if text[j] == "`":
            return j
        j += 1
    raise _Unreadable("unclosed backtick")


def _closing_double(text: str, start: int, depth: int) -> int:
    j = start
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
        elif ch == '"':
            return j
        elif text.startswith("$(", j):
            j = _closing_paren(text, j + 1, depth + 1) + 1
        elif ch == "`":
            j = _closing_backtick(text, j + 1) + 1
        else:
            j += 1
    raise _Unreadable("unclosed double quote")


def _closing_paren(text: str, open_index: int, depth: int = 0) -> int:
    """Index of the ``)`` closing the ``(`` at ``open_index``, skipping quotes."""
    if depth > 4 * MAX_DEPTH:
        raise _Unreadable("nested too deeply")
    level = 0
    j = open_index
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "'":
            end = text.find("'", j + 1)
            if end < 0:
                raise _Unreadable("unclosed single quote")
            j = end + 1
            continue
        if ch == '"':
            j = _closing_double(text, j + 1, depth) + 1
            continue
        if ch == "`":
            j = _closing_backtick(text, j + 1) + 1
            continue
        if ch == "#" and (j == open_index + 1 or text[j - 1] in " \t\n;&|()"):
            raise _Unreadable("a comment inside a command substitution")
        if ch == "(":
            level += 1
        elif ch == ")":
            level -= 1
            if level == 0:
                return j
        j += 1
    raise _Unreadable("unclosed parenthesis")


class _Scanner:
    """Splits shell text into simple commands, redirections and substitutions."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.commands = [_Simple()]
        self.substitutions: list[str] = []
        self.word: list[str] = []
        self.in_word = False
        self.redirect: str | None = None

    def _add(self, chunk: str) -> None:
        self.word.append(chunk)
        self.in_word = True

    def _end_word(self) -> None:
        if not self.in_word:
            return
        word = "".join(self.word)
        self.word, self.in_word = [], False
        if self.redirect:
            self.commands[-1].redirects.append((self.redirect, word))
            self.redirect = None
        else:
            self.commands[-1].words.append(word)

    def _end_command(self) -> None:
        if self.redirect:
            raise _Unreadable(f"redirection {self.redirect} without a target")
        self.commands.append(_Simple())

    def _substitution(self) -> bool:
        t, i = self.text, self.i
        if t.startswith("$(", i) or (t[i] in "<>" and t.startswith("(", i + 1)):
            end = _closing_paren(t, i + 1)
            self.substitutions.append(t[i + 2 : end])
        elif t[i] == "`":
            end = _closing_backtick(t, i + 1)
            self.substitutions.append(t[i + 1 : end].replace("\\`", "`"))
        else:
            return False
        self._add(t[i : end + 1])
        self.i = end + 1
        return True

    def _double_quoted(self) -> None:
        t, j, parts = self.text, self.i + 1, []
        while True:
            if j >= len(t):
                raise _Unreadable("unclosed double quote")
            ch = t[j]
            if ch == '"':
                break
            if ch == "\\" and j + 1 < len(t):
                if t[j + 1] == "\n":
                    pass  # a line continuation: gone before the shell runs it
                elif t[j + 1] in '$`"\\':
                    parts.append(t[j + 1])
                else:
                    parts.append(t[j : j + 2])
                j += 2
            elif t.startswith("$(", j):
                end = _closing_paren(t, j + 1)
                self.substitutions.append(t[j + 2 : end])
                parts.append(t[j : end + 1])
                j = end + 1
            elif ch == "`":
                end = _closing_backtick(t, j + 1)
                self.substitutions.append(t[j + 1 : end].replace("\\`", "`"))
                parts.append(t[j : end + 1])
                j = end + 1
            else:
                parts.append(ch)
                j += 1
        self._add("".join(parts))
        self.i = j + 1

    def _redirection(self) -> bool:
        t, i = self.text, self.i
        if t[i] not in "<>" and not t.startswith("&>", i):
            return False
        op = next(o for o in _REDIRECTS if t.startswith(o, i))
        if (
            t[i] in "<>"
            and self.in_word
            and _STREAM_NUMBER.fullmatch("".join(self.word))
        ):
            self.word, self.in_word = [], False  # the stream number, as in 2>
        else:
            self._end_word()
        if self.redirect:
            raise _Unreadable(f"redirection {self.redirect} without a target")
        self.redirect = op
        self.i += len(op)
        return True

    def parse(self) -> tuple[list[_Simple], list[str]]:
        t = self.text
        while self.i < len(t):
            c = t[self.i]
            if c == "\\":
                if t[self.i + 1 : self.i + 2] not in ("", "\n"):
                    self._add(t[self.i + 1])
                self.i += 2
            elif c == "'":
                end = t.find("'", self.i + 1)
                if end < 0:
                    raise _Unreadable("unclosed single quote")
                self._add(t[self.i + 1 : end])
                self.i = end + 1
            elif c == '"':
                self._double_quoted()
            elif self._substitution() or self._redirection():
                continue
            elif c in " \t\r":
                self._end_word()
                self.i += 1
            else:
                op = next((o for o in _OPERATORS if t.startswith(o, self.i)), None)
                if op is None:
                    self._add(c)
                    self.i += 1
                    continue
                self._end_word()
                self._end_command()
                self.i += len(op)
        self._end_word()
        self._end_command()
        commands = [c for c in self.commands if c.words or c.redirects]
        return commands, self.substitutions


def _pattern_words(pattern: str) -> list[str]:
    try:
        words = shlex.split(pattern)
    except ValueError:
        words = pattern.split()
    return words


def _allowed(words: list[str], allow: list[str]) -> bool:
    # Case matters: executables are case-sensitive, and so are flags
    # (``git branch -d`` must not allow ``git branch -D``).
    for pattern in allow:
        prefix = _pattern_words(pattern)
        if prefix and words[: len(prefix)] == prefix:
            return True
    return False


def _denied(text: str, deny: list[str]) -> str | None:
    low = text.lower()
    return next((p for p in deny if p.strip().lower() in low), None)


def _redirect_refusal(op: str, target: str) -> str | None:
    if op in (">&", "<&") and _STREAM_DUP.fullmatch(target):
        return None
    if target == "/dev/null":
        return None
    return f"redirection {op} {target} is not allowed by the allow list"


def _refusal(
    simple: _Simple, mode: str, allow: list[str], deny: list[str]
) -> str | None:
    shown = " ".join(simple.words)
    if mode == "deny":
        text = " ".join([*simple.words, *(f"{op} {t}" for op, t in simple.redirects)])
        pattern = _denied(text, deny)
        return f"{shown!r} matches deny pattern {pattern!r}" if pattern else None
    for op, target in simple.redirects:
        reason = _redirect_refusal(op, target)
        if reason:
            return reason
    if not simple.words:
        return None
    if not allow:
        return "allow list empty"
    if not _allowed(simple.words, allow):
        return f"{shown!r} is not in the allow list"
    return None


def _unsupported(command: str) -> str | None:
    """Syntax the scanner does not model, refused rather than misread."""
    if "<<" in command:
        return "here-documents and here-strings are not supported"
    if "$'" in command or '$"' in command:
        return "ANSI-C and locale quotes ($'...', $\"...\") are not supported"
    if re.search(r"(?:^|[\s;&|()])#", command):
        return "comments are not supported"
    return None


def check_command(
    command: str,
    mode: str = "deny",
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    *,
    _depth: int = 0,
) -> tuple[bool, str]:
    """``(True, "ok")`` when every part of ``command`` passes, else the reason."""
    allow = [p for p in (allow or []) if isinstance(p, str) and p.strip()]
    deny = [p for p in (deny or []) if isinstance(p, str) and p.strip()]
    if command is not None and not isinstance(command, str):
        return False, "the command is not text"
    command = (command or "").strip()
    if not command:
        return False, "empty command"
    if mode not in MODES:
        return False, f"unknown policy mode {mode!r}"
    if _depth > MAX_DEPTH:
        return False, f"command substitution nested deeper than {MAX_DEPTH}"
    unsupported = _unsupported(command)
    if unsupported:
        return False, f"cannot read the command: {unsupported}"
    try:
        commands, substitutions = _Scanner(command).parse()
    except _Unreadable as exc:
        return False, f"cannot read the command: {exc}"
    if mode == "deny":
        pattern = _denied(command, deny)
        if pattern:
            return False, f"matches deny pattern {pattern!r}"
    for inner in substitutions:
        ok, reason = check_command(inner, mode, allow, deny, _depth=_depth + 1)
        if not ok:
            return False, f"command substitution refused: {reason}"
    for simple in commands:
        reason = _refusal(simple, mode, allow, deny)
        if reason:
            return False, reason
    return True, "ok"


class _BadPolicy(ValueError):
    """A command policy the daemon cannot read."""


def _patterns(request: dict[str, Any], key: str) -> list[str]:
    value = request.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise _BadPolicy(f"{key} must be a list of strings")
    return value


def local_deny() -> list[str]:
    """The deny list of the person running the daemon (``FABRIQUE_COMMAND_DENY``)."""
    raw = os.getenv(LOCAL_DENY_ENV, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def refusal(command: str, request: Any = None) -> str | None:
    """Why the daemon will not run ``command``, or ``None`` to run it.

    ``request`` is the ``command_policy`` of a ``workspace.execute`` request.
    The daemon's own deny list applies first and whatever the request says.
    """
    own = local_deny()
    if os.name == "nt" and (own or request is not None):
        return (
            "refused: a command policy is enforced only with a POSIX shell, "
            "and this computer runs commands through cmd.exe or PowerShell"
        )
    if own:
        ok, reason = check_command(command, "deny", deny=own)
        if not ok:
            return f"refused by this computer's {LOCAL_DENY_ENV}: {reason}"
    if request is None:
        return None
    if not isinstance(request, dict):
        return "refused: the command policy is not an object"
    mode = str(request.get("mode") or "").strip().lower()
    try:
        allow, deny = _patterns(request, "allow"), _patterns(request, "deny")
    except _BadPolicy as exc:
        return f"refused: the command policy cannot be read: {exc}"
    ok, reason = check_command(command, mode, allow, deny)
    if ok:
        return None
    return f"refused by the company's command policy ({mode or 'no mode'}): {reason}"
