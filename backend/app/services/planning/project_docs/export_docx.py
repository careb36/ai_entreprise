"""A documentation pack as a DOCX file, with python-docx (issue #694, phase 4).

``export.source_of`` gives the parsed pack; this module draws it with Word's
built-in styles (Title, Heading 1 to 3, List Bullet and List Number with
their levels, Quote, Table Grid) so a reader's own Word styles and its
navigation pane apply. Each document starts on a new page with a Heading 1;
the header row of every table repeats across pages; the document language is
set on the defaults so a screen reader and the spelling checker read Spanish
as Spanish.

The bytes are reproducible: the core properties carry the pack's compile time,
and the zip is rewritten with one fixed timestamp per entry (python-docx
stamps each entry with the time of writing).

python-docx (MIT) and lxml (BSD) are imported only when a DOCX is rendered.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

from services.planning.project_docs import export_markdown as md

RENDERER = "python-docx"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
LIST_STYLES = {False: "List Bullet", True: "List Number"}
WORD_LANGUAGES = {"en": "en-GB", "es": "es-ES"}
# Characters XML 1.0 cannot carry; the sanitizer drops them already, this is
# the renderer's own guard.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def unavailable_reason() -> str | None:
    try:
        import docx  # noqa: F401
    except ImportError as exc:  # pragma: no cover - the lock installs it
        return f"python-docx is not installed ({exc})"
    return None


def renderer_name() -> str:
    from importlib.metadata import version

    return f"{RENDERER} {version('python-docx')}"


def _text(value: str) -> str:
    return _NOT_XML.sub("", value)


def _runs(paragraph: Any, inlines: list[md.Inline], bold: bool = False) -> None:
    for kind, text in inlines:
        run = paragraph.add_run(_text(text))
        if kind in ("strong", "strong_em") or bold:
            run.bold = True
        if kind in ("em", "strong_em"):
            run.italic = True
        if kind == "code":
            run.style = "Code Char"


def _styles(document: Any, lang: str) -> None:
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    styles = document.styles
    code = styles.add_style("Code Block", WD_STYLE_TYPE.PARAGRAPH)
    code.base_style = styles["Normal"]
    code.font.name = "Courier New"
    code.font.size = Pt(8)
    code.paragraph_format.space_after = Pt(0)
    char = styles.add_style("Code Char", WD_STYLE_TYPE.CHARACTER)
    char.font.name = "Courier New"
    styles["Normal"].font.size = Pt(10)
    # The language on the document's run defaults.
    defaults = styles.element.find(qn("w:docDefaults"))
    rpr = defaults.find(qn("w:rPrDefault")).find(qn("w:rPr"))
    for old in rpr.findall(qn("w:lang")):
        rpr.remove(old)
    language = OxmlElement("w:lang")
    language.set(qn("w:val"), WORD_LANGUAGES.get(lang, lang))
    rpr.append(language)


def _repeat_header(row: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(header)


def _table(document: Any, table: md.Table) -> None:
    grid = document.add_table(rows=1 + len(table.rows), cols=len(table.header))
    grid.style = "Table Grid"
    for row_index, cells in enumerate([table.header, *table.rows]):
        row = grid.rows[row_index]
        for cell, inlines in zip(row.cells, cells, strict=True):
            _runs(cell.paragraphs[0], inlines, bold=row_index == 0)
    _repeat_header(grid.rows[0])


def _list(document: Any, block: md.ListBlock, level: int = 1) -> None:
    style = LIST_STYLES[block.ordered] + (f" {level}" if level > 1 else "")
    for item in block.items:
        _runs(document.add_paragraph(style=style), item.inlines)
        for child in item.children:
            _list(document, child, min(level + 1, 3))


def _code(document: Any, block: md.Code, mermaid_label: str) -> None:
    if block.language == "mermaid":
        label = document.add_paragraph()
        label.add_run(_text(mermaid_label)).italic = True
    for line in block.text.split("\n"):
        document.add_paragraph(_text(line), style="Code Block")


def _blocks(
    document: Any,
    blocks: list[md.Block],
    mermaid_label: str,
    first_heading_breaks: bool,
    quoted: bool = False,
) -> None:
    for index, block in enumerate(blocks):
        if isinstance(block, md.Heading):
            heading = document.add_heading(level=min(block.level, 9))
            _runs(heading, block.inlines)
            if index == 0 and first_heading_breaks and block.level == 1:
                heading.paragraph_format.page_break_before = True
        elif isinstance(block, md.Paragraph):
            style = "Quote" if quoted else None
            _runs(document.add_paragraph(style=style), block.inlines)
        elif isinstance(block, md.ListBlock):
            _list(document, block)
        elif isinstance(block, md.Table):
            _table(document, block)
        elif isinstance(block, md.Quote):
            _blocks(document, block.blocks, mermaid_label, False, quoted=True)
        elif isinstance(block, md.Code):
            _code(document, block, mermaid_label)


def _core(document: Any, source: Any) -> None:
    core = document.core_properties
    moment = _moment(source.compiled_at)
    core.title = source.title
    core.language = source.language
    core.author = "La Fabrique"
    core.last_modified_by = "La Fabrique"
    core.created = moment
    core.modified = moment
    core.last_printed = moment
    core.revision = 1
    core.identifier = source.identifier
    core.subject = ""
    core.keywords = ""
    core.comments = ""
    core.category = ""


def _moment(compiled_at: str) -> datetime:
    try:
        return datetime.strptime(compiled_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return datetime(1980, 1, 1, tzinfo=UTC)


def _fixed_zip(data: bytes) -> bytes:
    """The same entries in the same order, each with one fixed timestamp."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=ZIP_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            dst.writestr(entry, src.read(info.filename))
    return out.getvalue()


def render(source: Any) -> bytes:
    """The pack (``export.Source``) as DOCX bytes."""
    from docx import Document

    document = Document()
    _styles(document, source.language)
    _core(document, source)
    document.add_paragraph(_text(source.title), style="Title")
    for label, value in source.cover:
        paragraph = document.add_paragraph()
        paragraph.add_run(_text(f"{label}: ")).bold = True
        paragraph.add_run(_text(value))
    document.add_paragraph(_text(source.note))
    document.add_heading(_text(source.contents_heading), level=1)
    for title in source.contents:
        document.add_paragraph(_text(title), style="List Bullet")
    for doc in source.documents:
        _blocks(document, doc.blocks, source.mermaid_label, first_heading_breaks=True)
    buffer = io.BytesIO()
    document.save(buffer)
    return _fixed_zip(buffer.getvalue())
