"""A company's standards file: its schema, parser and validator (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §3, §4, §12 decisions 3, 5 and 7).

A company standard is a check the Verifier runs without a model. Its kind
comes from the closed list ``KINDS``; the file places and configures checks,
it never adds a kind and carries no code of its own. The file is
``.fabrique/standards.toml`` at the repository root, read with the standard
library's ``tomllib`` (data only, typed values; YAML is not accepted: its
implicit typing silently changes a standard's meaning).

::

    format = 1

    [[standard]]                 # globs relative to the repository root
    id = "LF-SEC-1"
    ...

    [folders."backend"]          # per-folder rules: globs relative to the folder
    [[folders."backend".standard]]
    id = "LF-LINT-1"
    ...

``parse`` is pure: bytes in, ``Parsed`` out, every refusal named. A refusal
of the whole file (``file_refusals``) leaves no standard; a standard's own
refusal keeps the others parsed, and a draft with any refusal cannot be
activated. Nothing here reads a disk, a clock or a network, and no hash in
the file is read: the platform computes the sha256 of the bytes it received
(rule 6 of ``docs/POSITIONING.md``).

A company standard only adds to the catalogue (§12 decision 1): an ``STD-``
id anywhere in the file refuses the whole file.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

from services.contracts import company_regex, protected_paths, roles

PATH = ".fabrique/standards.toml"
FORMAT = 1
MAX_BYTES = 64 * 1024
MAX_STANDARDS = 50
MAX_GLOBS = 50
MAX_GLOB_CHARS = 300
MAX_GUARDS = 20
MAX_COMMAND_CHARS = 1000
MAX_LIMIT = 1_000_000
ALL_ROLES = "*"

KINDS = ("command", "forbid_pattern", "require_together", "max_lines", "forbid_path")
SEVERITIES = ("blocking", "finding")
ON_VALUES = ("changed", "added")
# The kinds the backend evaluates itself, with no execution at all.
IN_BACKEND_KINDS = ("forbid_pattern", "require_together", "max_lines", "forbid_path")

_ID = re.compile(r"[A-Z][A-Z0-9]{1,7}-[A-Z0-9]+-[0-9]+")
_CATALOGUE_ID = re.compile(r"(?i)\bSTD-[A-Z0-9]+-[0-9]+")
_COMMON = (
    "id",
    "title",
    "rule",
    "binds",
    "kind",
    "severity",
    "paths",
    "owner",
    "folder",
)
_REQUIRED_COMMON = ("id", "title", "rule", "binds", "kind", "severity", "owner")
_KIND_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # kind: (required fields, optional fields)
    "command": (("command", "guards"), ()),
    "forbid_pattern": (("pattern", "files"), ("except",)),
    "require_together": (("when", "then"), ("on",)),
    "max_lines": (("limit", "files"), ("baseline", "except")),
    "forbid_path": (("files",), ("except",)),
}
_GLOB_FIELDS = ("paths", "files", "except", "when", "then")

# What a command standard may never run, whatever the daemon's own policy
# says (§10 T1): fetching from the network, a pipe into an interpreter,
# privilege, credentials, rewriting history or remotes. The daemon's command
# policy and sandbox still apply at run time; this refuses at import, so an
# Owner never activates one.
_DENY_WORDS = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "sudo",
        "su",
        "doas",
        "eval",
        "exec",
        "telnet",
        "ftp",
    }
)
_DENY_TEXT = (
    re.compile(r"\|\s*(?:ba|z|k|da|c|tc|fi)?sh\b"),
    re.compile(r"\|\s*(?:python[0-9.]*|node|perl|ruby|php)\b"),
    re.compile(
        r"(?:~|\$HOME|\$\{HOME\})/\.(?:ssh|aws|gnupg|config/gcloud|kube|docker)\b"
    ),
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+/(?:\s|$)"),
    re.compile(r"\bchmod\s+(?:-R\s+)?777\b"),
    re.compile(
        r"\bgit\s+(?:push|remote|config|credential|filter-branch|reset\s+--hard)\b"
    ),
    re.compile(r"/dev/tcp/"),
    re.compile(r"<<"),
)
_WORD_SPLIT = re.compile(r"[\s;&|()`$<>]+")


@dataclass
class Parsed:
    """What ``parse`` read: the standards, and every refusal by name."""

    format: int | None = None
    standards: list[dict[str, Any]] = field(default_factory=list)
    file_refusals: list[dict[str, Any]] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.file_refusals and not self.refusals

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "standards": self.standards,
            "file_refusals": self.file_refusals,
            "refusals": self.refusals,
        }


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _refusal(
    code: str, message: str, standard: str | None = None, field_name: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message}
    if standard is not None:
        out["standard"] = standard
    if field_name is not None:
        out["field"] = field_name
    return out


def command_refusal(command: Any) -> str | None:
    """Pure: why *command* cannot be a command standard's command, or None."""
    if not isinstance(command, str) or not command.strip():
        return "the command is a non-empty string"
    if len(command) > MAX_COMMAND_CHARS:
        return f"the command is longer than {MAX_COMMAND_CHARS} characters"
    if any(ch in command for ch in ("\n", "\r", "\x00")):
        return "the command is one line"
    for pattern in _DENY_TEXT:
        if pattern.search(command):
            return f"the command matches a refused form ({pattern.pattern})"
    words = {w.strip("'\"").lower() for w in _WORD_SPLIT.split(command) if w}
    denied = sorted(words & _DENY_WORDS)
    if denied:
        return f"the command runs {denied[0]}, which a standard may never run"
    return None


def _relative(value: Any) -> str | None:
    """A repository-relative POSIX path, or None (absolute, outside, empty)."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    if value.strip().startswith(("/", "\\")) or re.match(r"[A-Za-z]:", value.strip()):
        return None
    return protected_paths.normalize(value, backslash_separates=False)


def _join(folder: str | None, path: str) -> str:
    return f"{folder}/{path}" if folder else path


class _Reader:
    """Reads one standard's table, collecting its refusals."""

    def __init__(self, table: dict[str, Any], label: str, folder: str | None):
        self.table, self.label, self.folder = table, label, folder
        self.refusals: list[dict[str, Any]] = []

    def refuse(self, code: str, message: str, field_name: str | None = None) -> None:
        self.refusals.append(_refusal(code, message, self.label, field_name))

    def text(self, name: str, limit: int) -> str | None:
        value = self.table.get(name)
        if not isinstance(value, str) or not value.strip():
            self.refuse("invalid_field", f"{name} is a non-empty string", name)
            return None
        value = value.strip()
        if len(value) > limit:
            self.refuse("invalid_field", f"{name} is at most {limit} characters", name)
            return None
        return value

    def path(self, name: str, value: Any) -> str | None:
        clean = _relative(value)
        if clean is None:
            self.refuse(
                "invalid_path",
                f"{name}: {value!r} is not a path inside the repository",
                name,
            )
            return None
        return _join(self.folder, clean)

    def globs(self, name: str, required: bool) -> list[str]:
        value = self.table.get(name)
        if value is None:
            if required:
                self.refuse("missing_field", f"{name} is required for this kind", name)
            return []
        if not isinstance(value, list) or (required and not value):
            self.refuse("invalid_field", f"{name} is a list of globs", name)
            return []
        if len(value) > MAX_GLOBS:
            self.refuse(
                "too_many_globs", f"{name} holds at most {MAX_GLOBS} globs", name
            )
            return []
        out: list[str] = []
        for glob in value:
            if not isinstance(glob, str) or len(glob) > MAX_GLOB_CHARS:
                self.refuse(
                    "invalid_glob",
                    f"{name}: {glob!r} is not a glob of at most {MAX_GLOB_CHARS} characters",
                    name,
                )
                continue
            clean = _relative(glob)
            if clean is None:
                self.refuse(
                    "invalid_glob",
                    f"{name}: {glob!r} does not stay inside the repository",
                    name,
                )
                continue
            out.append(_join(self.folder, clean))
        return out

    def guards(self) -> list[dict[str, Any]]:
        value = self.table.get("guards")
        if not isinstance(value, list) or not value:
            self.refuse(
                "command_without_guards",
                "a command standard names its guards: the files and folders its verdict depends on",
                "guards",
            )
            return []
        if len(value) > MAX_GUARDS:
            self.refuse(
                "too_many_guards",
                f"guards holds at most {MAX_GUARDS} entries",
                "guards",
            )
            return []
        out: list[dict[str, Any]] = []
        for entry in value:
            raw, exclude = entry, []
            if isinstance(entry, dict):
                unknown = sorted(set(entry) - {"path", "exclude"})
                if unknown:
                    self.refuse(
                        "unknown_field", f"guards: unknown field {unknown[0]}", "guards"
                    )
                    continue
                raw = entry.get("path")
                exclude_reader = _Reader(
                    {"exclude": entry.get("exclude", [])}, self.label, None
                )
                exclude = exclude_reader.globs("exclude", required=False)
                self.refusals.extend(exclude_reader.refusals)
            path = self.path("guards", raw)
            if path is not None:
                out.append({"path": path, "exclude": exclude})
        return out


def _standard(
    table: Any, index: int, folder: str | None
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    label = (
        table.get("id")
        if isinstance(table, dict) and isinstance(table.get("id"), str)
        else f"#{index + 1}"
    )
    if not isinstance(table, dict):
        return None, [_refusal("invalid_standard", "a standard is a table", label)]
    r = _Reader(table, label, folder)
    kind = table.get("kind")
    if kind not in KINDS:
        r.refuse(
            "unknown_kind", f"kind {kind!r} is not one of {', '.join(KINDS)}", "kind"
        )
        return None, r.refusals
    required, optional = _KIND_FIELDS[kind]
    allowed = set(_COMMON) | set(required) | set(optional)
    for name in sorted(set(table) - allowed):
        r.refuse("unknown_field", f"{name} is not a field of a {kind} standard", name)
    for name in _REQUIRED_COMMON + required:
        if name not in table:
            r.refuse("missing_field", f"{name} is required", name)
    if folder is not None and "folder" in table:
        r.refuse(
            "folder_in_folder_table",
            "a standard in a folder table takes its folder from the table",
            "folder",
        )

    out: dict[str, Any] = {"kind": kind}
    sid = table.get("id")
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        r.refuse("invalid_id", f"id {sid!r} is not of the form LF-ARCH-1", "id")
    elif sid.startswith("STD-"):
        r.refuse("catalogue_id", "the STD- prefix belongs to the catalogue", "id")
    out["id"] = sid
    out["title"] = r.text("title", 200) if "title" in table else None
    out["rule"] = r.text("rule", 2000) if "rule" in table else None
    out["owner"] = r.text("owner", 100) if "owner" in table else None
    severity = table.get("severity")
    if "severity" in table and severity not in SEVERITIES:
        r.refuse("invalid_field", "severity is blocking or finding", "severity")
    out["severity"] = severity
    binds = table.get("binds")
    if "binds" in table:
        if (
            not isinstance(binds, list)
            or not binds
            or not all(isinstance(b, str) for b in binds)
        ):
            r.refuse("invalid_field", "binds is a non-empty list of roles", "binds")
            binds = []
        for role in binds:
            if role != ALL_ROLES and role not in roles.ROLES:
                r.refuse(
                    "unknown_role", f"{role!r} is not a role of the catalogue", "binds"
                )
    out["binds"] = sorted(set(binds or []))
    out["folder"] = folder
    if folder is None and "folder" in table:
        out["folder"] = r.path("folder", table.get("folder"))
    paths = r.globs("paths", required=False)
    out["paths"] = paths or ([f"{folder}/**"] if folder else [])

    if kind == "command":
        refusal = command_refusal(table.get("command"))
        if refusal and "command" in table:
            r.refuse("command_refused", refusal, "command")
        out["command"] = table.get("command")
        out["guards"] = r.guards()
    elif kind == "forbid_pattern":
        refusal = company_regex.compile_refusal(table.get("pattern"))
        if refusal and "pattern" in table:
            code = (
                refusal if refusal == company_regex.UNAVAILABLE else "pattern_refused"
            )
            r.refuse(code, refusal, "pattern")
        out["pattern"] = table.get("pattern")
    elif kind == "require_together":
        on = table.get("on", "changed")
        if on not in ON_VALUES:
            r.refuse("invalid_field", "on is changed or added", "on")
        out["on"] = on
    elif kind == "max_lines":
        limit = table.get("limit")
        if "limit" in table and (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_LIMIT
        ):
            r.refuse(
                "invalid_field", f"limit is an integer from 1 to {MAX_LIMIT}", "limit"
            )
        out["limit"] = limit
        out["baseline"] = (
            r.path("baseline", table["baseline"]) if "baseline" in table else None
        )
    for name in _GLOB_FIELDS[1:]:
        if name in required or name in optional:
            out[name] = r.globs(name, required=name in required)
    return out, r.refusals


def parse(data: bytes) -> Parsed:
    """Pure: the standards file *data* read and validated."""
    result = Parsed()
    if not isinstance(data, bytes | bytearray):
        result.file_refusals.append(_refusal("invalid_file", "the file is bytes"))
        return result
    if len(data) > MAX_BYTES:
        result.file_refusals.append(
            _refusal("file_too_large", f"the file is larger than {MAX_BYTES} bytes")
        )
        return result
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        result.file_refusals.append(
            _refusal("invalid_file", "the file is not UTF-8 text")
        )
        return result
    mention = _CATALOGUE_ID.search(text)
    if mention:
        result.file_refusals.append(
            _refusal(
                "catalogue_reference",
                f"the file names the catalogue standard {mention.group(0)}; a company only adds standards",
            )
        )
        return result
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        result.file_refusals.append(
            _refusal("invalid_toml", f"the file is not valid TOML: {exc}")
        )
        return result
    for name in sorted(set(doc) - {"format", "standard", "folders"}):
        result.file_refusals.append(
            _refusal(
                "unknown_field", f"{name} is not a top-level field", field_name=name
            )
        )
    fmt = doc.get("format")
    if fmt != FORMAT or isinstance(fmt, bool):
        result.file_refusals.append(
            _refusal(
                "unknown_format", f"format = {FORMAT} is required", field_name="format"
            )
        )
    result.format = fmt if isinstance(fmt, int) and not isinstance(fmt, bool) else None

    tables: list[tuple[Any, str | None]] = []
    top = doc.get("standard", [])
    if not isinstance(top, list):
        result.file_refusals.append(
            _refusal("invalid_file", "standard is an array of tables ([[standard]])")
        )
        top = []
    tables.extend((t, None) for t in top)
    folders = doc.get("folders", {})
    if not isinstance(folders, dict):
        result.file_refusals.append(
            _refusal("invalid_file", "folders is a table of folder tables")
        )
        folders = {}
    for key, body in folders.items():
        folder = _relative(key)
        if folder is None or not isinstance(body, dict):
            result.file_refusals.append(
                _refusal(
                    "invalid_folder",
                    f"folders.{key!r} is not a folder table inside the repository",
                )
            )
            continue
        for name in sorted(set(body) - {"standard"}):
            result.file_refusals.append(
                _refusal("unknown_field", f"folders.{key}.{name} is not a field")
            )
        inner = body.get("standard", [])
        if not isinstance(inner, list):
            result.file_refusals.append(
                _refusal(
                    "invalid_file", f"folders.{key}.standard is an array of tables"
                )
            )
            continue
        tables.extend((t, folder) for t in inner)
    if len(tables) > MAX_STANDARDS:
        result.file_refusals.append(
            _refusal(
                "too_many_standards",
                f"the file holds more than {MAX_STANDARDS} standards",
            )
        )
    if result.file_refusals:
        return result

    seen: set[str] = set()
    for index, (table, folder) in enumerate(tables):
        standard, refusals = _standard(table, index, folder)
        result.refusals.extend(refusals)
        if standard is None:
            continue
        if standard["id"] in seen:
            result.refusals.append(
                _refusal(
                    "duplicate_id",
                    f"{standard['id']} appears twice",
                    standard["id"],
                    "id",
                )
            )
            continue
        seen.add(standard["id"])
        result.standards.append(standard)
    return result


def has_command(standards: list[dict[str, Any]]) -> bool:
    return any(s.get("kind") == "command" for s in standards)


def guard_specs(standards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every distinct guard of the command standards, in order."""
    out: list[dict[str, Any]] = []
    for standard in standards:
        for guard in standard.get("guards") or []:
            if guard not in out:
                out.append(guard)
    return out


def baseline_paths(standards: list[dict[str, Any]]) -> list[str]:
    return sorted({s["baseline"] for s in standards if s.get("baseline")})


def guard_digest(files: dict[str, str]) -> str:
    """Pure: one sha256 over a guard's files, each ``<sha256>  <path>`` on its
    own line, sorted by path; a file added, removed, renamed or edited
    changes it (§12 decision 5)."""
    lines = "".join(f"{files[path]}  {path}\n" for path in sorted(files))
    return sha256_hex(lines.encode("utf-8"))


def excluded(path: str, exclude: list[str]) -> bool:
    return bool(exclude) and bool(protected_paths.matches([path], exclude))


def baseline_refusal(data: bytes | None) -> tuple[dict[str, int] | None, str | None]:
    """Pure: a ``baseline`` file read as a JSON map of path to line count."""
    import json

    if data is None:
        return None, "the baseline file is not in the repository at that commit"
    try:
        value = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "the baseline is not JSON"
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
        for k, v in value.items()
    ):
        return None, "the baseline is not a JSON map of path to a line count"
    return value, None
