Fixed

* **A task that delivers the non-document file it names is not held to the documentation rules.** A word such as "architecture" in a task's title made the quality gate ask for two Markdown headings, so a one-line change to `docs/architecture/contexts.json` was sent back (sprint 6, 2026-11-23). When every file a delivery writes is one the task names, the wording no longer decides; a document task that delivers other files is still sent back.
