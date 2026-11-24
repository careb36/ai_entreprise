"""The documentation checks: links that resolve and paths that exist (issue #766).

Documenting a whole project (``docs/design/agent-toolsets.md``, "Next changes"
item 10) asks "check what was written": a guide whose links lead nowhere or
that names files the project does not have is wrong, whatever its writer says.
Two Verifier checks, on a delivery that changes Markdown under a docs folder
(a path component ``docs`` or ``doc``) or a README file:

- ``docs_links``: every relative link and reference definition in those files
  resolves to a file or folder of the delivered tree, and its ``#anchor`` to a
  heading (or explicit anchor) of the target, when the target's text is known.
- ``docs_code_paths``: every inline code span that names a path (a known file
  suffix, or a trailing ``/`` for a folder) names one the tree has: from the
  root, from the document's folder, or as the tail of a listed path.

What is judged is observed, never the writer's word:

- the tree is the daemon's listing of the folder the delivery was written into
  (``project_files`` on the evidence, ``vscode-daemon/delivery.py``);
- a delivered file's text counts only when the daemon read the same bytes
  back: the edit tool's files as the daemon returned them (``tool_edit_files``),
  or an answer's file whose sha256, computed here over the text, equals the
  sha256 the daemon reported reading back from disk (``workspace_writes``).
  Any other delivered document is ``unverifiable`` (not read back).

A broken link, or a path with a ``/`` that no listed path matches, refutes,
with the broken ones listed (at most ``SHOWN``, then how many more). What
cannot be decided is ``unverifiable`` and listed: a target in a folder the
listing skips (hidden ones, ``node_modules``, ``build``…) or outside the
project, an anchor into a document whose text was not read back, any miss
when the listing was cut. A bare file name not found (``config.yaml``) is a
finding only: it may be a file the reader is told to create. Absolute URLs
are not followed: verification never touches the network.

Pure: no database, no I/O.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from typing import Any
from urllib.parse import unquote

from services.contracts.verdicts import VERDICT_REFUTED, VERDICT_UNVERIFIABLE
from services.evidence import context_read, markdown_refs

CHECK_LINKS = "docs_links"
CHECK_CODE_PATHS = "docs_code_paths"
SHOWN = 20
MARKDOWN = (".md", ".markdown")
DOC_FOLDERS = frozenset({"docs", "doc"})
# The folders the daemon's listing skips (``workspace_read.SKIP_DIRS``); hidden
# folders are skipped too.
UNLISTED = frozenset(
    {
        "node_modules", "__pycache__", "venv", "env", "dist", "build", "target",
        "coverage", "vendor",
    }
)  # fmt: skip
PATH_SUFFIXES = frozenset(
    {
        "py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "json", "yaml", "yml",
        "toml", "ini", "cfg", "md", "markdown", "rst", "txt", "sh", "ps1", "go",
        "rs", "java", "kt", "rb", "php", "cs", "c", "h", "cpp", "hpp", "css",
        "scss", "html", "sql", "lock", "xml", "csv", "ipynb", "proto", "tf",
        "vue", "svelte", "swift",
    }
)  # fmt: skip
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_PATH_TOKEN = re.compile(r"^[\w.\-/]+$")


def is_documentation(path: str) -> bool:
    """Markdown under a docs folder, or a README file."""
    parts = [p for p in str(path or "").split("/") if p]
    if not parts:
        return False
    if parts[-1].upper().startswith("README"):
        return True
    return parts[-1].lower().endswith(MARKDOWN) and any(
        p.lower() in DOC_FOLDERS for p in parts[:-1]
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_back_texts(
    evidence: dict[str, Any], declared: list[dict[str, Any]]
) -> tuple[dict[str, str], list[str]]:
    """``(path → text the daemon read back, delivered documents not read back)``."""
    texts = {f["path"]: f["content"] for f in context_read.edited_texts(evidence)}
    writes = {
        w.get("path"): w
        for w in evidence.get("workspace_writes") or []
        if isinstance(w, dict)
    }
    unread: list[str] = []
    for f in declared:
        path, content = f.get("path"), f.get("content")
        if not isinstance(path, str) or path in texts:
            continue
        write = writes.get(path) or {}
        if (
            isinstance(content, str)
            and "edits" not in f
            and write.get("status") == "written"
            and write.get("sha256") == _sha256(content)
        ):
            texts[path] = content
        elif is_documentation(path):
            unread.append(path)
    return texts, unread


def _listing(evidence: dict[str, Any]) -> tuple[set[str], bool] | None:
    listing = evidence.get("project_files")
    if isinstance(listing, dict) and isinstance(listing.get("files"), list):
        files, complete = listing["files"], not listing.get("truncated")
    elif isinstance(listing, list):
        files, complete = listing, True
    else:
        return None
    return {f.strip("/") for f in files if isinstance(f, str) and f}, complete


class _Tree:
    def __init__(self, files: set[str], complete: bool, texts: dict[str, str]):
        self.files = files | set(texts)
        self.complete = complete
        self.dirs = {
            "/".join(f.split("/")[:i])
            for f in self.files
            for i in range(1, f.count("/") + 1)
        }

    def has(self, path: str) -> bool:
        return path == "" or path in self.files or path in self.dirs

    def tail(self, path: str) -> bool:
        """Some listed path ends with *path* (a path given from a sub-project)."""
        return any(f.endswith("/" + path) for f in self.files | self.dirs)


def _unlisted(path: str) -> bool:
    return any(p.startswith(".") or p in UNLISTED for p in path.split("/") if p)


def _resolve(document: str, target: str) -> str | None:
    """*target* (a link's path part) from *document*, or ``None`` outside the project."""
    base = "" if target.startswith("/") else posixpath.dirname(document)
    joined = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
    if joined in (".", ""):
        return ""
    return None if joined == ".." or joined.startswith("../") else joined


def _links(
    texts: dict[str, str], documents: list[str], tree: _Tree
) -> tuple[list[str], list[str], int]:
    broken: list[str] = []
    unchecked: list[str] = []
    count = 0
    for document in documents:
        for line, target in markdown_refs.links(texts[document]):
            if _SCHEME.match(target) or target.startswith("//"):
                continue  # An absolute URL: never followed.
            count += 1
            where = f"{document}:{line} → {target}"
            path_part, _, anchor = target.partition("#")
            path = (
                _resolve(document, unquote(path_part.split("?", 1)[0]))
                if path_part
                else document  # "#anchor": this document.
            )
            if path is None or _unlisted(path or ""):
                unchecked.append(f"{where} (outside the listing)")
                continue
            if not tree.has(path):
                (broken if tree.complete else unchecked).append(
                    f"{where} (no such file)"
                )
                continue
            if not anchor or not path.lower().endswith(MARKDOWN):
                continue
            if path not in texts:
                unchecked.append(f"{where} (the target's text was not read back)")
            elif unquote(anchor).lower() not in markdown_refs.anchors(texts[path]):
                broken.append(f"{where} (no such heading)")
    return broken, unchecked, count


def _is_path_token(token: str) -> bool:
    if not _PATH_TOKEN.match(token) or token.startswith(("-", "/")) or "//" in token:
        return False
    if token.endswith("/"):
        return len(token) > 1 and not token.startswith(".")
    name = token.rsplit("/", 1)[-1]
    return "." in name.strip(".") and name.rsplit(".", 1)[-1].lower() in PATH_SUFFIXES


def _code_paths(
    texts: dict[str, str], documents: list[str], tree: _Tree
) -> tuple[list[str], list[str], list[str], int]:
    broken: list[str] = []
    unchecked: list[str] = []
    findings: list[str] = []
    count = 0
    for document in documents:
        folder = posixpath.dirname(document)
        for line, token in markdown_refs.code_spans(texts[document]):
            if not _is_path_token(token):
                continue
            count += 1
            path = posixpath.normpath(token.rstrip("/"))
            where = f"{document}:{line} `{token}`"
            if path.startswith("../") or _unlisted(path):
                unchecked.append(f"{where} (outside the listing)")
                continue
            local = _resolve(document, path) if folder else path
            if tree.has(path) or (local and tree.has(local)) or tree.tail(path):
                continue
            if "/" not in path:
                findings.append(f"{where} (no file of that name)")
            elif tree.complete:
                broken.append(f"{where} (no such path)")
            else:
                unchecked.append(f"{where} (not in the cut listing)")
    return broken, unchecked, findings, count


def _shown(items: list[str]) -> str:
    more = f"; and {len(items) - SHOWN} more" if len(items) > SHOWN else ""
    return "; ".join(items[:SHOWN]) + more


def _result(
    name: str,
    what: str,
    count: int,
    broken: list[str],
    unchecked: list[str],
    documents: list[str],
    findings: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "documents": documents,
        "checked": count,
        "broken": broken,
        "unchecked": unchecked,
    }
    if findings:
        result["findings"] = findings
    if broken:
        result.update(
            outcome=VERDICT_REFUTED,
            observation=f"{len(broken)} broken {what}: {_shown(broken)}",
        )
    elif unchecked:
        result.update(
            outcome=VERDICT_UNVERIFIABLE,
            observation=f"{len(unchecked)} {what} not checked: {_shown(unchecked)}",
        )
    else:
        result.update(
            outcome="pass",
            observation=f"{count} {what} in {len(documents)} document(s) resolve",
        )
    return result


def checks(
    evidence: dict[str, Any] | None, declared: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The two checks for a delivery that changes documentation (module
    docstring); ``[]`` when it changes none. *declared* is the answer's files
    (``parse_file_manifest``); the edit tool's come from *evidence*."""
    evidence = evidence or {}
    texts, unread = read_back_texts(evidence, declared)
    documents = sorted(p for p in texts if is_documentation(p))
    if not documents and not unread:
        return []
    listing = _listing(evidence)
    if listing is None:
        reason = "no listing of the delivered tree from the workspace daemon"
        return [
            {"name": n, "outcome": VERDICT_UNVERIFIABLE, "observation": reason}
            for n in (CHECK_LINKS, CHECK_CODE_PATHS)
        ]
    tree = _Tree(*listing, texts)
    not_read = [f"{p} (its text was not read back from disk)" for p in unread]
    broken, unchecked, count = _links(texts, documents, tree)
    paths_broken, paths_unchecked, findings, paths = _code_paths(texts, documents, tree)
    return [
        _result(CHECK_LINKS, "link(s)", count, broken, not_read + unchecked, documents),
        _result(
            CHECK_CODE_PATHS,
            "cited path(s)",
            paths,
            paths_broken,
            not_read + paths_unchecked,
            documents,
            findings,
        ),
    ]
