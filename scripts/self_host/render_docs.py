#!/usr/bin/env python3
"""La Fabrique's own design documents, rendered from its architecture record (issue #329).

``docs/architecture/specification.json`` and ``architecture.json`` are the
records the factory keeps for itself: the same shapes the PO and the
Architect write for any company, checked by the same ``spec_check`` and
``arch_check``. This script renders them the way the product does for a
company's repository:

- ``docs/ARCHITECTURE.md`` by ``arch_check.as_markdown``;
- ``docs/adr/NNNN-<slug>.md``, one file per decision, by
  ``adr_docs.adr_documents``, numbered from the record's
  ``adr_first_number`` (La Fabrique's ADRs 0010 and 0011 predate the record
  and stay as written; they are superseded).

``python3 scripts/self_host/render_docs.py`` reports what differs and exits 1;
``--write`` writes the files. ``backend/tests/test_self_hosting.py`` keeps the
files and the record in step, the way ``test_standards.py`` keeps
``docs/STANDARDS.md`` in step with the catalogue. The e2e self-hosting step
(``scripts/self_host/self_host.py``) checks that the record La Fabrique stores
for itself, once posted and approved, renders the same files through the API.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLANNING = REPO / "backend" / "app" / "services" / "planning"


def _pure(name: str):
    """A pure module of the planning context, loaded by its file: the
    ``services`` package itself would pull the database in, and these three
    import only the standard library."""
    spec = importlib.util.spec_from_file_location(
        f"_self_host_{name}", PLANNING / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adr_docs = _pure("adr_docs")
arch_check = _pure("arch_check")
spec_check = _pure("spec_check")

SPECIFICATION = REPO / "docs" / "architecture" / "specification.json"
ARCHITECTURE = REPO / "docs" / "architecture" / "architecture.json"
# The ADRs written before the record existed; kept as they are (superseded).
LEGACY_ADRS = (
    "0010-autonomous-dev-self-correction-loop.md",
    "0011-llm-fallback-routing.md",
)


def records() -> tuple[dict, dict, int]:
    """The normalized specification and architecture, and the first ADR number."""
    raw_arch = json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    spec = spec_check.normalize(json.loads(SPECIFICATION.read_text(encoding="utf-8")))
    return spec, arch_check.normalize(raw_arch), int(raw_arch.get("adr_first_number") or 1)


def rendered() -> dict[str, str]:
    """Every rendered file, by its path relative to the repository."""
    spec, arch, first = records()
    files = {"docs/ARCHITECTURE.md": arch_check.as_markdown(arch, spec["title"], 1)}
    for adr in adr_docs.adr_documents(arch, spec["title"], 1, first_number=first):
        files[adr["path"]] = adr["markdown"]
    return files


def differences(files: dict[str, str]) -> list[str]:
    """What differs between *files* and the repository, including stray ADR files."""
    out = []
    for path, text in files.items():
        target = REPO / path
        if not target.exists():
            out.append(f"{path}: missing")
        elif target.read_text(encoding="utf-8") != text:
            out.append(f"{path}: differs from the record's rendering")
    expected = {Path(p).name for p in files if p.startswith(adr_docs.ADR_DIRECTORY)}
    for existing in sorted((REPO / adr_docs.ADR_DIRECTORY).glob("*.md")):
        if existing.name not in expected and existing.name not in LEGACY_ADRS:
            out.append(
                f"{adr_docs.ADR_DIRECTORY}/{existing.name}: not a decision of the record"
            )
    return out


def main(argv: list[str]) -> int:
    files = rendered()
    if "--write" in argv:
        for path, text in files.items():
            (REPO / path).parent.mkdir(parents=True, exist_ok=True)
            (REPO / path).write_text(text, encoding="utf-8")
        print(f"wrote {len(files)} file(s) from the architecture record")
    problems = differences(files)
    for problem in problems:
        print(problem)
    if not problems:
        print(f"{len(files)} file(s) in step with the architecture record")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
