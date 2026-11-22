"""A documentation pack as a PDF/A-3b file declaring PDF/UA-1, with WeasyPrint.

Issue #694, phase 4; the decision of 2026-11-19 in
``docs/archive/design/PROJECT_DOCUMENTATION.md``. ``export.source_of`` gives the parsed
pack; this module writes it as HTML it builds itself (every piece of text
escaped, no markup from a record) and lets WeasyPrint lay it out.

- **PDF/A-3b** (ISO 19005-3): WeasyPrint's ``pdf/a-3b`` variant embeds every
  font, adds the sRGB output intent, the file identifier and the XMP
  ``pdfaid`` declaration.
- **PDF/UA-1** (ISO 14289-1): rendered with ``pdf_tags`` (the structure tree
  from the HTML: H1 to H3, P, L/LI/Lbl/LBody, Table/THead/TBody/TR/TH/TD with
  each cell's headers, BlockQuote), the document language in the catalog, the
  title shown in the window (``DisplayDocTitle``), and the XMP ``pdfuaid``
  declaration with its PDF/A extension schema, added by ``_finish`` because a
  WeasyPrint variant declares one standard only. No page header or footer:
  WeasyPrint leaves margin-box text outside the structure tree, which PDF/UA-1
  does not allow.
- **Reproducible**: the dates are the pack's compile time; the table-header
  ids WeasyPrint draws from memory addresses are renumbered in document order
  (``_finish``), and the file identifier is the hash of the content.
- **Nothing is fetched**: the URL fetcher refuses every URL, so no font, image
  or stylesheet can be pulled from a network or the disk by the content.

WeasyPrint (BSD-3-Clause) needs Pango and HarfBuzz from the system; it is
imported only when a PDF is rendered, and a missing native library makes PDF
export unavailable with the library's message, never the backend.
"""

from __future__ import annotations

import html
import os
import threading
from datetime import UTC, datetime
from typing import Any

from services.planning.project_docs import export_markdown as md

RENDERER = "WeasyPrint"
VARIANT = "pdf/a-3b"

_RENDER_LOCK = threading.Lock()
_import_error: str | None = None
_checked = False

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
html { font-family: "DejaVu Sans", "Liberation Sans", sans-serif; font-size: 9.5pt;
  line-height: 1.35; color: #1b1b1b; }
h1 { font-size: 17pt; margin: 0 0 8pt 0; }
h2 { font-size: 13pt; margin: 12pt 0 4pt 0; }
h3 { font-size: 11pt; margin: 10pt 0 4pt 0; }
h4, h5, h6 { font-size: 10pt; margin: 8pt 0 3pt 0; }
section.document { break-before: page; }
p { margin: 3pt 0; }
ul, ol { margin: 3pt 0 3pt 14pt; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 5pt 0; font-size: 7.5pt; }
th, td { border: 0.5pt solid #8a8a8a; padding: 2pt 3pt; vertical-align: top;
  text-align: left; overflow-wrap: anywhere; }
th { background: #ececec; }
blockquote { margin: 5pt 0; padding: 3pt 8pt; border-left: 2.5pt solid #6b6b6b;
  background: #f4f4f4; }
.code, pre { font-family: "DejaVu Sans Mono", "Liberation Mono", monospace; }
.code { font-size: 8.5pt; overflow-wrap: anywhere; }
pre { font-size: 7.5pt; white-space: pre-wrap; overflow-wrap: anywhere;
  border: 0.5pt solid #b0b0b0; padding: 4pt; margin: 3pt 0 6pt 0; }
.diagram-label { font-style: italic; }
.cover .value { overflow-wrap: anywhere; }
"""

# The PDF/UA identification schema and its PDF/A extension-schema description
# (ISO 19005-3, 6.6.2.3: a property outside the predefined schemas is described).
_UA_XMP = (
    b'<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    b'xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/" rdf:about="">'
    b"<pdfuaid:part>1</pdfuaid:part></rdf:Description>"
    b'<rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    b'xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/" '
    b'xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#" '
    b'xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#" rdf:about="">'
    b'<pdfaExtension:schemas><rdf:Bag><rdf:li rdf:parseType="Resource">'
    b"<pdfaSchema:schema>PDF/UA Universal Accessibility Schema</pdfaSchema:schema>"
    b"<pdfaSchema:namespaceURI>http://www.aiim.org/pdfua/ns/id/</pdfaSchema:namespaceURI>"
    b"<pdfaSchema:prefix>pdfuaid</pdfaSchema:prefix>"
    b'<pdfaSchema:property><rdf:Seq><rdf:li rdf:parseType="Resource">'
    b"<pdfaProperty:name>part</pdfaProperty:name>"
    b"<pdfaProperty:valueType>Integer</pdfaProperty:valueType>"
    b"<pdfaProperty:category>internal</pdfaProperty:category>"
    b"<pdfaProperty:description>Indicates, which part of ISO 14289 standard is "
    b"followed</pdfaProperty:description>"
    b"</rdf:li></rdf:Seq></pdfaSchema:property>"
    b"</rdf:li></rdf:Bag></pdfaExtension:schemas></rdf:Description>"
)


def _load() -> Any:
    import weasyprint

    return weasyprint


def unavailable_reason() -> str | None:
    """None when WeasyPrint and its native libraries load, else why not."""
    global _checked, _import_error
    if not _checked:
        try:
            _load()
            _import_error = None
        except (ImportError, OSError) as exc:
            _import_error = f"WeasyPrint cannot load: {exc}".splitlines()[0][:300]
        _checked = True
    return _import_error


def renderer_name() -> str:
    from importlib.metadata import version

    return f"{RENDERER} {version('weasyprint')}"


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _inline(inlines: list[md.Inline]) -> str:
    out = []
    for kind, text in inlines:
        body = _e(text)
        if kind == "strong":
            body = f"<strong>{body}</strong>"
        elif kind == "em":
            body = f"<em>{body}</em>"
        elif kind == "strong_em":
            body = f"<strong><em>{body}</em></strong>"
        elif kind == "code":
            body = f'<span class="code">{body}</span>'
        out.append(body)
    return "".join(out)


def _list(block: md.ListBlock) -> str:
    tag = "ol" if block.ordered else "ul"
    items = []
    for item in block.items:
        children = "".join(_list(child) for child in item.children)
        items.append(f"<li>{_inline(item.inlines)}{children}</li>")
    return f"<{tag}>{''.join(items)}</{tag}>"


def _table(block: md.Table) -> str:
    head = "".join(f'<th scope="col">{_inline(c)}</th>' for c in block.header)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
        for row in block.rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def _blocks(blocks: list[md.Block], mermaid_label: str) -> str:
    out = []
    for block in blocks:
        if isinstance(block, md.Heading):
            level = min(block.level, 6)
            out.append(f"<h{level}>{_inline(block.inlines)}</h{level}>")
        elif isinstance(block, md.Paragraph):
            out.append(f"<p>{_inline(block.inlines)}</p>")
        elif isinstance(block, md.ListBlock):
            out.append(_list(block))
        elif isinstance(block, md.Table):
            out.append(_table(block))
        elif isinstance(block, md.Quote):
            out.append(
                f"<blockquote>{_blocks(block.blocks, mermaid_label)}</blockquote>"
            )
        elif isinstance(block, md.Code):
            if block.language == "mermaid":
                out.append(f'<p class="diagram-label">{_e(mermaid_label)}</p>')
            out.append(f"<pre>{_e(block.text)}</pre>")
    return "".join(out)


def html_of(source: Any) -> str:
    """The pack (``export.Source``) as the HTML WeasyPrint lays out."""
    cover = "".join(
        f'<p><strong>{_e(label)}:</strong> <span class="value">{_e(value)}</span></p>'
        for label, value in source.cover
    )
    contents = "".join(f"<li>{_e(title)}</li>" for title in source.contents)
    documents = "".join(
        f'<section class="document">{_blocks(doc.blocks, source.mermaid_label)}</section>'
        for doc in source.documents
    )
    return (
        f'<!DOCTYPE html><html lang="{_e(source.language)}"><head>'
        f'<meta charset="utf-8"><title>{_e(source.title)}</title>'
        '<meta name="author" content="La Fabrique">'
        f'<meta name="dcterms.created" content="{_e(source.compiled_at)}">'
        f'<meta name="dcterms.modified" content="{_e(source.compiled_at)}">'
        f"<style>{CSS}</style></head><body>"
        f'<section class="cover"><h1>{_e(source.title)}</h1>{cover}'
        f"<p>{_e(source.note)}</p><h2>{_e(source.contents_heading)}</h2>"
        f"<ul>{contents}</ul></section>{documents}</body></html>"
    )


def _refuse(url: str, *args: Any, **kwargs: Any) -> Any:
    raise ValueError("the export fetches nothing")


def _finish(document: Any, pdf: Any) -> None:
    """Renumber the table-header ids in order and add the PDF/UA declaration."""
    import pydyf

    renumbered: dict[str, str] = {}
    for obj in pdf.objects:
        if isinstance(obj, dict) and obj.get("S") == "/TH" and "ID" in obj:
            old = str(obj["ID"].string)
            renumbered[old] = f"th{len(renumbered) + 1}"
            obj["ID"] = pydyf.String(renumbered[old])
    for obj in pdf.objects:
        attributes = obj.get("A") if isinstance(obj, dict) else None
        if isinstance(attributes, dict) and "Headers" in attributes:
            attributes["Headers"] = pydyf.Array(
                pydyf.String(renumbered.get(str(h.string), str(h.string)))
                for h in attributes["Headers"]
            )
    reference = pdf.catalog["Metadata"]
    if isinstance(reference, bytes):
        reference = reference.decode("ascii")
    stream = pdf.objects[int(str(reference).split()[0])]
    content = b"".join(stream.stream)
    stream.stream = [content.replace(b"</rdf:RDF>", _UA_XMP + b"</rdf:RDF>", 1)]


def _epoch(compiled_at: str) -> str:
    try:
        moment = datetime.strptime(compiled_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "315532800"  # 1980-01-01, the zip epoch
    return str(int(moment.replace(tzinfo=UTC).timestamp()))


def render(source: Any) -> bytes:
    """The pack as PDF/A-3b bytes, tagged, declaring PDF/UA-1.

    fontTools stamps each font subset with the time it is written unless
    ``SOURCE_DATE_EPOCH`` names one (the reproducible-builds convention); it is
    set to the pack's compile time for the render only, under a lock, so one
    PDF renders at a time in a process.
    """
    weasyprint = _load()
    with _RENDER_LOCK:
        before = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = _epoch(source.compiled_at)
        try:
            document = weasyprint.HTML(
                string=html_of(source), url_fetcher=_refuse
            ).render()
            return document.write_pdf(
                pdf_variant=VARIANT,
                pdf_tags=True,
                finisher=_finish,
                pdf_identifier=True,
            )
        finally:
            if before is None:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            else:
                os.environ["SOURCE_DATE_EPOCH"] = before
