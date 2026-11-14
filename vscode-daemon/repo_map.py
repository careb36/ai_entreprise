"""The repository map: ``workspace.map``.

A summary of the project's definitions (classes, functions, methods and
their signatures), ranked by how often the rest of the project refers to
them, cut to a budget in characters. The backend puts it in the
Developer's and the Architect's prompts beside the top of the tree
(``backend/app/services/repo_map.py``): context, never evidence.

The idea is Aider's repository map (Apache-2.0), which parses with
tree-sitter and ranks with PageRank over the graph of files that refer to
each other's definitions. No code is taken from it, and this daemon keeps
``nats-py`` as its only dependency, so the parsing here is the standard
library's: ``ast`` for Python (definitions, and references by name), and a
conservative line-based extractor for JavaScript and TypeScript (function,
class, interface, type and enum declarations, and arrow functions bound at
the top level or exported). The ranking is a deterministic PageRank over
the file graph: a file that refers to a name another file defines gives it
a share of its rank, and a definition's rank is what the files that refer
to it gave it.

Bounds: the files a listing skips (dependencies, build output, VCS data,
hidden files) and the files that may hold secrets (``workspace_read``) are
never opened; at most ``MAP_MAX_FILES`` files of at most
``MAP_MAX_FILE_BYTES`` each, ``MAP_MAX_TOTAL_BYTES`` in all, and
``MAP_DEADLINE_SECONDS`` of parsing, past which the map says it is partial.
A symlink that leads out of the project is skipped.

Cache: the map is kept under ``FABRIQUE_MAP_STATE`` (default
``~/.fabrique-daemon/maps``) per project folder, keyed by the set of the
files' sha256 and the budget: the same files give the same map without
parsing them again, and a changed file is parsed again alone (each file's
parse is cached by its sha256).

Secrets: a signature can carry a literal (``def connect(password="…")``,
``apiKey = "sk-…"``). A string literal bound to a name that says it is a
secret, and a value shaped like a known token (private key, AWS, GitHub,
Slack, Google, Stripe, OpenAI-style keys, JWTs), is replaced by
``"[redacted]"`` before the signature is clipped or cached. The backend
redacts the map again at the prompt (``context_read.redact_secrets``).
``MAP_VERSION`` is part of the cache: a version change discards older caches.

Only regular files are read (a FIFO or a device named ``x.py`` would block
or never end), and the ranking skips names defined in more than
``MAP_MAX_DEFINERS`` files and stops at the same deadline as the parsing.
"""

from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import json
import math
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import workspace_read

MAP_VERSION = 4  # 2: signatures redacted; 3: AST defaults, JS literals; 4: fail closed on literals
MAP_MAX_FILES = 2000
MAP_MAX_FILE_BYTES = 256 * 1024
MAP_MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAP_DEADLINE_SECONDS = 20.0
MAP_DEFAULT_CHARS = 3000
MAP_MAX_CHARS = 20000
SIGNATURE_CHARS = 120
PAGERANK_DAMPING = 0.85
PAGERANK_ITERATIONS = 40
PARSE_CACHE_LIMIT = 5000
# A name defined in more files than this says nothing about which one is meant.
MAP_MAX_DEFINERS = 20

PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}

_JS_NAME = r"[A-Za-z_$][\w$]*"
# One declaration per line, anchored at its start: conservative on purpose.
_JS_PATTERNS = (
    ("function", re.compile(rf"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*({_JS_NAME})\s*(\([^)]*\))?")),
    ("class", re.compile(rf"^(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+({_JS_NAME})((?:\s+extends\s+[\w$.]+)?)")),
    ("interface", re.compile(rf"^(?:export\s+)?interface\s+({_JS_NAME})")),
    ("type", re.compile(rf"^(?:export\s+)?type\s+({_JS_NAME})\s*(?:<[^=]*>)?\s*=")),
    ("enum", re.compile(rf"^(?:export\s+)?(?:const\s+)?enum\s+({_JS_NAME})")),
    ("function", re.compile(rf"^(?:export\s+)?(?:const|let|var)\s+({_JS_NAME})\s*(?::[^=]+)?=\s*(?:async\s+)?(\([^)]*\)|{_JS_NAME})\s*(?::[^=]+)?=>")),
)
_JS_METHOD = re.compile(rf"^\s+(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*({_JS_NAME})\s*(\([^)]*\))\s*(?::[^{{]+)?\{{")
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "constructor"}
_JS_IDENT = re.compile(_JS_NAME)
# Names every program uses for the language's own types (``dict.get``,
# ``list.append``, ``len``): a reference to one says nothing about a file.
_COMMON_NAMES = frozenset(
    name
    for kind in (dict, list, str, set, bytes, object, int, Exception)
    for name in dir(kind)
) | frozenset(dir(builtins)) | {
    "run", "start", "stop", "close", "open", "read", "write", "send", "then", "map", "filter",
    "push", "length", "forEach", "toString", "log", "error", "warn", "info", "debug",
}


def _state_dir() -> Path:
    return Path(os.environ.get("FABRIQUE_MAP_STATE") or Path.home() / ".fabrique-daemon" / "maps")


# ---------------------------------------------------------------------------
# Parsing: definitions and references of one file
# ---------------------------------------------------------------------------


_SECRET_NAME = r"[\w$]*(?:password|passwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|credential)[\w$]*"
# A quoted literal assigned to a secret-looking name (``password="…"``,
# ``apiKey: str = '…'``, ``token = `…` ``), within the signature's text.
_SECRET_ASSIGNED = re.compile(
    rf"(?i)(\b{_SECRET_NAME}\b\s*(?::[^=,()]*)?=\s*)[bru]{{0,2}}(['\"`])(?:\\.|(?!\2).)*\2"
)
_SECRET_TOKENS = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bxox[abprs]-[0-9A-Za-z-]{10,}|\bAIza[0-9A-Za-z_-]{20,}|\b[sr]k_(?:live|test)_[0-9A-Za-z]{10,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_.-]{8,}"
)


def redact(text: str) -> str:
    """*text* with secret-looking literals replaced by ``"[redacted]"``."""
    text = _SECRET_ASSIGNED.sub(lambda m: f'{m.group(1)}"[redacted]"', text)
    return _SECRET_TOKENS.sub("[redacted]", text)


def _clip(text: str) -> str:
    text = redact(" ".join(text.split()))
    return text if len(text) <= SIGNATURE_CHARS else text[: SIGNATURE_CHARS - 1] + "…"


_SECRET_PARAM = re.compile(rf"(?i)^{_SECRET_NAME}$")


def _redacted_args(args: ast.arguments) -> ast.arguments:
    """*args* with the default of every secret-named parameter replaced by
    ``"[redacted]"``, whatever its form (an f-string, a call, an annotation
    holding commas): the text regex cannot see all of them."""
    args = copy.deepcopy(args)
    positional = args.posonlyargs + args.args
    pairs = list(zip(positional[len(positional) - len(args.defaults):], range(len(args.defaults))))
    for arg, i in pairs:
        if _SECRET_PARAM.match(arg.arg) and not _is_none(args.defaults[i]):
            args.defaults[i] = ast.Constant("[redacted]")
    for i, arg in enumerate(args.kwonlyargs):
        default = args.kw_defaults[i]
        if default is not None and _SECRET_PARAM.match(arg.arg) and not _is_none(default):
            args.kw_defaults[i] = ast.Constant("[redacted]")
    return args


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _py_signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return _clip(f"class {node.name}({bases})" if bases else f"class {node.name}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return _clip(f"{prefix} {node.name}({ast.unparse(_redacted_args(node.args))}){returns}")


def parse_python(text: str) -> Dict[str, Any]:
    """Top-level classes and functions, and the methods of top-level classes;
    every name the file uses (``Name`` loads, attributes, imported names)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {"defs": [], "refs": {}, "error": "does not parse"}
    defs: List[List[Any]] = []
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in tree.body:
        if not isinstance(node, kinds):
            continue
        defs.append([node.name, _py_signature(node), node.lineno, 0])
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                    child.name.startswith("__") and child.name.endswith("__")
                ):
                    defs.append([child.name, _py_signature(child), child.lineno, 1])
    refs: Dict[str, int] = {}
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.alias):
            name = node.name.rsplit(".", 1)[-1]
        if name and name not in _COMMON_NAMES:
            refs[name] = refs.get(name, 0) + 1
    return {"defs": defs, "refs": refs}


# Comments and string, template and character literals, left to right, so a
# quote inside a comment (or ``//`` inside a string) is read as the language does.
_JS_LITERALS = re.compile(
    r"//[^\n]*|/\*.*?(?:\*/|\Z)|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`",
    re.S,
)


def _strip_literals(text: str) -> Tuple[Optional[str], str]:
    """*text* without what its literals and comments hold (their line breaks
    kept): a password in a string is not a name the file refers to. Fails
    closed: a template literal nested in another's ``${…}``, an unclosed
    comment or a quote left unbalanced gives ``None`` and the reason, since
    the text left could still hold a literal's contents."""
    problem = ""

    def blank(match: "re.Match[str]") -> str:
        nonlocal problem
        literal = match.group(0)
        if literal.startswith("`") and "${" in literal and "}" not in literal[literal.rindex("${"):]:
            problem = problem or "a template literal nested in another"
        elif literal.startswith("/*") and not literal.endswith("*/"):
            problem = problem or "an unclosed comment"
        return "\n" * literal.count("\n") or " "

    stripped = _JS_LITERALS.sub(blank, text)
    if not problem and any(quote in stripped for quote in "`'\""):
        problem = "a quote the literals do not balance"
    return (None, problem) if problem else (stripped, "")


def parse_javascript(text: str) -> Dict[str, Any]:
    """Declarations at the start of a line (exported, or at the top level),
    methods inside a class, and every identifier as a reference, less the
    declarations' own names."""
    defs: List[List[Any]] = []
    in_class = False
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if not line[0].isspace():
            in_class = False
            for kind, pattern in _JS_PATTERNS:
                match = pattern.match(line)
                if match:
                    groups = match.groups()
                    name, rest = groups[0], (groups[1] if len(groups) > 1 else None) or ""
                    if kind == "function":
                        params = rest if rest.startswith("(") else f"({rest})" if rest else "()"
                        signature = f"function {name}{params}"
                    else:
                        signature = f"{kind} {name}{rest}"
                    defs.append([name, _clip(signature), number, 0])
                    in_class = kind == "class"
                    break
        elif in_class:
            match = _JS_METHOD.match(line)
            if match and match.group(1) not in _JS_KEYWORDS and len(line) - len(line.lstrip()) <= 4:
                defs.append([match.group(1), _clip(f"{match.group(1)}{match.group(2)}"), number, 1])
    stripped, problem = _strip_literals(text)
    if stripped is None:  # Definitions only: no reference is safe to keep.
        return {"defs": defs, "refs": {}, "refs_skipped": problem}
    refs: Dict[str, int] = {}
    for name in _JS_IDENT.findall(stripped):
        if name not in _COMMON_NAMES:
            refs[name] = refs.get(name, 0) + 1
    for name, _sig, _line, _depth in defs:
        if refs.get(name):
            refs[name] -= 1
    return {"defs": defs, "refs": {k: v for k, v in refs.items() if v > 0}}


def parse_file(relative: str, text: str) -> Dict[str, Any]:
    suffix = Path(relative).suffix.lower()
    if suffix in PY_SUFFIXES:
        return parse_python(text)
    return parse_javascript(text)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _pagerank(
    nodes: List[str], edges: Dict[str, Dict[str, float]], deadline: Optional[float] = None
) -> Tuple[Dict[str, float], bool]:
    """PageRank over *nodes* with weighted *edges* (source → target → weight),
    a fixed number of iterations in a fixed order: the same graph, the same
    ranks. Past *deadline* it stops early and says so."""
    count = len(nodes)
    if not count:
        return {}, False
    rank = {node: 1.0 / count for node in nodes}
    totals = {node: sum(edges.get(node, {}).values()) for node in nodes}
    for _ in range(PAGERANK_ITERATIONS):
        if deadline is not None and time.monotonic() > deadline:
            return rank, True
        dangling = sum(rank[n] for n in nodes if not totals[n])
        base = (1.0 - PAGERANK_DAMPING) / count + PAGERANK_DAMPING * dangling / count
        nxt = {node: base for node in nodes}
        for source in nodes:
            total = totals[source]
            if not total:
                continue
            share = PAGERANK_DAMPING * rank[source] / total
            for target, weight in sorted(edges[source].items()):
                nxt[target] += share * weight
        rank = nxt
    return rank, False


def _name_weight(name: str, defined_in: int) -> float:
    """Aider's weighting, as an idea: a private name or one many files define
    says little about which file is meant; a long, compound name says much."""
    weight = 1.0
    if name.startswith("_"):
        weight *= 0.1
    if defined_in > 5:
        weight *= 0.1
    if len(name) >= 8 and ("_" in name.strip("_") or re.search(r"[a-z][A-Z]", name)):
        weight *= 10.0
    return weight


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name
        or any(part in ("tests", "test", "__tests__") for part in path.split("/")[:-1])
    )


def rank_definitions(
    parsed: Dict[str, Dict[str, Any]], deadline: Optional[float] = None
) -> Tuple[List[Dict[str, Any]], bool]:
    """Every definition with its ``rank`` and ``refs`` (how many other files
    name it), best first; ties by path and line. A definition in a test file,
    or a private one, keeps a tenth of its rank: the map is for the code
    under change and what it may call. A name defined in more than
    ``MAP_MAX_DEFINERS`` files gives no edges. Past *deadline* (a
    ``time.monotonic()`` value) the edges stop growing and PageRank stops
    iterating; the second value says so."""
    defines: Dict[str, List[str]] = {}
    for path in sorted(parsed):
        for name, *_rest in parsed[path]["defs"]:
            defines.setdefault(name, [])
            if path not in defines[name]:
                defines[name].append(path)
    edges: Dict[str, Dict[str, float]] = {}
    by_name: Dict[Tuple[str, str], Dict[str, float]] = {}
    late = False
    for source in sorted(parsed):
        if deadline is not None and time.monotonic() > deadline:
            late = True
            break
        for name, count in sorted(parsed[source]["refs"].items()):
            definers = defines.get(name, ())
            if len(definers) > MAP_MAX_DEFINERS:
                continue
            targets = [t for t in definers if t != source]
            for target in targets:
                weight = _name_weight(name, len(defines[name])) * math.sqrt(count) / len(targets)
                edges.setdefault(source, {})
                edges[source][target] = edges[source].get(target, 0.0) + weight
                by_name.setdefault((target, name), {})
                by_name[(target, name)][source] = by_name[(target, name)].get(source, 0.0) + weight
    ranks, cut = _pagerank(sorted(parsed), edges, deadline)
    late = late or cut
    totals = {source: sum(out.values()) for source, out in edges.items()}
    out: List[Dict[str, Any]] = []
    for path in sorted(parsed):
        for name, signature, line, depth in parsed[path]["defs"]:
            given = by_name.get((path, name), {})
            score = sum(ranks[src] * w / totals[src] for src, w in given.items() if totals.get(src))
            if _is_test(path) or name.startswith("_"):
                score *= 0.1
            out.append({
                "path": path, "name": name, "signature": signature, "line": line, "depth": depth,
                "refs": len(given), "rank": score, "file_rank": ranks.get(path, 0.0),
            })
    out.sort(key=lambda d: (-d["rank"], -d["refs"], -d["file_rank"], d["path"], d["line"]))
    return out, late


def render(ranked: List[Dict[str, Any]], max_chars: int) -> Tuple[str, int]:
    """The best-ranked definitions that fit in *max_chars*, grouped by file
    (files in the order of their best definition, definitions by line)."""
    chosen: Dict[str, List[Dict[str, Any]]] = {}
    used = shown = 0
    for definition in ranked:
        row = _row(definition)
        cost = len(row) + 1 + (0 if definition["path"] in chosen else len(definition["path"]) + 2)
        if used + cost > max_chars:
            if used + 40 > max_chars:
                break
            continue
        chosen.setdefault(definition["path"], []).append(definition)
        used += cost
        shown += 1
    lines: List[str] = []
    for path, definitions in chosen.items():
        lines.append(f"{path}:")
        lines.extend(_row(d) for d in sorted(definitions, key=lambda d: d["line"]))
    return "\n".join(lines), shown


def _row(definition: Dict[str, Any]) -> str:
    refs = f"  [used in {definition['refs']} files]" if definition["refs"] > 1 else "  [used in 1 file]" if definition["refs"] else ""
    indent = "    " * definition["depth"]
    return f"{definition['line']:5d}| {indent}{definition['signature']}{refs}"


# ---------------------------------------------------------------------------
# The project: files, cache, the map
# ---------------------------------------------------------------------------


def _candidates(root: Path) -> Tuple[List[Tuple[str, Path]], bool]:
    files: List[Tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in workspace_read.SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or Path(name).suffix.lower() not in PY_SUFFIXES | JS_SUFFIXES:
                continue
            if name.endswith((".min.js", ".d.ts")):
                continue
            path = Path(dirpath) / name
            contained = workspace_read._inside(root, path)
            if contained is None or workspace_read.is_sensitive(root, contained):
                continue
            files.append((path.relative_to(root).as_posix(), contained))
            if len(files) >= MAP_MAX_FILES:
                return files, True
    return files, False


def read_source(path: Path, limit: int = MAP_MAX_FILE_BYTES) -> Optional[bytes]:
    """The bytes of *path* when it is a regular file of at most *limit* bytes; else ``None``."""
    try:
        info = path.lstat()
        # A FIFO, a socket or a device would block or never end.
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        # Non-blocking, so a file swapped for a FIFO since cannot hang the open.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            raw = handle.read(limit + 1)
    except OSError:
        return None
    return raw if len(raw) <= limit else None


def _load_cache(root: Path) -> Tuple[Path, Dict[str, Any]]:
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:32]
    path = _state_dir() / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == MAP_VERSION:
            return path, data
    except (OSError, ValueError):
        pass
    return path, {"version": MAP_VERSION, "parses": {}, "maps": {}}


def _save_cache(path: Path, data: Dict[str, Any]) -> None:
    parses = data.get("parses") or {}
    if len(parses) > PARSE_CACHE_LIMIT:
        data["parses"] = dict(list(parses.items())[-PARSE_CACHE_LIMIT:])
    data["maps"] = dict(list((data.get("maps") or {}).items())[-8:])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # The cache saves work; without it the map is computed again.


def build_map(workspace_path: str, max_chars: Any = None) -> Dict[str, Any]:
    """The repository map of the project in *workspace_path*, cut at
    *max_chars* (default ``MAP_DEFAULT_CHARS``, at most ``MAP_MAX_CHARS``)."""
    root = workspace_read._root(workspace_path)
    try:
        budget = max(200, min(int(max_chars or MAP_DEFAULT_CHARS), MAP_MAX_CHARS))
    except (TypeError, ValueError):
        budget = MAP_DEFAULT_CHARS
    started = time.monotonic()
    files, files_capped = _candidates(root)
    cache_path, cache = _load_cache(root)
    parses: Dict[str, Any] = cache.setdefault("parses", {})
    contents: List[Tuple[str, str, Optional[bytes]]] = []
    total = 0
    partial = files_capped
    for relative, path in files:
        raw = read_source(path)
        if raw is None:
            continue
        total += len(raw)
        if total > MAP_MAX_TOTAL_BYTES:
            partial = True
            break
        if b"\0" in raw[:8192]:
            continue
        contents.append((relative, hashlib.sha256(raw).hexdigest(), raw))
    fingerprint = hashlib.sha256(
        json.dumps([MAP_VERSION, budget, [(r, d) for r, d, _ in contents]]).encode("utf-8")
    ).hexdigest()
    cached = (cache.get("maps") or {}).get(fingerprint)
    if isinstance(cached, dict) and not partial:
        return {**cached, "cached": True}
    parsed: Dict[str, Dict[str, Any]] = {}
    for relative, digest, raw in contents:
        entry = parses.get(f"{Path(relative).suffix.lower()}:{digest}")
        if entry is None:
            if time.monotonic() - started > MAP_DEADLINE_SECONDS:
                partial = True
                break
            entry = parse_file(relative, raw.decode("utf-8", errors="replace"))
            parses[f"{Path(relative).suffix.lower()}:{digest}"] = entry
        parsed[relative] = entry
    ranked, late = rank_definitions(parsed, started + MAP_DEADLINE_SECONDS)
    partial = partial or late
    text, shown = render(ranked, budget)
    result = {
        "map": text,
        "fingerprint": fingerprint,
        "files_parsed": len(parsed),
        "files_unparsed": sorted(r for r, e in parsed.items() if e.get("error"))[:20],
        # JavaScript or TypeScript whose literals could not be stripped safely:
        # their definitions are mapped, their references are not counted.
        "files_without_refs": sorted(r for r, e in parsed.items() if e.get("refs_skipped"))[:20],
        "definitions": len(ranked),
        "shown": shown,
        "truncated": shown < len(ranked),
        "partial": partial,
        "max_chars": budget,
        "cached": False,
    }
    if not partial:
        cache.setdefault("maps", {})[fingerprint] = result
    _save_cache(cache_path, cache)
    return result


async def handle_map(nc, msg, reply, request_body, resolve) -> None:
    """``workspace.map`` → the ranked definitions as text, and how many were left out."""
    await workspace_read._serve(nc, msg, reply, request_body, resolve, lambda r, d, s: build_map(r, d.get("max_chars")))


# (subject, handler) for the daemon's subscription loop.
HANDLERS = (("workspace.map", handle_map),)
