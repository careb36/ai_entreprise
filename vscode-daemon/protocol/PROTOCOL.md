# The workspace daemon protocol, version 1

The wire contract between La Fabrique's backend and the workspace daemon
(`vscode-daemon/`), the only component that touches the user's files. This
document describes protocol version 1 as the daemon serves it today. Later
changes add their section here, with their schemas and fixtures, in the pull
request that makes them (§7).

- **This document, the schemas and the fixtures are the contract.** The JSON
  Schemas (draft 2020-12) in [`schemas/v1/`](schemas/v1/) give each subject's
  request and reply; the conformance fixtures in [`fixtures/v1/`](fixtures/v1/)
  are recorded request/reply pairs.
- **Kept honest by tests.** `backend/tests/test_daemon_protocol.py` fails when
  a subject the daemon serves, consumes or publishes has no schema, when a
  schema names a subject the daemon does not use, when a subject is missing
  from this document, and when a handler's reply to a fixture changes.
  `backend/tests/test_daemon_protocol_client.py` validates every fixture
  against its schema and feeds it to the backend code that reads it, and
  covers the version negotiation and the heartbeat.
- **Design credit.** The layout (a versioned spec with a refusal table, a
  schema per message, conformance fixtures both sides replay, a heartbeat the
  platform times on its own clock) is taken from AixleHQ/flow's
  `runner/protocol/` (`PROTOCOL.md`, `schemas/v1/`, `fixtures/v1/`) and its
  runner health check (`app/services/runners/health_check.rb`), Apache-2.0,
  as design only: no code was copied.

## 1. Transport

- **NATS core request-reply.** The backend sends a request on a subject and
  waits for one reply; every daemon subscribes in the queue group
  `workspace-daemon`, so one daemon answers each request.
- **JSON bodies** in UTF-8, keys in `snake_case`. A request body that is not a
  JSON object is read as `{}`.
- **Replies arrive under `_INBOX.backend`.** With the broker configuration
  `scripts/nats_config.py` writes, the daemon may publish only its replies
  there, `workspace.delivered`, `workspace.heartbeat`, `glassroom.*` and the
  JetStream calls of its own consumer; it may never send a request.
- **Subjects** are not namespaced, except the JetStream ones
  (`workspace.deliver`, `workspace.delivered`), which carry `NATS_NAMESPACE`
  (`vscode-daemon/nats_scope.py`).
- **Paths** are the daemon machine's own absolute paths, passed as opaque
  strings; the backend never resolves them.

## 2. The protocol version and its negotiation

The current protocol version is `1`.

The daemon reports it in `workspace.status` and in every heartbeat:

| Field | Meaning |
| --- | --- |
| `protocol_version` | The protocol this daemon speaks (`daemon_status.PROTOCOL_VERSION`). |
| `protocol_min` | The oldest protocol it still serves (`daemon_status.PROTOCOL_MIN`). |

The backend supports a range, `SUPPORTED_PROTOCOL` in
`backend/app/services/workspace/daemon_liveness.py` (`1` to `1` today), and
decides:

| The daemon reports | The backend's verdict | What happens |
| --- | --- | --- |
| A range that overlaps the backend's | `compatible`, with `negotiated`: the highest version both speak | Served. |
| A range entirely above or below the backend's, or a version that is not a positive integer | `incompatible`, with a reason that names the side to update | `GET /v1/workspace/status` says `state: incompatible` and the reason; `daemon_request` raises `DaemonIncompatible` (a `DaemonUnavailable` whose message is the reason) without sending; the task worker does not send or queue a delivery and records `the workspace daemon is incompatible: <reason>` as the evidence's `reason`. Never a generic timeout. |
| No version (a daemon from before protocol 1) | `unknown`: `unknown protocol version (older daemon)` | Served as before: no regression for existing installs. |

Requests carry no version in protocol 1: the daemon reports, the backend
decides. The backend refuses only when every daemon it heard from within the
offline window (§3) is incompatible; one compatible or older daemon among them
keeps the work flowing.

## 3. Liveness: the heartbeat

The daemon publishes `workspace.heartbeat` every `FABRIQUE_HEARTBEAT_SECONDS`
(default 20, bounded to 1..25) from the moment it is ready. Nobody answers it.

| Field | Meaning |
| --- | --- |
| `daemon_id` | Random per process: a restart shows as a new id. |
| `sequence` | 1 for the first beat of the process, then one more per beat, also for a beat that could not be sent, so the backend sees the gap. |
| `protocol_version`, `protocol_min`, `version`, `host` | As in `workspace.status`. |
| `interval_seconds` | The interval the daemon beats at. |
| `sent_at` | The daemon's clock. Never used to judge liveness. |

The backend subscribes (a plain subscription, so every replica observes every
beat) and records when it *observed* each one, on its own clock
(`daemon_liveness.observe_heartbeat`). A daemon is `online` up to
`DAEMON_HEARTBEAT_STALE_SECONDS` (60) after its last observed heartbeat,
`stale` up to `DAEMON_HEARTBEAT_OFFLINE_SECONDS` (90), then `offline`.
Without any heartbeat observed (an older daemon, or none yet since the backend
started), the request-based check (`workspace.status` with a 3 s timeout) is
the fallback; `GET /v1/workspace/status` names the source.

**Claims and observations stay apart.** Everything the daemon says about
itself (`version`, `protocol_version`, `protocol_min`, `host`, `daemon_id`,
`sequence`, `sent_at`) is reported under `reported_by_daemon`. What the
backend observed (`last_observed_at`, `seconds_since_last`, the heartbeat
state, `missed` beats counted from sequence gaps) is under `heartbeat`, with
`observed_by: backend`.

## 4. Replies, refusals and observed failures

A request that cannot be served is answered with a **refusal**:

```json
{ "error": { "status": 404, "message": "No such file: src/missing.py" } }
```

`status` follows HTTP and is the contract; `message` is for people and may
change. The backend's `daemon_request` raises `DaemonError(status, message)`
for it; the routes relay the status and message (`HTTPException`), and the
Developer's tools hand the message to the model as the tool's error.

| `status` | Meaning | Raised by |
| --- | --- | --- |
| 400 | The request is malformed: a required field is missing or of the wrong type, a path is not inside the project or not absolute, a pattern does not compile, an edit changes nothing, too many seeds, paths or names. | Every subject that refuses (§5). |
| 403 | A file that may hold secrets is never shown or edited; a folder the daemon may not read. | `workspace.read`, `workspace.edit`, `workspace.browse` |
| 404 | The project folder is not on this machine, or no such file or folder. | The read tools, `workspace.edit`, `workspace.browse` |
| 408 | A search ran past its time limit and was stopped. | `workspace.search` |
| 409 | A conflict with the repository's state: a git operation refused (publish, merge, base status, integration), a snapshot already undone or files changed since the delivery, an edit session of another project, `gh` / `glab` missing or failing. | `workspace.git.*`, `workspace.undo`, `workspace.edit` |
| 413 | A file too large for the edit tool, or an edit that would make it so. | `workspace.edit` |
| 415 | Not a UTF-8 text file. | `workspace.edit` |
| 422 | The edit was not applied; not a pull or merge request URL. | `workspace.edit`, `workspace.git.reviews` |
| 500 | The project or the snapshot store could not be read or written; an undo that failed. | The read tools, `workspace.undo` |
| 502 | `gh` / `glab` answered something that is not JSON. | `workspace.git.reviews` |
| 504 | `gh` / `glab` did not answer in time. | `workspace.git.reviews` |

On the backend's side, three more outcomes never come from the daemon:
`DaemonUnavailable` with `absent` (NATS says nobody listens: no daemon
runs), `DaemonUnavailable` without it (no reply in time, or NATS unreachable:
whether a daemon runs is unknown), and `DaemonIncompatible` (§2). A reply that
is not a JSON object is `DaemonError(502)`.

**An observed failure is not a refusal.** Where the daemon tried and observed
the failure, the reply is the normal one and says so: `workspace.execute`
(`success: false` with `exit_code`, `timed_out`, `refused` by the command
policy or `error`), `workspace.app.check` (`error`, `refused`),
`workspace.console.prepare` / `collect` (`ok: false` with `error`) and
`workspace.generate` (a `status` per file: `written`, `skipped_protected`,
`rejected`, `rejected_edit`, `rejected_syntax`, `workspace_missing`; or
`ok: false` with `error`). The Verifier judges these; they are evidence.

A `workspace.generate` request without `files` gets **no reply**: sprint
kickoff stories travel on that subject for the backend's agent graph.

## 5. The subjects

Every subject the daemon serves (`daemon.subscriptions`), consumes or
publishes, with its schema in [`schemas/v1/<subject>.json`](schemas/v1/)
(`$defs/request` and `$defs/reply`, or `$defs/message` for what is not a
request; a subject that refuses has `$defs/ok` and `$defs/refusal`).

| Subject | Kind | Module | Request | Reply |
| --- | --- | --- | --- | --- |
| `workspace.status` | request | `daemon_status.py` | `{}` | `host`, `version`, `protocol_version`, `protocol_min`, `daemon_id`, `heartbeat_seconds`, `jetstream`, `sandbox_cache` |
| `workspace.heartbeat` | published | `daemon_status.py` | — | §3 |
| `workspace.capabilities` | request | `daemon.py` | `commands`? | `assistants` |
| `workspace.generate` | request | `daemon.py`, `delivery.py` | `workspace_root`, `files` (`path` with `content` or `edits`), `git`?, `sprint_id`? | `ok`, `workspace_root`, `files` (per file `status`, `sha256` read back, the diff), `snapshot` or `git`, `project_files` |
| `workspace.execute` | request | `daemon.py`, `shell_exec.py`, `sandbox.py` | `command`, `workspace_root`, `sandbox`?, `command_policy`?, `timeout_seconds`? | `success`, `stdout`, `stderr`, `exit_code`, `timed_out`?, `sandbox`? |
| `workspace.browse` | request | `host_folders.py` | `path`? | `host`, `path`, `parent`, `entries`, `shortcuts`, `roots`, `truncated` |
| `workspace.inspect` | request | `host_folders.py` | `path`, `snapshot`? | `host`, `path`, `name`, `exists`, `is_dir`, `git`, `snapshot`? |
| `workspace.hash` | request | `file_hash.py` | `workspace_root`, `paths` | `files` (`present` with `sha256`, `missing`, `rejected`, `workspace_missing`) |
| `workspace.undo` | request | `delivery_snapshot.py` | `workspace_root`, `snapshot_id` | `ok`, `snapshot`, `restored`, `removed` |
| `workspace.list` | request | `workspace_read.py` | `workspace_root`, `path`?, `depth`? | `path`, `entries`, `truncated` |
| `workspace.read` | request | `workspace_read.py` | `workspace_root`, `path`, `start_line`? | `path`, `sha256`, `bytes`, `binary`, `text`, `lines`, `first_line`, `last_line`, `truncated` |
| `workspace.search` | request | `workspace_read.py` | `workspace_root`, `pattern`, `path`? | `pattern`, `matches`, `files_scanned`, `match_count`, `truncated`, … |
| `workspace.find` | request | `workspace_read.py` | `workspace_root`, `pattern`, `path`? | `pattern`, `path`, `files`, `match_count`, `truncated` |
| `workspace.map` | request | `repo_map.py` | `workspace_root`, `max_chars`? | `map`, `fingerprint`, `definitions`, `shown`, `partial`, `cached`, … |
| `workspace.edit` | request | `workspace_edit.py` | `workspace_root`, `session`, `path`, lines or `search` / `replace` | `path`, `sha256_before`, `sha256_after`, `bytes`, `lines`, `checked`, the window |
| `workspace.edit.finish` | request | `workspace_edit.py` | `workspace_root`, `session` | `session`, `files` |
| `workspace.signatures` | request | `signatures.py` | `workspace_root`, `wanted` (`path`, `names`) | `files` (per file `status`, `sha256`, per name `found` / `unknown` / `missing`) |
| `workspace.related` | request | `code_graph.py` | `workspace_root`, `seeds`, `hops`?, `max_files`? | `files` (`path`, `sha256`, `hop`, `seed`, `uses`, `used_by`), `partial`, `seeds_matched`, `seeds_unmatched` |
| `workspace.console.prepare` | request | `console_run.py` | `workspace_root`, `git` | `ok`, `worktree`, `branch`, `head`, `guard`, … |
| `workspace.console.collect` | request | `console_run.py` | `workspace_root`, `head`, `guard` | `ok`, `files`, `refused`, `restored`, … |
| `workspace.sandbox.reset` | request | `sandbox_cache.py` | `workspace_root` | `volume`, `volume_reset`, `quarantine_cleared`, … |
| `workspace.git.publish` | request | `git_workspace.py` | `workspace_root`, `branch`, `base`?, `title`?, `body`? | `ok`, the branch, commit and request URL |
| `workspace.git.merge` | request | `local_merge.py` | `workspace_root`, `branch`, `base`, `commit`? | `ok`, `base`, `before`, `after`, `fast_forward`, `already` |
| `workspace.git.base_status` | request | `git_base.py` | `workspace_root`, `base`? | `is_repo`, and how the base stands against its remote |
| `workspace.git.reviews` | request | `platform_reviews.py` | `workspace_root`, `url`, `provider` | `ok`, `url`, `comments`, `truncated` |
| `workspace.app.check` | request | `app_run.py` | `workspace_root`, `start_command`, `port`, `probe_path`?, `e2e_command`?, `sandbox`? | `listening`, probes, the e2e run, or `error` |
| `workspace.git.integrate` | request | `sprint_integration.py` | `workspace_root`, `branch`, `merge` | `ok`, the integration branch and its merges, or the conflict |
| `workspace.deliver` | JetStream, consumed | `daemon.py` | — | A delivery queued while the daemon was offline: `job_id`, `workspace_root`, `files`, `git`? |
| `workspace.delivered` | JetStream, published | `daemon.py` | — | The `workspace.generate` reply with `job_id`, `company_id`, `sprint_id`, `item_id`, `host` |
| `glassroom.*` | published | `daemon.py` | — | A status line on `glassroom.<sprint_id>` |

The schemas allow fields they do not name: a reader ignores what it does not
know, which is what lets a field be added without a new version (§7).

**`workspace.execute` and the daemon's sandbox floor** (issue #525). Every
request goes through the command policy and then the sandbox's rules
(`shell_exec.execute_shell_command`, `sandbox.normalize`). A request without
`sandbox` is read as `{"mode": "host"}`, so on a daemon with
`FABRIQUE_SANDBOX_REQUIRED=container` (or `bwrap`) it runs in that mode and
the reply carries `sandbox` with `forced_by_daemon: true`. When that mode
cannot run (no Docker or Podman, no bubblewrap), the reply is
`{"success": false, "error": "sandbox unavailable: <reason>"}` and nothing
ran: never the host. Without a floor nothing changes: the host, and **no
`sandbox` in the reply of a request that did not ask**, so a backend from
before this change reads the same replies. The report appears whenever the
command left the host, because then the evidence must say where it ran.
`sandbox.purpose` takes `test`, `install`, `check` or `assistant` (a console
assistant's consultation, sent by `services/producers/console_assistants.py`
with the company's sandbox, its network setting and the names in
`sandbox_env` and `console_env`); anything else reads as `test`, so an older
daemon treats `assistant` as `test`. Both are additive: protocol version 1.
The application check (`workspace.app.check`) already normalized a missing
`sandbox` the same way.

## 6. Conformance fixtures

`fixtures/v1/<subject>/<scenario>.json`, one recorded exchange per file, in
plain JSON:

```json
{
  "description": "What the exchange shows.",
  "subject": "workspace.read",
  "workspace": { "files": { "src/greeting.py": "def greet(name): ..." } },
  "request": { "workspace_root": "$WORKSPACE", "path": "src/greeting.py" },
  "reply": { "path": "src/greeting.py", "sha256": "…", "...": "…" },
  "volatile": ["/host"]
}
```

- `workspace.files` is written into a fresh temporary folder before the
  request; `$WORKSPACE` stands for that folder in the request and the reply.
- `daemon_env` (optional) sets the daemon machine's own settings for the
  replay, today `FABRIQUE_SANDBOX_REQUIRED` and `FABRIQUE_SANDBOX_RUNTIME`
  (`DAEMON_ENV` in the test); without it both are unset.
- `reply` is what the daemon answered (for a published subject, what it
  published). The daemon's side replays the request against the real handler
  and requires the same reply, except at the JSON pointers in `volatile`
  (values that depend on the machine or the run, recorded with neutral
  examples), which must keep their JSON type.
- The backend's side validates every `reply` against the subject's schema and
  hands it to the backend code that reads it (the presence and protocol check,
  the delivery's evidence, the test run, the Developer's tools, the repository
  map, the related code, the signatures).

Fixtures cover `workspace.status`, `workspace.heartbeat`,
`workspace.capabilities`, `workspace.generate`, `workspace.execute`,
`workspace.list`, `workspace.read`, `workspace.search`, `workspace.find`,
`workspace.map`, `workspace.related`, `workspace.signatures`,
`workspace.hash`, `workspace.sandbox.reset`, `workspace.inspect` and
`workspace.browse` (for `workspace.execute`, also a console assistant's
consultation on the host and a request without `sandbox` refused by a floor
whose runtime is missing); the other subjects are covered by their schema, and their
handlers by their own tests. To add one, write the file with the request and
an empty `reply`, run the daemon-side test, and copy the actual reply it
prints after checking it.

## 7. Changing the protocol

- **Adding** an optional request field, a reply field or a new subject keeps
  the version: readers ignore what they do not know. The new subject gets its
  schema and a row in §5 in the same pull request (the tests require both).
- **Removing, renaming or changing the meaning** of a field or a subject, or
  making a request field required, is a new version: raise
  `PROTOCOL_VERSION` in `vscode-daemon/daemon_status.py`, add `schemas/v2/`
  and `fixtures/v2/`, and widen the backend's `SUPPORTED_PROTOCOL` so it
  speaks both the current and the previous version for one release. A daemon
  that still serves the old version keeps it in `protocol_min`.
