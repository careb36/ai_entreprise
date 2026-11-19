"""The C4 code level of ``c4.md``, from the stored code graph (issue #693).

It reads the snapshot's ``code_graph`` (``collect_code``) beside the
architecture, and keeps its two kinds of statement apart:

- **Claim.** Which files a component is made of is what the architecture
  states (a module's path, its ``implements`` edge): cited as the module,
  marked as a claim.
- **Observed.** The files the stored graph holds under that path, the names
  each defines and the references between the components' files are what
  the workspace daemon parsed from the files: each file cited with the
  sha256 the daemon reported. A reference is a name one file uses and
  another defines, not a call proven.

With no stored graph the section says "not observed" and is listed in the
document's ``missing``; a component whose path holds no stored file says so,
and nothing is inferred for it. File paths and names came from a parse of
files agents may have written: every one passes ``md_inline``.

Pure; no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from services.planning.project_docs.layout import missing_line
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import md_id, md_inline

INPUT = "code_graph"
SHORT_SHA = 12
_HEX = re.compile(r"[^0-9a-f]")
Tr = Callable[..., str]


def _short(sha256: Any) -> str:
    return _HEX.sub("", str(sha256 or "").lower())[:SHORT_SHA] or "-"


def _module_of(arch: dict, key: str) -> dict | None:
    return next(
        (
            m
            for m in arch.get("modules") or []
            if isinstance(m, dict) and key in (m.get("id"), m.get("name"))
        ),
        None,
    )


def _claim_ref(arch: dict, module: dict, tr: Tr) -> dict[str, Any]:
    return ref(
        "module",
        module.get("id") or module.get("name"),
        sha256=digest(module),
        label=tr("label_arch_version", id=arch["id"], version=arch.get("version")),
        claim=True,
    )


def _file_ref(item: dict, tr: Tr) -> dict[str, Any]:
    return ref(
        "code_file",
        item.get("path"),
        sha256=_HEX.sub("", str(item.get("sha256") or "").lower()),
        label=tr("c4.code_label"),
    )


def _defines(item: dict, tr: Tr) -> str:
    names = ", ".join(md_inline(n, 80) for n in item.get("defines") or [])
    if item.get("more_defines"):
        names += " " + tr("c4.code_more", count=int(item["more_defines"]))
    return names or "-"


def _component(
    arch: dict, claim: dict, files: list[dict], src: Sources, tr: Tr
) -> list[str]:
    module = _module_of(arch, claim["module"]) or {"id": claim["module"]}
    name = module.get("name") or claim["module"]
    lines = [
        tr(
            "c4.code_component",
            name=md_inline(name),
            id=md_id(module.get("id") or claim["module"]),
        ),
        "",
        tr(
            "c4.code_claim",
            path=md_inline(claim["path"]),
            mark=src.cite(_claim_ref(arch, module, tr)),
        ),
        "",
    ]
    mine = [f for f in files if claim["module"] in (f.get("modules") or [])]
    if not mine:
        return lines + [tr("c4.code_component_none"), ""]
    lines += [tr("c4.code_observed"), "", tr("c4.code_files"), "|---|---|---|---|"]
    for item in mine:
        lines.append(
            f"| {md_inline(item.get('path'))} | `{_short(item.get('sha256'))}` | "
            f"{_defines(item, tr)} | {src.cite(_file_ref(item, tr))} |"
        )
    return lines + [""]


def _references(observed: dict, src: Sources, tr: Tr) -> list[str]:
    owners = {f.get("path"): f for f in observed.get("files") or []}
    refs = observed.get("references") or []
    lines = [tr("c4.code_refs_heading"), "", tr("c4.code_refs_note"), ""]
    if not refs:
        return lines + [tr("c4.code_refs_none"), ""]
    lines += [tr("c4.code_refs"), "|---|---|---|---|"]

    def end(path: Any) -> str:
        item = owners.get(path) or {"path": path}
        modules = ", ".join(md_id(m) for m in item.get("modules") or [])
        return f"{md_inline(path)} ({modules})" if modules else md_inline(path)

    for edge in refs:
        names = ", ".join(md_inline(n, 80) for n in edge.get("names") or [])
        if edge.get("more_names"):
            names += " " + tr("c4.code_more", count=int(edge["more_names"]))
        marks = src.cite(
            _file_ref(
                {"path": edge.get("from"), "sha256": edge.get("from_sha256")}, tr
            ),
            _file_ref({"path": edge.get("to"), "sha256": edge.get("to_sha256")}, tr),
        )
        lines.append(
            f"| {end(edge.get('from'))} | {end(edge.get('to'))} | {names or '-'} | {marks} |"
        )
    if observed.get("more_references"):
        lines += ["", tr("c4.code_refs_cut", count=int(observed["more_references"]))]
    return lines + [""]


def section(
    snapshot: dict[str, Any], src: Sources, lang: str, tr: Tr
) -> tuple[list[str], bool]:
    """The code level's lines, and whether anything was observed."""
    arch = snapshot["architecture"]
    graph = snapshot.get(INPUT) or {}
    observed = graph.get("observed")
    lines = [tr("c4.code_heading"), ""]
    if not observed:
        return lines + [missing_line(lang, tr("c4.code_not_observed")), ""], False
    when = snapshot.get("code_graph_observed_at") or tr("c4.code_when_unknown")
    lines += [tr("c4.code_intro", when=md_inline(when)), ""]
    files = observed.get("files") or []
    for claim in graph.get("claims") or []:
        lines += _component(arch, claim, files, src, tr)
    named = {c.get("module") for c in graph.get("claims") or []}
    for module in arch.get("modules") or []:
        if isinstance(module, dict) and not named & {
            module.get("id"),
            module.get("name"),
        }:
            lines += [
                tr(
                    "c4.code_component",
                    name=md_inline(module.get("name")),
                    id=md_id(module.get("id")),
                ),
                "",
                tr("c4.code_no_path"),
                "",
            ]
    if observed.get("more_files"):
        lines += [tr("c4.code_files_cut", count=int(observed["more_files"])), ""]
    return lines + _references(observed, src, tr), True
