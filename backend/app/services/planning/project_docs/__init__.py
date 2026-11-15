"""Project management documentation compiled from the factory's records (issue #530).

A documentation pack is a set of Markdown documents and C4 diagrams about one
company's project, built from what La Fabrique already records: the approved
specification and architecture, the sprints and their tasks, the task events
and their hash chain, the Verifier's risk tiers, the retrospectives and the
integrations. ``docs/design/PROJECT_DOCUMENTATION.md`` is the design.

- ``refs``, ``sanitize``, ``c4``, ``scope_docs`` and ``control_docs`` are pure:
  plain data in, Markdown and diagram source out, no database, no network.
  Every statement they write cites the record it came from; a section with no
  record says so.
- ``collect`` reads the database into that plain data (the snapshot).
- ``pack`` compiles a pack, checks it for drift against the current records,
  asks the TechWriter for the few prose sections that have no record behind
  them (marked as claims), and writes an approved pack into the project's
  repository through the workspace daemon.

The document structure follows PMBOK (PMI) and the C4 model (Simon Brown,
c4model.com, CC BY 4.0); the wording is La Fabrique's own.
"""
