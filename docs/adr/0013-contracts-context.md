# ADR 0013: A contracts context holds the shapes the cycle is made of

## Status

Accepted (2026-11-09). ADR-2 of architecture v1, *La Fabrique backend: contexts and their dependency rule*, for the specification *Backend contexts and their dependency rule*. Written by La Fabrique from the architecture record; edit it there, not here.

## Context

Most of the 20 edges exist to reach a shape, not behaviour: standards, verification, acceptance and workspace_edits import task_worker lazily for parse_file_manifest and ALLOWED_ARTIFACT_EXTENSIONS; evidence imports the verdict names from orchestration_metrics; workspace_daemon imports criteria_tests and app_check to shape its reply; verification imports mcp_tools, research_tools and console_clis for the shape of their observations.

## Decision

Create backend/app/services/contracts/ with verdicts.py, manifest.py (parse_file_manifest, expected_hashes, ALLOWED_ARTIFACT_EXTENSIONS, moved out of task_worker.py), delivery_reply.py (the daemon reply and the tool-call observation as TypedDicts with their readers), task_record.py (the event shape and the TaskRecordView protocol), and move roles, edit_blocks, standards_catalogue and sandbox_schemas into it. contracts imports nothing else in services, performs no I/O and holds no decision.

## Consequences

The contracts step alone removes the 4 edges to task_worker from evidence and tools and lets the observation readers move to evidence. Old names stay importable through re-exports (task_worker.parse_file_manifest, orchestration_metrics.VERDICT_*) until their last importer is changed. contracts must stay small: a function that decides belongs to its context, not here.
