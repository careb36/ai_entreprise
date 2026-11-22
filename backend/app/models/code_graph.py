"""
The stored code knowledge graph (issue #438, second slice;
``docs/archive/design/CODE_GRAPH.md``).

What the workspace daemon observed of a project's code, per company and
project folder: each file with the sha256 the daemon read from disk, the
definitions it parsed in it and the names it refers to. A file's
definitions and references belong to it and are replaced with it when its
sha256 changes, so a changed file rewrites only its own rows and an
unchanged one is never parsed or written again. The ``references`` edge
from a file to a definition is resolved when it is read (a name a file uses
and another file defines), which is why replacing one file never leaves an
edge pointing at another file's old definitions.

``code_graph_edges`` holds the edges that come from records rather than from
the parse: ``implements`` and ``depends_on`` from the approved architecture
(a claim a person approved) and ``verified`` from the Verifier's records (an
observation). Each row names its ``source`` and the record it came from, so
an edge the architecture claims and one a verification observed are never
the same row or the same field.

Everything here is an observation of the code or a copy of a record, never
what a producer claimed about its own work: the sha256 is the daemon's,
computed on the machine that holds the files (rule 6 of
``docs/POSITIONING.md``). It feeds a prompt as context, never a verdict.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Index, UniqueConstraint
from sqlmodel import Column, Field, SQLModel

# Where an edge in ``code_graph_edges`` comes from, and what it is.
EDGE_SOURCES = {
    "architecture": "claim",  # the approved architecture: a person approved it
    "verification": "observation",  # the Verifier's records of the daemon's report
}
EDGE_KINDS = ("implements", "depends_on", "verified")
FILE_STATUSES = ("parsed", "unparsed", "refs_skipped")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CodeGraphFile(SQLModel, table=True):
    __tablename__ = "code_graph_files"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "workspace_root", "path", name="uq_code_graph_files_path"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    # The project folder on the daemon's machine (or a task's worktree).
    workspace_root: str
    # Project-relative, POSIX.
    path: str
    # The sha256 the daemon computed from the bytes it read: the key that
    # decides whether the file is parsed and written again.
    sha256: str = Field(index=True)
    # "parsed", "unparsed" (it does not parse) or "refs_skipped" (JavaScript
    # whose literals could not be stripped safely: definitions only).
    status: str = "parsed"
    # The daemon kept at most its caps of definitions and references.
    truncated: bool = False
    observed_at: datetime = Field(default_factory=_now)


class CodeGraphDefinition(SQLModel, table=True):
    __tablename__ = "code_graph_definitions"
    __table_args__ = (
        Index("ix_code_graph_definitions_scope_name", "company_id", "name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="code_graph_files.id", index=True)
    company_id: int = Field(foreign_key="companies.id")
    name: str
    line: int
    # 0 at the top level, 1 for a method of a top-level class.
    depth: int = 0


class CodeGraphReference(SQLModel, table=True):
    __tablename__ = "code_graph_references"
    __table_args__ = (
        Index("ix_code_graph_references_scope_name", "company_id", "name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="code_graph_files.id", index=True)
    company_id: int = Field(foreign_key="companies.id")
    name: str
    # How many times the file uses the name.
    count: int = 1


class CodeGraphEdge(SQLModel, table=True):
    __tablename__ = "code_graph_edges"
    __table_args__ = (
        Index(
            "ix_code_graph_edges_scope_source",
            "company_id",
            "workspace_root",
            "source",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="companies.id", index=True)
    workspace_root: str
    kind: str  # EDGE_KINDS
    # "module:MOD-1", "requirement:REQ-1", "file:src/x.py", "run:12".
    from_ref: str
    to_ref: str
    source: str  # EDGE_SOURCES
    # The record the edge was read from: "architecture:3", "verification:41".
    source_ref: str
    # What the record said beside the edge (a verification's sha256 of the
    # file it observed, its task), copied, never computed here.
    detail: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True), nullable=True)
    )
    recorded_at: datetime = Field(default_factory=_now)
