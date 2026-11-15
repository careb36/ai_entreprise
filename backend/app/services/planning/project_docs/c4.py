"""C4 diagrams (context, containers, components) from the architecture record.

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
level is not built here: it needs the daemon's code graph
(``services/tools/code_graph.py``) and is left for a later phase.

A diagram is plain data the frontend draws (``nodes``, ``edges``, ``groups``)
plus its Mermaid source, rendered from that data, and the digest of its inputs
(``input_sha256``) the drift check compares. Node ids are numbered here
(``n1``, ``n2``…), never taken from record text, and every label passes
``sanitize.mermaid_label``.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs.layout import (
    GENERATED_NOTE,
    input_sha256,
    missing_line,
)
from services.planning.project_docs.refs import Sources, digest, ref
from services.planning.project_docs.sanitize import (
    md_id,
    md_inline,
    mermaid_label,
    plain_label,
)

C4_INPUTS = ("architecture", "stack")
LEVELS = ("context", "container", "component")
C4_CREDIT = (
    "Structure after the C4 model by Simon Brown (c4model.com, CC BY 4.0); "
    "the content comes from La Fabrique's records."
)
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


def _module_ref(arch: dict, module: dict) -> dict[str, Any]:
    return ref(
        "module",
        module.get("id"),
        sha256=digest(module),
        label=f"architecture {arch['id']} version {arch.get('version')}",
    )


def _interface_ref(arch: dict, interface: dict) -> dict[str, Any]:
    return ref(
        "interface",
        interface.get("id"),
        sha256=digest(interface),
        label=f"architecture {arch['id']} version {arch.get('version')}",
    )


def _stack_ref(stack: dict) -> dict[str, Any]:
    return ref(
        "discovery",
        stack.get("discovery_id"),
        sha256=stack.get("sha256"),
        label="read from the project's files",
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
    arch, stack = snapshot["architecture"], snapshot.get("stack")
    d = _Diagram("c4-context", "context", "System context")
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
            f"Users of {interface.get('name')}",
            "person",
            f"{interface.get('kind')} interface no module consumes",
            source=_interface_ref(arch, interface),
        )
        d.edge(outside, system, f"uses {interface.get('kind')}")
    for name, kind in _datastores(stack):
        node = d.node(
            f"store-{name}",
            name,
            "external" if kind == "infrastructure" else "datastore",
            f"{kind} detected in the project's files",
            source=_stack_ref(stack),
        )
        d.edge(system, node, "uses")
    if not _outside_interfaces(arch):
        d.notes.append(
            "No person or outside system is recorded: the architecture names none, and every interface has a consumer inside the project."
        )
    if not stack:
        d.notes.append(
            "No stack discovery is recorded, so no database or infrastructure is shown."
        )
    return d.as_dict(snapshot)


def container(snapshot: dict[str, Any]) -> dict[str, Any]:
    arch, stack = snapshot["architecture"], snapshot.get("stack")
    d = _Diagram("c4-container", "container", "Containers")
    boundary = d.group(arch.get("title"))
    for module in arch.get("modules") or []:
        d.node(
            f"mod-{module.get('id')}",
            module.get("name"),
            "container",
            module.get("responsibility"),
            group=boundary,
            source=_module_ref(arch, module),
        )
    for name, kind in _datastores(stack):
        d.node(
            f"store-{name}",
            name,
            "datastore" if kind == "database" else "external",
            f"{kind} detected in the project's files",
            group=boundary,
            source=_stack_ref(stack),
        )
    for module in arch.get("modules") or []:
        for dep in module.get("depends_on") or []:
            d.edge(
                d.id_of(f"mod-{module.get('id')}"), d.id_of(f"mod-{dep}"), "depends on"
            )
    for interface in arch.get("interfaces") or []:
        provider = d.id_of(f"mod-{interface.get('module_id')}")
        for consumer in interface.get("consumers") or []:
            d.edge(
                d.id_of(f"mod-{consumer}"), provider, f"calls {interface.get('name')}"
            )
    for interface in _outside_interfaces(arch):
        outside = d.node(
            f"out-{interface.get('id')}",
            f"Users of {interface.get('name')}",
            "person",
            source=_interface_ref(arch, interface),
        )
        d.edge(
            outside,
            d.id_of(f"mod-{interface.get('module_id')}"),
            f"uses {interface.get('kind')}",
        )
    if _datastores(stack):
        d.notes.append(
            "The records do not say which module uses each database or infrastructure item, so no line is drawn to it."
        )
    d.notes.append(
        "The architecture does not record which modules are deployed separately; each module is shown as a container."
    )
    return d.as_dict(snapshot)


def component(snapshot: dict[str, Any]) -> dict[str, Any]:
    arch = snapshot["architecture"]
    d = _Diagram("c4-component", "component", "Components")
    for module in arch.get("modules") or []:
        group = d.group(module.get("name"))
        d.node(
            f"mod-{module.get('id')}",
            f"{module.get('name')} core",
            "container",
            module.get("path") or module.get("responsibility"),
            group=group,
            source=_module_ref(arch, module),
        )
        for interface in arch.get("interfaces") or []:
            if interface.get("module_id") == module.get("id"):
                d.node(
                    f"if-{interface.get('id')}",
                    interface.get("name"),
                    "component",
                    f"{interface.get('kind')}: {interface.get('contract') or 'no contract recorded'}",
                    group=group,
                    source=_interface_ref(arch, interface),
                )
    for interface in arch.get("interfaces") or []:
        target = d.id_of(f"if-{interface.get('id')}")
        d.edge(d.id_of(f"mod-{interface.get('module_id')}"), target, "provides")
        for consumer in interface.get("consumers") or []:
            d.edge(d.id_of(f"mod-{consumer}"), target, "uses")
    if not arch.get("interfaces"):
        d.notes.append(
            "The architecture records no interface, so each module shows only its core."
        )
    return d.as_dict(snapshot)


def diagrams(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """The three diagrams, or none without an architecture."""
    if not snapshot.get("architecture"):
        return []
    return [context(snapshot), container(snapshot), component(snapshot)]


def document(snapshot: dict[str, Any], built: list[dict[str, Any]]) -> dict[str, Any]:
    """The Markdown document that carries the diagrams, an element table each."""
    src, missing, body = Sources(), [], []
    arch = snapshot.get("architecture")
    if arch:
        body.append(
            f"From architecture {md_id(arch['id'])} version {md_id(arch.get('version'))} ({md_inline(arch.get('status'))}) {src.cite(ref('architecture', arch['id'], sha256=arch.get('sha256')))}."
        )
        body.append("")
    else:
        body.append(
            missing_line(
                "the project has no architecture in La Fabrique, so no diagram is drawn."
            )
        )
        missing.append("all")
    for diagram in built:
        body += [
            f"## {md_inline(diagram['title'])} ({diagram['level']} level)",
            "",
            "```mermaid",
            diagram["mermaid"].rstrip("\n"),
            "```",
            "",
        ]
        body += ["| Element | Kind | Description | Source |", "|---|---|---|---|"]
        for node in diagram["nodes"]:
            mark = src.cite(node["source"]) if node.get("source") else "-"
            body.append(
                f"| {md_inline(node['label'])} | {node['kind']} | {md_inline(node.get('detail') or '-')} | {mark} |"
            )
        body += [""] + [f"- {md_inline(note)}" for note in diagram["notes"]] + [""]
    if arch:
        body += [
            "## Code level",
            "",
            missing_line(
                "the code level is not compiled yet; it will come from the project's code graph."
            ),
            "",
        ]
        missing.append("code level")
    lines = [
        "# Architecture diagrams (C4)",
        "",
        f"_{GENERATED_NOTE} {C4_CREDIT}_",
        "",
        *body,
        "",
    ]
    return {
        "key": "c4",
        "title": "Architecture diagrams (C4)",
        "framework": C4_CREDIT,
        "markdown": "\n".join(lines) + "\n" + src.section(),
        "sources": src.as_list(),
        "missing": missing,
        "claims": [],
        "persons": [],
        "domain": "",
        "focus_area": "",
        "inputs": list(C4_INPUTS),
        "input_sha256": input_sha256(snapshot, C4_INPUTS),
    }
