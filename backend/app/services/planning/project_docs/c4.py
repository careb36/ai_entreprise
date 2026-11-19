"""C4 diagrams (context, containers, components) from the architecture record,
and the code level from the stored code graph.

The C4 model is Simon Brown's (c4model.com, CC BY 4.0); its four levels give
the structure, and the content comes only from records:

- the approved architecture (``models/architecture.py``): modules with their
  responsibility, path and ``depends_on``; interfaces with the module that
  provides them and their consumers;
- the stack discovery (``ProjectDiscovery``): the databases and infrastructure
  read from the project's files.

The architecture record has no field for people or for systems outside the
project, so the context level shows what can be derived (an interface no
module consumes is used from outside) and says what is not recorded. The code
level (``c4_code``, issue #693) is a section of the document, not a diagram:
per component, the files the stored code graph holds under the module's path
(the architecture's claim) and what was observed in them.

A diagram is plain data the frontend draws (``nodes``, ``edges``, ``groups``; our labels, the
notes and the titles in the pack's language, the names from the architecture
as recorded)
plus its Mermaid source, rendered from that data, and the digest of its inputs
(``input_sha256``) the drift check compares. Node ids are numbered here
(``n1``, ``n2``…), never taken from record text, and every label passes
``sanitize.mermaid_label``.

Pure; no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from services.planning.project_docs import c4_code, texts
from services.planning.project_docs.layout import input_sha256, missing_line, tr_of
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import (
    md_id,
    md_inline,
    mermaid_label,
    plain_label,
)

C4_INPUTS = ("architecture", "stack")
# The document also reads the stored code graph for its code level; the
# diagrams do not, so a changed file leaves them current.
DOCUMENT_INPUTS = (*C4_INPUTS, c4_code.INPUT)
LEVELS = ("context", "container", "component")
Tr = Callable[..., str]
OUTSIDE_KINDS = ("http", "cli", "event")
# Mermaid's shape for each kind of element.
_SHAPES = {
    "person": ('(["', '"])'),
    "system": ('["', '"]'),
    "external": ('[["', '"]]'),
    "container": ('["', '"]'),
    "datastore": ('[("', '")]'),
    "component": ('("', '")'),
}
_CLASSES = {
    "person": "fill:#08427b,color:#fff",
    "system": "fill:#1168bd,color:#fff",
    "external": "fill:#999999,color:#fff",
    "container": "fill:#438dd5,color:#fff",
    "datastore": "fill:#438dd5,color:#fff",
    "component": "fill:#85bbf0,color:#000",
}


class _Diagram:
    def __init__(self, key: str, level: str, title: str) -> None:
        self.key, self.level, self.title = key, level, title
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self._ids: dict[str, str] = {}

    def node(
        self,
        ref_key: str,
        label: Any,
        kind: str,
        detail: Any = "",
        group: str | None = None,
        source: dict | None = None,
    ) -> str:
        node_id = f"n{len(self.nodes) + 1}"
        self._ids[ref_key] = node_id
        self.nodes.append(
            {
                "id": node_id,
                "label": plain_label(label),
                "kind": kind,
                "detail": plain_label(detail, 200) if detail else "",
                "group": group,
                "source": source,
            }
        )
        return node_id

    def id_of(self, ref_key: str) -> str | None:
        return self._ids.get(ref_key)

    def edge(self, source: str | None, target: str | None, label: Any) -> None:
        if source and target and source != target:
            self.edges.append(
                {"source": source, "target": target, "label": plain_label(label, 40)}
            )

    def group(self, label: Any) -> str:
        group_id = f"g{len(self.groups) + 1}"
        self.groups.append({"id": group_id, "label": plain_label(label)})
        return group_id

    def as_dict(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        out = {
            "key": self.key,
            "level": self.level,
            "title": self.title,
            "nodes": self.nodes,
            "edges": self.edges,
            "groups": self.groups,
            "notes": self.notes,
            "input_sha256": input_sha256(snapshot, C4_INPUTS),
        }
        out["mermaid"] = to_mermaid(out)
        return out


def to_mermaid(diagram: dict[str, Any]) -> str:
    """The Mermaid flowchart source for *diagram*: its ids are ours, its labels sanitized."""
    lines = ["flowchart TB"]

    def node_line(node: dict[str, Any], indent: str) -> str:
        opening, closing = _SHAPES.get(node["kind"], _SHAPES["container"])
        return f"{indent}{node['id']}{opening}{mermaid_label(node['label'])}{closing}"

    for group in diagram.get("groups") or []:
        lines.append(f'  subgraph {group["id"]}["{mermaid_label(group["label"])}"]')
        lines += [
            node_line(n, "    ")
            for n in diagram["nodes"]
            if n.get("group") == group["id"]
        ]
        lines.append("  end")
    lines += [node_line(n, "  ") for n in diagram["nodes"] if not n.get("group")]
    for edge in diagram.get("edges") or []:
        lines.append(
            f'  {edge["source"]} -->|"{mermaid_label(edge["label"], 40)}"| {edge["target"]}'
        )
    used = sorted({n["kind"] for n in diagram["nodes"]})
    lines += [
        f"  classDef {kind} {_CLASSES[kind]}" for kind in used if kind in _CLASSES
    ]
    for kind in used:
        ids = [n["id"] for n in diagram["nodes"] if n["kind"] == kind]
        lines.append(f"  class {','.join(ids)} {kind}")
    return "\n".join(lines) + "\n"


def _module_ref(arch: dict, module: dict, tr: Tr) -> dict[str, Any]:
    return ref(
        "module",
        module.get("id"),
        sha256=digest(module),
        label=tr("label_arch_version", id=arch["id"], version=arch.get("version")),
    )


def _interface_ref(arch: dict, interface: dict, tr: Tr) -> dict[str, Any]:
    return ref(
        "interface",
        interface.get("id"),
        sha256=digest(interface),
        label=tr("label_arch_version", id=arch["id"], version=arch.get("version")),
    )


def _stack_ref(stack: dict, tr: Tr) -> dict[str, Any]:
    return ref(
        "discovery",
        stack.get("discovery_id"),
        sha256=stack.get("sha256"),
        label=tr("label_stack"),
    )


def _outside_interfaces(arch: dict) -> list[dict]:
    return [
        i
        for i in arch.get("interfaces") or []
        if not i.get("consumers") and i.get("kind") in OUTSIDE_KINDS
    ]


def _datastores(stack: dict | None) -> list[tuple[str, str]]:
    if not stack:
        return []
    out = [(str(d), "database") for d in stack.get("databases") or []]
    out += [(str(i), "infrastructure") for i in stack.get("infrastructure") or []]
    return out


def context(snapshot: dict[str, Any]) -> dict[str, Any]:
    _, tr = tr_of(snapshot)
    arch, stack = snapshot["architecture"], snapshot.get("stack")
    d = _Diagram("c4-context", "context", tr("c4.title.context"))
    system = d.node(
        "system",
        arch.get("title"),
        "system",
        arch.get("overview"),
        source=ref("architecture", arch["id"], sha256=arch.get("sha256")),
    )
    for interface in _outside_interfaces(arch):
        outside = d.node(
            f"out-{interface.get('id')}",
            tr("c4.users_of", name=interface.get("name")),
            "person",
            tr("c4.unconsumed", kind=interface.get("kind")),
            source=_interface_ref(arch, interface, tr),
        )
        d.edge(outside, system, tr("c4.uses_kind", kind=interface.get("kind")))
    for name, kind in _datastores(stack):
        node = d.node(
            f"store-{name}",
            name,
            "external" if kind == "infrastructure" else "datastore",
            tr("c4.detected", kind=tr(f"c4.{kind}")),
            source=_stack_ref(stack, tr),
        )
        d.edge(system, node, tr("c4.uses"))
    if not _outside_interfaces(arch):
        d.notes.append(tr("c4.note_no_outside"))
    if not stack:
        d.notes.append(tr("c4.note_no_stack"))
    return d.as_dict(snapshot)


def container(snapshot: dict[str, Any]) -> dict[str, Any]:
    _, tr = tr_of(snapshot)
    arch, stack = snapshot["architecture"], snapshot.get("stack")
    d = _Diagram("c4-container", "container", tr("c4.title.container"))
    boundary = d.group(arch.get("title"))
    for module in arch.get("modules") or []:
        d.node(
            f"mod-{module.get('id')}",
            module.get("name"),
            "container",
            module.get("responsibility"),
            group=boundary,
            source=_module_ref(arch, module, tr),
        )
    for name, kind in _datastores(stack):
        d.node(
            f"store-{name}",
            name,
            "datastore" if kind == "database" else "external",
            tr("c4.detected", kind=tr(f"c4.{kind}")),
            group=boundary,
            source=_stack_ref(stack, tr),
        )
    for module in arch.get("modules") or []:
        for dep in module.get("depends_on") or []:
            d.edge(
                d.id_of(f"mod-{module.get('id')}"),
                d.id_of(f"mod-{dep}"),
                tr("c4.depends_on"),
            )
    for interface in arch.get("interfaces") or []:
        provider = d.id_of(f"mod-{interface.get('module_id')}")
        for consumer in interface.get("consumers") or []:
            d.edge(
                d.id_of(f"mod-{consumer}"),
                provider,
                tr("c4.calls", name=interface.get("name")),
            )
    for interface in _outside_interfaces(arch):
        outside = d.node(
            f"out-{interface.get('id')}",
            tr("c4.users_of", name=interface.get("name")),
            "person",
            source=_interface_ref(arch, interface, tr),
        )
        d.edge(
            outside,
            d.id_of(f"mod-{interface.get('module_id')}"),
            tr("c4.uses_kind", kind=interface.get("kind")),
        )
    if _datastores(stack):
        d.notes.append(tr("c4.note_no_lines"))
    d.notes.append(tr("c4.note_containers"))
    return d.as_dict(snapshot)


def component(snapshot: dict[str, Any]) -> dict[str, Any]:
    _, tr = tr_of(snapshot)
    arch = snapshot["architecture"]
    d = _Diagram("c4-component", "component", tr("c4.title.component"))
    for module in arch.get("modules") or []:
        group = d.group(module.get("name"))
        d.node(
            f"mod-{module.get('id')}",
            tr("c4.core", name=module.get("name")),
            "container",
            module.get("path") or module.get("responsibility"),
            group=group,
            source=_module_ref(arch, module, tr),
        )
        for interface in arch.get("interfaces") or []:
            if interface.get("module_id") == module.get("id"):
                d.node(
                    f"if-{interface.get('id')}",
                    interface.get("name"),
                    "component",
                    f"{interface.get('kind')}: {interface.get('contract') or tr('c4.no_contract')}",
                    group=group,
                    source=_interface_ref(arch, interface, tr),
                )
    for interface in arch.get("interfaces") or []:
        target = d.id_of(f"if-{interface.get('id')}")
        d.edge(d.id_of(f"mod-{interface.get('module_id')}"), target, tr("c4.provides"))
        for consumer in interface.get("consumers") or []:
            d.edge(d.id_of(f"mod-{consumer}"), target, tr("c4.uses"))
    if not arch.get("interfaces"):
        d.notes.append(tr("c4.note_no_interfaces"))
    return d.as_dict(snapshot)


def diagrams(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The three diagrams, or none without an architecture."""
    if not snapshot.get("architecture"):
        return []
    return [context(snapshot), container(snapshot), component(snapshot)]


def document(snapshot: dict[str, Any], built: list[dict[str, Any]]) -> dict[str, Any]:
    """The Markdown document that carries the diagrams, an element table each."""
    lang, tr = tr_of(snapshot)
    src, missing, body = Sources(lang), [], []
    arch = snapshot.get("architecture")
    if arch:
        mark = src.cite(ref("architecture", arch["id"], sha256=arch.get("sha256")))
        body += [
            tr(
                "c4.from",
                id=md_id(arch["id"]),
                version=md_id(arch.get("version")),
                status=md_inline(texts.status(lang, arch.get("status"))),
                mark=mark,
            ),
            "",
        ]
    else:
        body.append(missing_line(lang, tr("c4.no_arch")))
        missing.append(tr("section.all"))
    for diagram in built:
        body += [
            tr(
                "c4.heading",
                title=md_inline(diagram["title"]),
                level=tr(f"c4.level.{diagram['level']}"),
            ),
            "",
            "```mermaid",
            diagram["mermaid"].rstrip("\n"),
            "```",
            "",
            tr("c4.elements"),
            "|---|---|---|---|",
        ]
        for node in diagram["nodes"]:
            mark = src.cite(node["source"]) if node.get("source") else "-"
            body.append(
                f"| {md_inline(node['label'])} | {tr('c4.kind.' + node['kind'])} | "
                f"{md_inline(node.get('detail') or '-')} | {mark} |"
            )
        body += [""] + [f"- {md_inline(note)}" for note in diagram["notes"]] + [""]
    if arch:
        code, observed = c4_code.section(snapshot, src, lang, tr)
        body += code
        if not observed:
            missing.append(tr("section.code_level"))
    credit = tr("c4.credit")
    title = tr("title.c4")
    lines = [f"# {title}", "", f"_{tr('generated_note')} {credit}_", "", *body, ""]
    return {
        "key": "c4",
        "title": title,
        "framework": credit,
        "markdown": "\n".join(lines) + "\n" + src.section(),
        "sources": src.as_list(),
        "missing": missing,
        "claims": [],
        "persons": [],
        "domain": "",
        "focus_area": "",
        "inputs": list(DOCUMENT_INPUTS),
        "input_sha256": input_sha256(snapshot, DOCUMENT_INPUTS),
    }
