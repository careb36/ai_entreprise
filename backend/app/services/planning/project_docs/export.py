"""Exporting a stored documentation pack to DOCX and PDF (issue #694, phase 4).

``docs/design/PROJECT_DOCUMENTATION.md``, "Export to DOCX and PDF" and the
decision of 2026-11-19: DOCX with python-docx, PDF with WeasyPrint as PDF/A-3b
(ISO 19005-3) declaring PDF/UA-1 (ISO 14289-1).

- **From the stored pack only.** The input is ``pack.documents`` as compiled,
  never anything the client sends: the same Markdown the repository gets,
  parsed by ``export_markdown`` (the pack's own subset). Citations, sources,
  claim marks, person sections and the person's statements are text and
  quotes in that Markdown, so they are carried as they are.
- **A cover page** names the pack, its version and status (a draft says so),
  its compile time, its inputs digest and the digest of the pack's manifest
  (``pack_manifest``), so a printed copy points back to the record.
- **The same bytes every time.** Both renderers fix every date to the pack's
  compile time and every identifier to the content, so an export of one pack
  in one status gives the same sha256, and the export's manifest
  (``pack_manifest.build_export``) can be checked against a file found later.
- **Optional renderers degrade.** Each renderer's library is imported only
  when an export is asked for. A library or native library that is missing
  (WeasyPrint without Pango, for instance) makes that one format
  ``ExportUnavailable`` with a named reason; the backend and the other format
  keep working.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from models import DocumentationPack

from services.planning.project_docs import export_markdown, texts
from services.planning.project_docs import pack_manifest as manifests
from services.planning.project_docs.pack import file_name

FORMATS = ("docx", "pdf")
MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
# What each format declares; ``docs/design/PROJECT_DOCUMENTATION.md`` says
# which of it the tests verify and which is only declared.
CONFORMANCE = {"docx": [], "pdf": ["PDF/A-3b", "PDF/UA-1"]}

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "cover_title": "Project documentation pack",
        "version": "Pack version",
        "status": "Status",
        "language": "Language",
        "compiled_at": "Compiled at (UTC)",
        "inputs_sha256": "Inputs digest (sha256)",
        "manifest_sha256": "Pack manifest digest (sha256)",
        "contents": "Contents",
        "note": "Exported by La Fabrique from the stored pack: the same documents, "
        "citations, claim marks and person sections as the Markdown in the "
        "repository. The sha256 of this file is listed in its export manifest.",
        "mermaid": "Diagram, as its Mermaid source (drawn in the app)",
        "status.draft": "Draft, not approved",
        "status.approved": "Approved",
        "status.superseded": "Superseded",
    },
    "es": {
        "cover_title": "Paquete de documentación del proyecto",
        "version": "Versión del paquete",
        "status": "Estado",
        "language": "Idioma",
        "compiled_at": "Compilado el (UTC)",
        "inputs_sha256": "Resumen de las entradas (sha256)",
        "manifest_sha256": "Resumen del manifiesto del paquete (sha256)",
        "contents": "Contenido",
        "note": "Exportado por La Fabrique desde el paquete guardado: los mismos "
        "documentos, citas, marcas de afirmación y secciones para una persona "
        "que el Markdown del repositorio. El sha256 de este archivo figura en "
        "su manifiesto de exportación.",
        "mermaid": "Diagrama, como su código Mermaid (se dibuja en la aplicación)",
        "status.draft": "Borrador, sin aprobar",
        "status.approved": "Aprobado",
        "status.superseded": "Reemplazado",
    },
}


class ExportUnavailable(Exception):
    """A format cannot be produced here; ``reason`` names why (a missing library)."""

    def __init__(self, fmt: str, reason: str) -> None:
        super().__init__(f"{fmt} export is unavailable: {reason}")
        self.format = fmt
        self.reason = reason


@dataclass(frozen=True)
class Document:
    """One document of the pack, parsed: its file name, title and blocks."""

    key: str
    title: str
    blocks: list[export_markdown.Block]


@dataclass(frozen=True)
class Source:
    """What a renderer gets: the cover and the documents, nothing else."""

    language: str
    title: str
    compiled_at: str
    cover: list[tuple[str, str]]
    contents_heading: str
    contents: list[str]
    note: str
    mermaid_label: str
    documents: list[Document]
    identifier: str


@dataclass(frozen=True)
class Export:
    format: str
    data: bytes
    file_name: str
    media_type: str
    sha256: str
    renderer: str
    conformance: list[str]


def labels(lang: str) -> dict[str, str]:
    return LABELS[texts.language(lang)]


def export_file_name(pack: DocumentationPack, fmt: str) -> str:
    return f"documentation-pack-{pack.id}-v{pack.version}.{fmt}"


def source_of(pack: DocumentationPack) -> Source:
    """The stored pack as a renderer reads it: parsed documents and the cover."""
    lang = texts.language(pack.language)
    tr = labels(lang)
    documents = []
    for doc in pack.documents or []:
        key = str(doc.get("key"))
        title = str(doc.get("title") or file_name(key))
        markdown = str(doc.get("markdown") or "")
        documents.append(Document(key, title, export_markdown.parse(markdown)))
    built = manifests.build(pack)
    manifest_sha256 = manifests.digest(built)
    status = str(pack.status or "draft")
    cover = [
        (tr["version"], str(pack.version)),
        (tr["status"], tr.get(f"status.{status}", status)),
        (tr["language"], lang),
        (tr["compiled_at"], built["compiled_at"]),
        (tr["inputs_sha256"], str(pack.inputs_sha256 or "")),
        (tr["manifest_sha256"], manifest_sha256),
    ]
    return Source(
        language=lang,
        title=f"{tr['cover_title']} {pack.version}",
        compiled_at=built["compiled_at"],
        cover=cover,
        contents_heading=tr["contents"],
        contents=[d.title for d in documents],
        note=tr["note"],
        mermaid_label=tr["mermaid"],
        documents=documents,
        # The file identifier: from the content, never random.
        identifier=hashlib.sha256(f"{manifest_sha256}:{status}".encode()).hexdigest()[
            :32
        ],
    )


def availability() -> dict[str, dict[str, Any]]:
    """Whether each format can be produced here, and if not, why."""
    from services.planning.project_docs import export_docx, export_pdf

    out = {}
    for fmt, module in (("docx", export_docx), ("pdf", export_pdf)):
        reason = module.unavailable_reason()
        out[fmt] = {"available": reason is None, "reason": reason}
    return out


def export(pack: DocumentationPack, fmt: str) -> Export:
    """Render *pack* as *fmt*; ``ExportUnavailable`` when its renderer is missing."""
    from services.planning.project_docs import export_docx, export_pdf

    if fmt not in FORMATS:
        raise ValueError(f"unknown export format: {fmt}")
    module = export_docx if fmt == "docx" else export_pdf
    reason = module.unavailable_reason()
    if reason is not None:
        raise ExportUnavailable(fmt, reason)
    data = module.render(source_of(pack))
    return Export(
        format=fmt,
        data=data,
        file_name=export_file_name(pack, fmt),
        media_type=MEDIA_TYPES[fmt],
        sha256=hashlib.sha256(data).hexdigest(),
        renderer=module.renderer_name(),
        conformance=list(CONFORMANCE[fmt]),
    )
