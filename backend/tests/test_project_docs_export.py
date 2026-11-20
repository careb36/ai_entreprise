"""Exporting a documentation pack to DOCX and PDF (issue #694, phase 4 of
``docs/design/PROJECT_DOCUMENTATION.md``).

The export reads the stored pack's Markdown through our parser of the pack's
own subset (``export_markdown``), never a request field; it carries the
citations, the claim marks, the person sections and the Mermaid sources;
the bytes are the same for the same pack, so the export's manifest lists a
sha256 a downloaded file can be checked against.

What is verified here, and what is only declared: the PDF tests check, on the
bytes, the PDF/A-3b and PDF/UA-1 declarations in the XMP (with the extension
schema PDF/A asks for), the sRGB output intent, that every font is embedded,
the structure tree (headings, tables with headers, lists, block quotes), the
language and the displayed title. A full ISO validation needs veraPDF (Java):
``test_verapdf_passes_the_pdf_for_pdf_a_3b_and_pdf_ua_1`` runs it when a
``verapdf`` command is on the PATH (or ``VERAPDF`` names one) and is skipped,
not passed, otherwise. The PDF tests are skipped with a reason when
WeasyPrint's native libraries (Pango, HarfBuzz) cannot load here; the DOCX,
parser and degradation tests run regardless.
"""

import io
import os
import re
import shutil
import subprocess
import zipfile
import zlib
from datetime import UTC, datetime

import pytest
from models import DocumentationPack
from services.planning.project_docs import export as exports
from services.planning.project_docs import export_docx, export_pdf
from services.planning.project_docs import export_markdown as md
from services.planning.project_docs import pack_manifest as manifests

PDF_REASON = export_pdf.unavailable_reason()
needs_pdf = pytest.mark.skipif(
    PDF_REASON is not None, reason=f"WeasyPrint unavailable here: {PDF_REASON}"
)

CHARTER = """# Acta de constitución

_Compilado por La Fabrique a partir de sus registros._

## Antecedentes

> **Afirmación.** Escrita por el TechWriter; no comprobada.
>
> El equipo necesita un tablero común: ¿por qué? Año, señal, acción.

## Justificación

> **Para que la complete una persona.** Por qué vale la pena la inversión.

## Requisitos

- REQ-1: Listar las tareas \\[S1\\]
  - REQ-1.1: dado un usuario, ve sus tareas \\[S1\\]
- REQ-2: Mover una tarea \\[S2\\]

| Requisito | Tarea | Estado | Fuente |
|---|---|---|---|
| REQ-1 | T-1 | bloqueada \\| QA | \\[S1\\] |
| REQ-2 | ninguna | - | \\[S2\\] |

## Fuentes

- S1: Requisito REQ-1 · `650dd345323fe236df9bad21da7a5f2b34a0c31ac96c1de15475006a7b31cec6`
- S2: Requisito REQ-2 · `5123b07b078992e7b5baf0e501ebe2097bd9910fdc41fe5ba34846948b7dc162`
"""

C4 = """# Diagramas de arquitectura (C4)

## Contexto

```mermaid
flowchart LR
  n1["Servicio del tablero"]
  n2[("sqlite")]
  n1 -->|"usa"| n2
```
"""


def _pack(**fields):
    defaults = {
        "id": 11,
        "company_id": 4,
        "version": 3,
        "status": "draft",
        "language": "es",
        "inputs_sha256": "ab" * 32,
        "created_at": datetime(2026, 11, 19, 10, 30, 15, tzinfo=UTC),
        "documents": [
            {"key": "charter", "title": "Acta de constitución", "markdown": CHARTER},
            {"key": "c4", "title": "Diagramas de arquitectura (C4)", "markdown": C4},
        ],
    }
    defaults.update(fields)
    return DocumentationPack(**defaults)


# --- the parser ------------------------------------------------------------------


def test_the_parser_reads_the_packs_subset_into_blocks():
    blocks = md.parse(CHARTER)
    kinds = [type(b).__name__ for b in blocks]
    assert kinds[:4] == ["Heading", "Paragraph", "Heading", "Quote"]
    quote = blocks[3]
    assert md.plain(quote.blocks[0].inlines).startswith("Afirmación.")
    assert quote.blocks[0].inlines[0] == ("strong", "Afirmación.")
    lists = [b for b in blocks if isinstance(b, md.ListBlock)]
    first = lists[0]
    assert [md.plain(i.inlines) for i in first.items] == [
        "REQ-1: Listar las tareas [S1]",
        "REQ-2: Mover una tarea [S2]",
    ]
    assert md.plain(first.items[0].children[0].items[0].inlines).startswith("REQ-1.1")
    table = next(b for b in blocks if isinstance(b, md.Table))
    assert [md.plain(c) for c in table.header] == [
        "Requisito",
        "Tarea",
        "Estado",
        "Fuente",
    ]
    # An escaped pipe stays inside its cell; the escapes are resolved.
    assert [md.plain(c) for c in table.rows[0]] == [
        "REQ-1",
        "T-1",
        "bloqueada | QA",
        "[S1]",
    ]
    code = md.parse(C4)[-1]
    assert isinstance(code, md.Code) and code.language == "mermaid"
    assert 'n1 -->|"usa"| n2' in code.text


def test_escaped_text_never_becomes_structure_or_markup():
    inlines = md.parse_inline(
        r"\*\*no\*\* \_no\_ &lt;img src=x&gt; &amp; snake_case `a*b` **sí** _sí_"
    )
    assert inlines == [
        ("text", "**no** _no_ <img src=x> & snake_case "),
        ("code", "a*b"),
        ("text", " "),
        ("strong", "sí"),
        ("text", " "),
        ("em", "sí"),
    ]
    # A record's markup reaches the PDF's HTML as text, never as an element.
    blocks = md.parse("&lt;script&gt;alert(1)&lt;/script&gt; \\[S1\\]")
    html = export_pdf._blocks(blocks, "m")
    assert "<script" not in html and "&lt;script&gt;" in html


# --- the source ------------------------------------------------------------------


def test_the_export_reads_the_stored_pack_and_names_its_manifest():
    pack = _pack()
    source = exports.source_of(pack)
    assert [d.title for d in source.documents] == [
        "Acta de constitución",
        "Diagramas de arquitectura (C4)",
    ]
    cover = dict(source.cover)
    assert cover["Resumen del manifiesto del paquete (sha256)"] == manifests.digest(
        manifests.build(pack)
    )
    assert cover["Estado"] == "Borrador, sin aprobar"
    assert cover["Compilado el (UTC)"] == "2026-11-19T10:30:15Z"
    assert source.language == "es" and source.compiled_at == "2026-11-19T10:30:15Z"


def test_the_export_labels_hold_the_same_keys_in_both_languages():
    assert set(exports.LABELS["en"]) == set(exports.LABELS["es"])


# --- DOCX ------------------------------------------------------------------------


def _docx_text(data):
    from docx import Document

    document = Document(io.BytesIO(data))
    paragraphs = [(p.style.name, p.text) for p in document.paragraphs]
    tables = [[[c.text for c in row.cells] for row in t.rows] for t in document.tables]
    return document, paragraphs, tables


def test_the_docx_carries_the_documents_citations_claims_and_person_sections():
    rendered = exports.export(_pack(), "docx")
    assert rendered.media_type.endswith("wordprocessingml.document")
    assert rendered.file_name == "documentation-pack-11-v3.docx"
    document, paragraphs, tables = _docx_text(rendered.data)
    headings = [t for s, t in paragraphs if s == "Heading 1"]
    assert "Acta de constitución" in headings
    assert "Diagramas de arquitectura (C4)" in headings
    quotes = [t for s, t in paragraphs if s == "Quote"]
    assert any(t.startswith("Afirmación.") for t in quotes)
    assert any(t.startswith("Para que la complete una persona.") for t in quotes)
    assert (
        "List Bullet 2",
        "REQ-1.1: dado un usuario, ve sus tareas [S1]",
    ) in paragraphs
    assert tables[0][1] == ["REQ-1", "T-1", "bloqueada | QA", "[S1]"]
    assert any("Mermaid" in t for _, t in paragraphs)
    assert ("Code Block", '  n1 -->|"usa"| n2') in paragraphs
    core = document.core_properties
    assert core.created == datetime(2026, 11, 19, 10, 30, 15, tzinfo=UTC)
    assert core.language == "es"
    styles = zipfile.ZipFile(io.BytesIO(rendered.data)).read("word/styles.xml")
    assert b'w:lang w:val="es-ES"' in styles
    # The header row repeats on every page.
    body = zipfile.ZipFile(io.BytesIO(rendered.data)).read("word/document.xml")
    assert b'<w:tblHeader w:val="true"/>' in body


def test_the_docx_bytes_are_the_same_for_the_same_pack():
    first = exports.export(_pack(), "docx")
    second = exports.export(_pack(), "docx")
    assert first.data == second.data and first.sha256 == second.sha256
    entries = zipfile.ZipFile(io.BytesIO(first.data)).infolist()
    assert {e.date_time for e in entries} == {export_docx.ZIP_TIME}
    # The status is on the cover, so approving changes the export.
    assert exports.export(_pack(status="approved"), "docx").sha256 != first.sha256


# --- PDF -------------------------------------------------------------------------


def _pdf_text(data):
    """The PDF's bytes with every compressed stream inflated beside them."""
    parts = [data]
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        try:
            parts.append(zlib.decompress(match.group(1)))
        except zlib.error:
            continue
    return b"\n".join(parts)


@pytest.fixture(scope="module")
def pdf_bytes():
    if PDF_REASON is not None:
        pytest.skip(f"WeasyPrint unavailable here: {PDF_REASON}")
    return exports.export(_pack(), "pdf").data


@needs_pdf
def test_the_pdf_declares_pdf_a_3b_and_pdf_ua_1_in_its_xmp(pdf_bytes):
    text = _pdf_text(pdf_bytes)
    assert pdf_bytes.startswith(b"%PDF-1.7")
    xmp = text[text.index(b"<x:xmpmeta") : text.index(b"</x:xmpmeta>")]
    assert re.search(rb'pdfaid:part="3"|<pdfaid:part>3<', xmp)
    assert re.search(rb'pdfaid:conformance="B"|<pdfaid:conformance>B<', xmp)
    assert b"<pdfuaid:part>1</pdfuaid:part>" in xmp
    # PDF/A asks for a schema outside its predefined ones to be described.
    assert b"<pdfaSchema:prefix>pdfuaid</pdfaSchema:prefix>" in xmp
    assert "Paquete de documentación del proyecto 3".encode() in xmp
    assert b"2026-11-19T10:30:15Z" in xmp


@needs_pdf
def test_the_pdf_is_tagged_embeds_its_fonts_and_names_its_language(pdf_bytes):
    text = _pdf_text(pdf_bytes)
    assert b"/StructTreeRoot" in text
    assert re.search(rb"/MarkInfo\s*<<\s*/Marked\s+true", text)
    assert re.search(rb"/DisplayDocTitle\s+true", text)
    assert re.search(rb"/Lang\s*\(es\)", text)
    assert b"/GTS_PDFA1" in text and b"/OutputIntents" in text
    for tag in (
        b"/H1",
        b"/H2",
        b"/P",
        b"/L",
        b"/LI",
        b"/Table",
        b"/TH",
        b"/TD",
        b"/BlockQuote",
    ):
        assert re.search(rb"/S\s*" + re.escape(tag) + rb"\b", text), tag
    descriptors = len(re.findall(rb"/Type\s*/FontDescriptor", text))
    embedded = len(re.findall(rb"/FontFile[23]?\s", text))
    assert descriptors and embedded == descriptors
    # Header ids are renumbered in order, so the bytes do not hold addresses.
    assert re.search(rb"/ID\s*\(th1\)", text)
    assert b"/JavaScript" not in text and b"/URI" not in text


@needs_pdf
def test_the_pdf_bytes_are_the_same_for_the_same_pack(pdf_bytes):
    again = exports.export(_pack(), "pdf")
    assert again.data == pdf_bytes
    assert again.conformance == ["PDF/A-3b", "PDF/UA-1"]
    assert again.renderer.startswith("WeasyPrint ")


@needs_pdf
def test_verapdf_passes_the_pdf_for_pdf_a_3b_and_pdf_ua_1(pdf_bytes, tmp_path):
    command = os.environ.get("VERAPDF") or shutil.which("verapdf")
    if not command:
        pytest.skip("veraPDF is not installed here; the ISO validation did not run")
    target = tmp_path / "pack.pdf"
    target.write_bytes(pdf_bytes)
    for flavour in ("3b", "ua1"):
        result = subprocess.run(
            [command, "-f", flavour, "--format", "text", str(target)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert re.search(r"^PASS ", result.stdout, re.MULTILINE), (
            flavour,
            result.stdout,
        )


def test_the_pdf_html_fetches_nothing_and_holds_no_record_markup():
    source = exports.source_of(
        _pack(
            documents=[
                {
                    "key": "x",
                    "title": "<b>X</b>",
                    "markdown": "# T\n\n<img src=http://h/>\n",
                }
            ]
        )
    )
    html = export_pdf.html_of(source)
    assert "<img" not in html and "&lt;img src=http://h/&gt;" in html
    assert "<b>X</b>" not in html
    with pytest.raises(ValueError):
        export_pdf._refuse("http://example.invalid/font.ttf")


# --- a missing renderer degrades -----------------------------------------------------


def test_a_missing_native_library_makes_pdf_unavailable_and_docx_still_works(
    monkeypatch,
):
    monkeypatch.setattr(export_pdf, "_checked", True)
    monkeypatch.setattr(
        export_pdf, "_import_error", "WeasyPrint cannot load: no library called pango"
    )
    with pytest.raises(exports.ExportUnavailable) as raised:
        exports.export(_pack(), "pdf")
    assert raised.value.format == "pdf" and "pango" in raised.value.reason
    assert exports.availability()["pdf"] == {
        "available": False,
        "reason": "WeasyPrint cannot load: no library called pango",
    }
    assert exports.export(_pack(), "docx").data[:2] == b"PK"


def test_an_import_failure_is_caught_and_named(monkeypatch):
    def broken():
        raise OSError("cannot load library 'libpango-1.0-0'")

    monkeypatch.setattr(export_pdf, "_checked", False)
    monkeypatch.setattr(export_pdf, "_import_error", None)
    monkeypatch.setattr(export_pdf, "_load", broken)
    assert export_pdf.unavailable_reason() == (
        "WeasyPrint cannot load: cannot load library 'libpango-1.0-0'"
    )


# --- the export's manifest -----------------------------------------------------------


def test_the_export_manifest_lists_the_exports_sha256_beside_the_packs_files():
    pack = _pack()
    rendered = exports.export(pack, "docx")
    built = manifests.build_export(
        pack,
        fmt="docx",
        file_name=rendered.file_name,
        data=rendered.data,
        renderer=rendered.renderer,
        conformance=rendered.conformance,
    )
    assert built["type"] == manifests.EXPORT_MANIFEST_TYPE
    assert built["pack_manifest_sha256"] == manifests.digest(manifests.build(pack))
    entry = next(
        f for f in built["files"] if f["path"] == "export/documentation-pack-11-v3.docx"
    )
    assert entry["sha256"] == rendered.sha256 and entry["bytes"] == len(rendered.data)
    files = {
        f"docs/project/{d['key']}.md": d["markdown"].encode() for d in pack.documents
    }
    files[entry["path"]] = rendered.data
    signature = manifests.sign(built)
    assert manifests.verify(built, signature, files)["ok"] is True
    files[entry["path"]] = rendered.data + b"\0"
    result = manifests.verify(built, signature, files)
    assert result["ok"] is False and result["mismatched"] == [entry["path"]]
