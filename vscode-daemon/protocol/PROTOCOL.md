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
  there, `workspace.delivered`, `workspace.heartbeat`, `glassroom.*`, the
  JetStream calls of its own consumer and a run's full log into the object
  store `FABRIQUE_RUN_LOGS` (§5.1); it may never send a request.
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

### 2.1 The abilities the daemon announces (additive, issue #315)

The backend used to guess what a daemon can do from its replies: a subject
nobody answered ("no responders") read as "an older daemon", a reply without
`full_log` as "a daemon from before full logs". The daemon now announces it,
in `workspace.status` and in every heartbeat (so from its first beat after it
connects), under `abilities` (`vscode-daemon/abilities.py`):

```json
{ "abilities_version": 1,
  "subjects": ["workspace.app.check", "workspace.execute", "..."],
  "features": { "workspace.execute": ["full_log", "purpose_assistant", "sandbox_floor"],
                "workspace.app.check": ["browser_probe", "cache", "full_log", "security_probe"] } }
```

| Field | Meaning |
| --- | --- |
| `abilities_version` | The announcement's own shape (`abilities.ABILITIES_VERSION`, 1). Adding a subject or a feature keeps it; the backend reads versions up to `daemon_abilities.SUPPORTED_ABILITIES_VERSION` and treats a higher one as no announcement. |
| `subjects` | Every request subject the daemon serves (kept equal to `daemon.subscriptions` by `test_daemon_protocol.py`). |
| `features` | Per subject, the optional request fields added within version 1 that it honours (§5.1, §5.1.1, §5.1.4, §5.1.5, §5.2, §5.6, §5.7, §5.8, §5.10). |

**A claim, never an observation.** The backend (`daemon_liveness`, with
`daemon_abilities`) keeps the announcement per daemon under
`reported_by_daemon.abilities`, bounded, and asks `announces(subject,
feature)`, which considers every daemon heard within the offline window (§3)
and answers `true` (all announce it), `false` (none does) or `null` (none
heard, one of them announced nothing, or they disagree). It is used only to
choose whether to send a request and how to word a reply that lacks a field:

| Where | `false` | `null` (no announcement: today's fallback) |
| --- | --- | --- |
| `workspace.signatures` (`services/tools/signature_lookup.py`) | Not asked; `signatures` recorded `observed: false`, "does not announce workspace.signatures". | Asked; nobody answering is "an older daemon". With `true`, nobody answering says the daemons announce it. |
| `workspace.hash` (`services/loop/delivery_checks.guard_test_files`) | Not asked, logged, as a daemon without the subject was before. | Asked; a failure is logged as before. |
| `full_log` (`services/workspace/run_logs.observe`) | A reply without it is "does not announce full_log on <subject>". | "a daemon from before full logs". With `true`, it says the daemon announced it but sent none. |

What a run did is still read from that run's reply: an announced feature
missing from a reply is never filled in from the announcement.

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
| `abilities` | What the daemon announces it can do (§2.1). |

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
`sequence`, `sent_at`, `abilities`) is reported under `reported_by_daemon`. What the
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
| `workspace.status` | request | `daemon_status.py`, `abilities.py` | `{}` | `host`, `version`, `protocol_version`, `protocol_min`, `daemon_id`, `heartbeat_seconds`, `jetstream`, `sandbox_cache`, `sandbox_environment` (§5.1.2), `abilities` (§2.1) |
| `workspace.heartbeat` | published | `daemon_status.py` | — | §3 |
| `workspace.capabilities` | request | `daemon.py` | `commands`? | `assistants` |
| `workspace.generate` | request | `daemon.py`, `delivery.py` | `workspace_root`, `files` (`path` with `content` or `edits`), `git`?, `sprint_id`? | `ok`, `workspace_root`, `files` (per file `status`, `sha256` read back, the diff), `snapshot` or `git`, `project_files` |
| `workspace.execute` | request | `daemon.py`, `shell_exec.py`, `sandbox.py`, `program_run.py`, `run_logs.py` | `command`, `workspace_root`, `sandbox`? (with `purpose: program`, `workdir` and `program`, §5.12), `command_policy`?, `timeout_seconds`?, `full_log`? | `success`, `stdout`, `stderr`, `exit_code`, `timed_out`?, `sandbox`?, `program`? (§5.12), `full_log`?, `output_truncated`? (the streams cut so the reply fits one NATS message: start and end kept) |
| `workspace.browse` | request | `host_folders.py` | `path`? | `host`, `path`, `parent`, `entries`, `shortcuts`, `roots`, `truncated` |
| `workspace.inspect` | request | `host_folders.py` | `path`, `snapshot`? | `host`, `path`, `name`, `exists`, `is_dir`, `git`, `snapshot`? (with `playwright`: the version the project pins, `playwright_pin.py`, #315) |
| `workspace.hash` | request | `file_hash.py` | `workspace_root`, `paths` | `files` (`present` with `sha256`, `missing`, `rejected`, `workspace_missing`) |
| `workspace.undo` | request | `delivery_snapshot.py` | `workspace_root`, `snapshot_id` | `ok`, `snapshot`, `restored`, `removed` |
| `workspace.list` | request | `workspace_read.py` | `workspace_root`, `path`?, `depth`? | `path`, `entries`, `truncated` |
| `workspace.read` | request | `workspace_read.py`, `document_text.py` | `workspace_root`, `path`, `start_line`?, `lines`?, `extract`? (§5.8) | `path`, `sha256`, `bytes`, `binary`, `text`, `lines`, `first_line`, `last_line`, `truncated`; with `extract`, `format` and `extracted` |
| `workspace.search` | request | `workspace_read.py` | `workspace_root`, `pattern`, `path`? | `pattern`, `matches`, `files_scanned`, `match_count`, `truncated`, … |
| `workspace.find` | request | `workspace_read.py` | `workspace_root`, `pattern`, `path`? | `pattern`, `path`, `files`, `match_count`, `truncated` |
| `workspace.map` | request | `repo_map.py` | `workspace_root`, `max_chars`? | `map`, `fingerprint`, `definitions`, `shown`, `partial`, `cached`, … |
| `workspace.edit` | request | `workspace_edit.py`, `workspace_move.py` | `workspace_root`, `session`, `op`? (`edit`, `move`, `delete`, §5.1.4), `path`, lines or `search` / `replace`, or `to_path` / `to_line`? | `path`, `sha256_before`, `sha256_after`, `bytes`, `lines`, `checked`, the window; a move `moved_lines`, `moved_sha256`, `source`, `target`; a delete `deleted` |
| `workspace.edit.finish` | request | `workspace_edit.py` | `workspace_root`, `session` | `session`, `files` |
| `workspace.signatures` | request | `signatures.py` | `workspace_root`, `wanted` (`path`, `names`) | `files` (per file `status`, `sha256`, per name `found` / `unknown` / `missing`) |
| `workspace.related` | request | `code_graph.py`, `code_lookup.py`, `project_facts.py`, `manifest_deps.py` | `workspace_root`, `seeds`, `hops`?, `max_files`?, `lookup`? (§5.7, §5.10) | `files` (`path`, `sha256`, `hop`, `seed`, `uses`, `used_by`), `partial`, `seeds_matched`, `seeds_unmatched`; with `lookup`, `lookup` (§5.7, §5.10) |
| `workspace.graph` | request | `code_graph.py` | `workspace_root`, `known` (path to sha256), `max_files`? | `files` (`path`, `sha256`, `status`, `defs`, `refs`, `truncated`) for the files whose sha256 the backend does not hold, `unchanged`, `removed`, `more`, `partial` |
| `workspace.console.prepare` | request | `console_run.py` | `workspace_root`, `git`, `private_git`? (§5.1.5) | `ok`, `worktree`, `branch`, `head`, `guard`, `private_git`, … |
| `workspace.console.collect` | request | `console_run.py` | `workspace_root`, `head`, `guard` | `ok`, `files`, `refused`, `restored`, … |
| `workspace.sandbox.image` | request | `stack_image.py` | `workspace_root` | `image` (the tag, or `null` with `error`), `toolchains`, `dockerfile_sha256`, `built`, `build_ms` (§5.1.3) |
| `workspace.sandbox.reset` | request | `sandbox_cache.py` | `workspace_root`, `scope`? | `volume`, `volume_reset`, `quarantine_cleared`, `environment_reset` (§5.1.2), … |
| `workspace.git.publish` | request | `git_workspace.py` | `workspace_root`, `branch`, `base`?, `title`?, `body`?, `forge_policy`? (§5.3) | `ok`, the branch, commit and request URL, `forge_policy`, `remote_contacted` |
| `workspace.git.merge` | request | `local_merge.py` | `workspace_root`, `branch`, `base`, `commit`?, `forge_policy`? | `ok`, `base`, `before`, `after`, `fast_forward`, `already` |
| `workspace.git.diff` | request | `git_diff.py`, `git_view.py` | `workspace_root`, `commit`, `base`?, `forge_policy`?; or `view` (`status`, `changed`, `diff`, `log`, `blame`, §5.6) with `base`?, `path`?, `limit`?, `start_line`?, `end_line`? | `ok`, `base_commit`, `merge_base`, `commit`, `files` (per file the hunks, `sha256_old`, `sha256_new`), `truncated`; a view: `view` and its fields (§5.6) |
| `workspace.git.base_status` | request | `git_base.py`, `git_local.py` | `workspace_root`, `base`?, `forge_policy`? (§5.3) | `is_repo`, and how the base stands against its remote, `forge_policy`, `remote_contacted`; read locally whatever the policy: `current_branch`, `clean`, `operation_in_progress`, `remote_url` (without credentials) |
| `workspace.git.sync_base` | request | `git_sync.py` | `workspace_root`, `base`?, `forge_policy`? (§5.3) | `ok`, `outcome` (`updated`, `up_to_date`, `ahead`, or the refusal: `forge_disabled`, `dirty`, `operation_in_progress`, `diverged`, `remote_unreachable`…), `reason`, `base`, `remote`, `remote_url`, `before`, `after`, `behind`, `ahead`, `forge_policy`, `remote_contacted` (§5.4) |
| `workspace.self_host.checkout` | request | `self_host_checkout.py` | `name` (§5.9) | `ok`, `path`, `created`, `refreshed`, `previous_head`, `head`, `source_head`, `files` |
| `workspace.self_host.git` | request | `self_host_git.py` | `view` (`branches`, `land_preview` with `branch`, `origin`, `push_preview`, `fix_commit` with `issue`; §5.11) | `ok`, `view` and its fields |
| `workspace.self_host.land` | request | `self_host_git.py` | `branch`, `expected_head`, `land_owner`? (§5.11) | `ok`, `exit_code`, `outcome`, `landed`, `main_before`, `main_after`, `record`, `output_tail` |
| `workspace.self_host.push` | request | `self_host_git.py` | `main`, `origin_main` (§5.11) | `ok`, `before`, `after`, `main`, `pushed`, `notes_ref`, `refspecs`, `remote_url` |
| `workspace.git.reviews` | request | `platform_reviews.py` | `workspace_root`, `url`, `provider`, `forge_policy`? (§5.3) | `ok`, `url`, `comments`, `truncated` |
| `workspace.git.read_at` | request | `git_read_at.py` | `workspace_root`, `base`?, `commit`?, `paths`?, `folders`?, `list`? | `ok`, `commit`, `base`, `files` (per path `status` and `content_b64`), `folders` (every file under each, sorted), `tree`?, `sandbox_required` (§5.5) |
| `workspace.app.check` | request | `app_run.py`, `browser_probe.py`, `security_probe.py`, `run_logs.py` | `workspace_root`, `start_command`, `port`, `probe_path`?, `e2e_command`?, `sandbox`?, `browser_probe`?, `security_probe`?, `full_log`? | `listening`, probes, the e2e run, `browser_probe`?, `security_probe`?, `full_log`?, or `error` |
| `workspace.git.integrate` | request | `sprint_integration.py` | `workspace_root`, `branch`, `merge`, `forge_policy`? (§5.3) | `ok`, the integration branch and its merges, or the conflict, `forge_policy`, `remote_contacted` |
| `workspace.deliver` | JetStream, consumed | `daemon.py` | — | A delivery queued while the daemon was offline: `job_id`, `workspace_root`, `files`, `git`? |
| `workspace.delivered` | JetStream, published | `daemon.py` | — | The `workspace.generate` reply with `job_id`, `company_id`, `sprint_id`, `item_id`, `host` |
| `glassroom.*` | published | `daemon.py` | — | A status line on `glassroom.<sprint_id>` |

The schemas allow fields they do not name: a reader ignores what it does not
know, which is what lets a field be added without a new version (§7).

### 5.1 Full logs and the browser probe (additive, issue #315)

Two optional request fields, added within version 1 (§7): a daemon that
predates them ignores them and replies as before, and the backend records
that as not observed, with the reason.

**`full_log`** on `workspace.execute` and `workspace.app.check`:
`{"bucket": "FABRIQUE_RUN_LOGS", "name": "run-<hex>"}` (letters, digits, `_`
and `-`, at most 64 each; the bucket carries `NATS_NAMESPACE` as
`<namespace>_FABRIQUE_RUN_LOGS`). The daemon keeps each stream whole, up to
`FABRIQUE_FULL_LOG_MAX_MB` (50 MiB) on its machine, and uploads it after the
run as the object `<name>/<stream>` in that JetStream object store (the
standard layout: stream `OBJ_<bucket>`, subjects `$O.<bucket>.C.>` and
`$O.<bucket>.M.>`). The streams are `stdout` and `stderr` for
`workspace.execute`; `log` (host) or `log.stdout` / `log.stderr` (container)
and `e2e.stdout` / `e2e.stderr` for `workspace.app.check`. The reply's
`full_log`:

| Field | Meaning |
| --- | --- |
| `bucket`, `max_bytes` | Where it uploaded, and its cap per stream. |
| `objects` | Per stream: `stream`, `object`, `bytes` (what the object holds), `total_bytes` (what the stream printed in all), `truncated` (cut at the cap). |
| `error` | The upload failed (no bucket, no permission, a broker error); `objects` is then empty or partial. |
| `reason` | Nothing to upload: the command did not run (refused, no such folder). |

The reply never carries a hash, and the schema refuses one (`sha256`,
`digest`) in an object's entry: the backend reads each object back and
computes its size and sha256 itself (rule 6 of `docs/POSITIONING.md`). The
broker lets the daemon's user write chunks and metadata in that one bucket,
read the bucket's info and an object's metadata, and nothing else there: no
purge, no delete. The bucket is the backend's to create (with JetStream),
with its retention (`FULL_LOG_RETENTION_DAYS`, `FULL_LOG_BUCKET_MAX_MB`).

**`browser_probe: true`** on `workspace.app.check`: when the application
listens and an e2e command is about to run, the daemon first runs
`images/browser/probe-browser.js` with `node -e` where the command runs (the
project's folder, or inside the container) and replies `browser_probe`:
`available` (a browser launched, painted a page and read it back),
`observed_by: daemon`, and as the probe found them `from` (`image`: the
browser sandbox image's own pinned Playwright, `FABRIQUE_PLAYWRIGHT_DIR`;
`project`: the project's), `package`, `playwright`, `browser`, or `reason`,
with `exit_code`. Without an e2e command, or without the field, no probe runs
and the reply has no `browser_probe`.

### 5.1.1 The security probe (additive, issue #540)

**`security_probe: true`** on `workspace.app.check`: once the application
listens (before the e2e command), the daemon sends it a fixed set of requests
from where it runs (the daemon's own process in host mode;
`python3 -c <security_probe.py> <base_url> <path>` inside the application's
container in container mode, its printed line read back and bounded again)
and replies `security_probe`: `observed`, `observed_by: daemon`, `ran_in`
(`host` / `container`), `base_url`, `scheme`, `path` and `requests`, one
entry per probe, each with the `request` sent (`method`, `path`, `headers`),
the `status` (or `null` with `error`), the response `headers` as
`[name, value]` pairs (each value cut at 1024 characters, at most 40) and
`body_bytes`:

| Entry | Request | Kept beyond the status |
| --- | --- | --- |
| `get` | `GET <probe_path>` | every header; `cookies`: each `Set-Cookie` parsed (`name`, `value_bytes`, `secure`, `httponly`, `samesite`, `path`, `domain`, `expires`, `max_age`, `attributes`); a cookie's value is never kept, in `cookies` or in `headers` |
| `cors_origin`, `cors_credentials` | the same GET with `Origin: https://evil.example`, the second with a cookie | `Access-Control-*`, `Vary` |
| `preflight` | `OPTIONS` from that origin asking for `PUT` and `authorization` | `Access-Control-*`, `Vary`, `Allow` |
| `options`, `trace` | `OPTIONS`; `TRACE` with a random `X-Fabrique-Probe` header | `Allow`; `echoed` (whether the TRACE answer reflected that header), never the body |
| `not_found`, `malformed` | `GET /fabrique-probe-<hex>-missing`; `POST <probe_path>` with a truncated JSON body | `body_head` (2000 characters), `Content-Type`, `Server`, `X-Powered-By` |
| `git_head`, `env_file` | `GET /.git/HEAD`; `GET /.env` | `body_head` (200 characters) for the first; `env_keys` (the names the file assigns, never a value) for the second |

Each request has a 5 s bound and the whole probe 40 s; a probe that could not
run says so with `observed: false` and `reason` (no `python3` in the image,
the bound). The daemon judges nothing: the backend maps the answers to OWASP
WSTG items (`services/evidence/security_probe.py`). Without the field no
request is sent and the reply has no `security_probe`; a daemon from before
it ignores the field, and the backend records the probe as not observed.

### 5.1.2 The environment volume (additive, issue #315)

**`environment: true`** and **`scope`** (letters, digits, `:`, `_`, `.`, `-`,
at most 64; the backend sends `company:<id>`) in the `sandbox` of
`workspace.execute` and `workspace.app.check`, announced as the feature
`sandbox_environment`. In container mode the daemon mounts the project's
environment volume at `/fabrique-env` (`sandbox_cache.prepare_env`):
read-write for `purpose: install`, which creates it, read-only for every
other run, and not at all before an install filled it. The reply's
`sandbox.environment`: `state` (`created`, `reused`, `absent`, `quarantined`,
`refused`, `unavailable`, `not_used`), `volume`, `mounted`, `writable`,
`project_key`, `scope`, `manifests_sha256` (the daemon's key material, not
evidence of what was installed), `reason`, and after the run `after`,
`failed_installs`, `quarantined`, `evicted`. `workspace.sandbox.reset` takes
the same `scope` and replies `environment_reset` (`volumes`, each with what
its removal did, `quarantine_cleared`, `quarantined` while one is left);
`workspace.status` replies `sandbox_environment` (`enabled`, `quarantined`).
A daemon from before it ignores the fields and replies without them.

### 5.1.3 The stack image (additive, issue #766)

A `sandbox` with `stack_image: true` and no `image` asks for the project's
own image (`stack_image.py`): the daemon reads the toolchains from the marker
files at the folder's root and in its direct folders (`python`, `node`,
`go`), writes the Dockerfile from fixed templates (nothing from the project
but those names), and runs from `fabrique-stack:<16 hex of the Dockerfile's
sha256>`, built first when the machine does not have it (no build context;
one build per tag at a time; at most `FABRIQUE_STACK_BUILD_SECONDS`, 1800).
The run's `sandbox` then carries `stack_image` (`image`, `toolchains`,
`dockerfile_sha256`, `built`, `build_ms`); an image that cannot be had makes
the run fail with the reason, never the old default in its place. A request
with `image` keeps that image; one with neither, from a backend before this,
keeps `python:3.12-slim`. `workspace.sandbox.image` builds it ahead (the
backend asks before a delivery's commands, so their own timeouts do not
include the build) and replies the same fields, or `error` with the reason.
Every `workspace.execute` reply carries `duration_ms`, the daemon's clock
around the command.

### 5.1.4 Moving lines and deleting an emptied file (additive, issue #766)

`workspace.edit` takes `op`: `edit` (the default, as before), `move` or
`delete` (`workspace_move.py`, design in
`docs/design/move-and-delete-files.md`). `move` takes `path`, `start_line`,
`end_line`, `to_path` and `to_line`?, cuts those lines and inserts the same
bytes into `to_path` (created when missing), applied to both files or neither,
and replies `moved_lines`, `moved_sha256` (computed by the daemon), `source`
and `target` (each `path`, `sha256_before`, `sha256_after`, `lines`; the
target also `created`). `delete` takes `path` and removes the file only when
it is empty or whitespace; it replies `deleted`, `sha256_before`. At
`workspace.edit.finish`, a created file carries `created: true` and a deleted
one `deleted: true` with no `content`; every original is put back.

`workspace.generate` takes a file entry `{"path", "delete": true,
"sha256_expected"}`: the file is removed only while it still hashes to
`sha256_expected` (the daemon's own `finish` report), is a regular file and
may not hold secrets. Its status is `deleted` (with `sha256_before`, and
`change: deleted`, `previous_sha256`, `deletions` and the `diff` of the bytes
it removed, as for a write) or `rejected_delete` (with `error`). A deletion is committed on the task's branch
like a write, and an undo writes the file back. A daemon announces both as
features: `"workspace.edit": ["delete", "move"]` and `"delete"` on
`workspace.generate`; the backend offers neither to a daemon that does not.

### 5.1.5 The task's worktree for the Developer's tools (additive, issue #766)

`workspace.console.prepare` takes `private_git`: `false` prepares the task's
worktree as for a console producer (the branch from `git.base` when new, the
done prerequisites in `git.merge` merged, put back at its `HEAD` with
untracked files removed) but keeps git's own `.git` pointer and gives it no
private repository; the reply says `private_git: false`. Missing or `true`:
the console producer's private repository, as before. The backend sends it
before a task's first turn under the Git workflow, so the Developer's tools
(reads, edits, move and delete, `run_tests`) run in the task's worktree from
the first attempt, and the delivery (`workspace.generate` with the same
`git.branch`) commits in that same worktree. A daemon announces it as
`"workspace.console.prepare": ["forge_policy", "private_git"]`; the backend
sends it only when every daemon does, since an older one ignores the field
and would add the private repository. Design:
`docs/design/first-attempt-in-task-worktree.md`.

### 5.2 `workspace.execute` and the daemon's sandbox floor (additive, issue #525)

Every
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
`sandbox.purpose` takes `test`, `install`, `check`, `program` (§5.12) or `assistant` (a console
assistant's consultation, sent by backends before issue #750 with the
company's sandbox, its network setting and the names in `sandbox_env` and
`console_env`; the backend no longer sends it, and the daemon keeps
accepting it for older backends); anything else reads as `test`, so an older
daemon treats `assistant` as `test`. Both are additive: protocol version 1.
The application check (`workspace.app.check`) already normalized a missing
`sandbox` the same way.

### 5.3 The forge policy (issue #727)

Every `workspace.git.*` request, and the `git` part of `workspace.generate`
and `workspace.console.prepare`, carries `forge_policy`: `none`, `fetch` or
`publish`, the company's setting capped by the backend's `FORGE_POLICY_MAX`
(`services/platform/forge_policy_setting.py`). The daemon applies it in
`forge_gate.py`, capped again by its own `FORGE_POLICY_MAX`:

| Policy | The base fetched before a new branch (`git_base`) | `workspace.git.publish` (push, `gh`/`glab`) | `workspace.git.reviews` |
| --- | --- | --- | --- |
| `none` | No: the local base, `base_remote_not_contacted` names the upstream | Refused: 409, `code: "forge_disabled"`, nothing runs | Refused: 409, `code: "forge_disabled"` |
| `fetch` | Yes, as before | Refused, as above | Refused, as above |
| `publish` | Yes | As before | As before |

The observation names what was applied: `forge_policy` and `remote_contacted`
on the base's observation (the `git` answer of a delivery, `base_status` of
an integration and `workspace.git.base_status`), at the top of the
integration's reply, and on a publication. `workspace.git.merge` and
`workspace.git.diff` never contact a remote; they receive the field like
every git request.

**Fail closed.** A request without `forge_policy`, or with a value the daemon
does not know, is `none`. This is a deliberate change for a backend from
before #727: its deliveries start from the local base and its publications
are refused until it is upgraded, so neither an outdated backend nor a
compromised one that leaves the field out can widen contact (NIST SP 800-53
AC-6, CM-7). The field is announced as the `forge_policy` feature of each of
these subjects (§2.1).

**Never a prompt** (`git_env.py`, issue #745). Every git command runs with
`GIT_TERMINAL_PROMPT=0`, an empty `GIT_ASKPASS` and `GCM_INTERACTIVE=never`;
one that can reach a remote (`fetch`, `push`, `pull`, `ls-remote`, `clone`)
also with `GIT_SSH_COMMAND`: the person's own (`GIT_SSH_COMMAND`, else
`core.sshCommand`, else `GIT_SSH`, else `ssh`) with `-o BatchMode=yes` right
after the program, before their options, so it wins over a `BatchMode=no`
of theirs (ssh keeps the first value). A remote that wants a passphrase, a password or an unknown host
key fails at once: the base's observation says `remote_needs_interaction:
true` with the reason prefixed `remote_needs_interaction:`, and
`workspace.git.sync_base` answers that outcome.

`workspace.git.sync_base` (§5.4) is a remote operation like the fetch of the
base: under `none` it refuses with `outcome: "forge_disabled"` and
`remote_contacted: false`, and runs no remote command.

### 5.4 Bringing the local base up to date (issue #745)

`workspace.git.sync_base` fast-forwards the person's local base branch to its
remote's tip, and nothing else: `git fetch` of that one branch into a
temporary ref (deleted at once), then `git update-ref` with the old value
expected when no worktree has the base checked out, or `git merge --ff-only`
in the checkout that has it. No merge commit, no rebase, no reset; no shell,
no prompt, bounded in time, with the person's own git credentials.

Before contacting the remote it refuses, changing nothing, when a rebase,
merge, cherry-pick or revert is unfinished (`operation_in_progress`) or the
base's checkout has uncommitted changes to tracked files (`dirty`). After the
fetch it refuses when both sides moved (`diverged`, with `behind` and
`ahead`), the remote wanted a passphrase, a password or an unknown host key
confirmed (`remote_needs_interaction`), or the remote could not be read
(`remote_unreachable`). A refusal is
`ok: false` with the `outcome` and a `reason`, never an `error`: the refs and
the index are as they were. `updated` names `before` and `after`;
`up_to_date` and `ahead` move nothing. The remote-tracking branch is updated
only when the outcome is not a refusal. The reply is the daemon's
observation; the backend records it on the company (`base_sync_observed`).

### 5.5 Files at the default branch's head (additive, issue #700)

`workspace.git.read_at` reads files as a commit has them, never as the
checkout has them: a company's standards file (`.fabrique/standards.toml`)
and the files its `command` standards depend on are imported from the
**default branch's** head (`docs/archive/design/COMPANY_STANDARDS.md` §4), so a
delivery that edits them cannot change the rule it is judged by. The commit
is the request's `commit`, else the head of `base`, else of the branch
`origin/HEAD` names, else `main`, else `master`; never the checked-out
branch. Read with local git only (`ls-tree`, `cat-file`), whatever the forge
policy. Each file is `present` with its bytes in base64, or `missing`,
`symlink` (refused: a link's target is not the file reviewed), `not_file` or
`too_large` (1 MiB a file, 16 MiB a reply). `folders` lists every file under
each folder, recursively and sorted. The reply carries bytes, never a hash:
the backend hashes what it received. `sandbox_required` is the machine's
`FABRIQUE_SANDBOX_REQUIRED`, which the backend reads before it lets an Owner
activate a `command` standard (§12 decision 4 of the design). A malformed
request is a 400, a folder not on this machine a 404, and a folder that is
not a repository, a branch or commit it does not have, a 409.

### 5.6 The agents' read-only git views (additive, issue #766)

`workspace.git.diff` with `view` answers one of a fixed set of read-only
views of the repository at `workspace_root` (a task's worktree, or the
project), for the agents' `workspace__git` tool
(`backend/app/services/tools/workspace_git.py`), instead of an integration
diff; `commit` is then not needed. The daemon announces it as the `view`
feature of `workspace.git.diff` (§2.1), and the backend offers each
operation only when every daemon announces it: a daemon from before it would
read the request as an integration diff without a commit and refuse it.

| `view` | Request | Reply (beside `ok`, `view`) |
| --- | --- | --- |
| `status` | — | `branch` (or `null`, detached), `entries` (`code`, the porcelain v1 `XY`; `path`; `from` for a rename or copy), `count`, `truncated` (300 entries) |
| `changed` | `base`? | `base`, `merge_base`, `branch`, `files` (`path`, `change`: `added`, `modified`, `deleted` or `untracked`), `count`, `truncated` (300) |
| `diff` | `path`, `base`? | `base`, `merge_base`, `path`, `patch` (60,000 characters), `truncated`, `untracked` |
| `log` | `limit`? (1 to 20, default 10) | `branch`, `commits` (`commit`, the full id; `subject`, 200 characters), `limit` |
| `blame` | `path`, `start_line`? (default 1), `end_line`? (default `start_line` + 99) | `path`, `start_line`, `end_line` (at most 200 lines, never past the end), `file_lines`, `groups` (`commit`, `start_line`, `end_line`, `date`: the author date in UTC, `subject`, `uncommitted`), `truncated` |

`changed` and `diff` compare the working tree, committed or not, with the
merge base of `HEAD` and `base` (a local branch), else the default branch of
§5.5 (origin's HEAD, `main`, `master`). `changed` lists untracked files
(`git ls-files --others --exclude-standard`) too. Paths are relative to
`workspace_root`; a `path` that is absolute, has `..`, resolves outside it
(a symlink followed), or names VCS metadata or a file that may hold secrets
(`workspace_read.is_sensitive`) is a 400. Every git command is an argument
list without a shell and without a prompt (`git_env.py`); nothing is written
and no remote is contacted, whatever the forge policy. A folder not on this
machine is a 404; one that is not a repository, or a base it does not have,
a 409. The backend redacts secret-looking lines from a patch before a model
sees it, as it does a read's.

`blame` is announced as its own `blame` feature of `workspace.git.diff`, so
a daemon that serves the other views but not blame is never asked it. It
blames the working tree (`git blame --porcelain -L`), so its line numbers are
those `workspace.read` shows; a line not committed yet has the all-zero
commit, `uncommitted: true` and an empty subject. The porcelain's author and
committer names and e-mail addresses are dropped while parsing and never
returned, nor are the lines' contents: the date and the subject say when and
why. A `start_line` past the end is a 400; a path git does not track, a 409.

### 5.7 Where a name is defined and who uses it (additive, issue #766)

`workspace.related` with `lookup` answers a definition or references lookup
on the code graph instead of a walk from `seeds` (send `seeds: []`), for the
agents' `workspace__definition` and `workspace__references` tools
(`backend/app/services/tools/workspace_navigation.py`). The daemon announces
it as the `lookup` feature of `workspace.related` (§2.1), and the backend
offers the tools only when every daemon announces it; a daemon from before it
ignores the field and answers an empty walk without `lookup`, which the
backend reads as "not served", never as "no definition".

| `lookup.kind` | Request | Reply `lookup` |
| --- | --- | --- |
| `definition` | `name` (one identifier), `path`? (narrows to one file) | `definitions` (`path`, `sha256`, `line`, `depth`: 0 top level, 1 a method; `signature`, the repository map's, redacted; `parameters` for a top-level Python function or class whose signature `workspace.signatures` would report as `found`), `more` |
| `references` | `name`, `path`, or both | `references` (`path`, `sha256`, `names`, `lines`: the line numbers that name it, at most 10; `more_lines`), `more`; with `path`, the files that use the names that file defines (or its `name` alone), by the edge rule of `repo_map.reference_edges` |

Both carry `kind`, `name`, `path`, `path_found` (with `path`: whether it is
a file the graph read) and `common_name` (a name every program uses, such
as `get` or `len`, which the parse does not count as a reference).
`max_files` (default 20, at most 30) bounds the files listed; `more` counts
those left out. The files are the code graph's: never a folder a listing
skips, a hidden file, a file that may hold secrets or a symlink out of the
project. A `path` that is absolute, has `..` or names a folder is a 400.
Only paths, names, line numbers and hashes leave the machine, never a file's
contents.

### 5.8 A document's text (additive, issue #766)

`workspace.read` with `extract: "text"` answers the text of a document in the
project instead of its bytes, for the agents' `workspace__read_document`
(`backend/app/services/tools/workspace_documents.py`), in a read's reply
shape: numbered lines from `start_line`, at most `lines` (the read's window
and byte bound), `lines` the document's line count, plus `format` (`docx`)
and `extracted: true`. `sha256` and `bytes` are those of the file's bytes as
the daemon read them. Announced as the `document_text` feature of
`workspace.read`; a daemon from before it ignores the field and answers the
bytes (`binary: true`), which the backend never shows as text.

- **DOCX** (`document_text.py`, standard library only): `word/document.xml`,
  one line per paragraph, a heading style as `#` marks, tabs and line breaks
  kept. The part is at most 30 MiB uncompressed and a DTD or entity
  declaration is refused (415), so no entity expands.
- **PDF** is refused (415, "PDF text extraction is not available on this
  daemon yet"): none of the daemon's dependencies reads PDF.

The path rules are a read's: inside the project, symlinks followed, never a
file that may hold secrets (403). A file over 20 MiB is a 413; another suffix,
or a file that is not a DOCX, a 415.

### 5.9 The self-hosting snapshot (additive, issue #701)

`workspace.self_host.checkout` makes the snapshot an owner action's
self-hosting run works on (`docs/design/owner-actions.md`), the harness's
`checkout` step done by the only component that touches the user's files.
The request has one field, `name`: one folder name
(`^[a-z0-9][a-z0-9._-]{0,62}$`), never a path. What is copied and where are
this machine's settings, never the request's: `FABRIQUE_SELF_HOST_SOURCE`
(default: the La Fabrique repository the daemon runs from) and
`FABRIQUE_SELF_HOST_ROOT` (default `~/fabrique-self-host`). The daemon copies
the files git tracks or would track in the source (ignored files such as
`.env` stay out; a symlink is skipped, never followed) into `<root>/<name>`
and commits them as `main` of a fresh repository. It refuses (400) a name that
is not one folder name, a target that resolves outside the root, the source
or a folder inside it (or a root inside the source), and a non-empty folder
that is not an earlier snapshot. Each snapshot commit records the source
commit it copied (a `Source-Commit:` trailer). An earlier snapshot whose
source has moved on is refreshed: the source's files replace the tracked
files of its `main` in one new commit (`refreshed: true`; deletions carried,
task branches kept); the same source is answered as it is (`refreshed:
false`, `files: null`). A `main` whose head is not a snapshot commit, a
checkout off `main` and a working tree with changes or untracked files are a
409, never overwritten. The reply is what the daemon observed: `path`,
`created`, `refreshed`, `previous_head`, `head`, `source_head`, `files`. A
git failure is a 409.
Announced as a subject (§2.1); the backend asks only a daemon that announces
it, or one that announces nothing.

### 5.10 What the project declares (additive, issue #766)

`workspace.related` with `lookup.kind` set to `api_surface`, `config_vars`
or `dependencies` answers what the project's files declare, read statically,
for the agents' `workspace__api_surface`, `workspace__config_vars` and
`workspace__dependencies` (`backend/app/services/tools/workspace_facts.py`).
Each kind is announced as a feature of its own on `workspace.related` (§2.1),
and the backend offers a tool only when every daemon announces its kind; a
daemon from before it answers an empty walk without `lookup` (or, knowing
only §5.7, refuses the kind with a 400), which the backend reports as "not
served", never as "nothing found". `lookup.path`, optional, narrows to one
folder or file; an absolute path or one with `..` is a 400.

| `lookup.kind` | Reply `lookup` (beside `kind`, `path`, empty `definitions` and `references`, `more`) |
| --- | --- |
| `api_surface` | `openapi`: each committed `openapi.json`, `openapi.yaml`/`.yml` or `swagger.*` (at most 20, 2 MiB each) with `path`, `sha256`, `spec`, `title`, `version` and `operations` (`method`, `path`, `operation_id`, `summary`), a YAML one read by its indentation; `routes`: the routes FastAPI-style decorators declare (`@x.get("/p")`, `post`, `put`, `patch`, `delete`, `head`, `options`, `trace`, `websocket`, `api_route(methods=…)`), each `method`, `path` (with the same module's `APIRouter(prefix=…)`), `function`, `file`, `line`, `router`; `mounts`: each `include_router` call (`file`, `line`, `router`, `prefix`), whose prefix is not applied. No server is started and nothing is imported. |
| `config_vars` | `variables`: each read of an environment variable (`os.environ[…]`, `os.environ.get`/`setdefault`, `os.getenv`, a pydantic `BaseSettings` field with its `env_prefix` or alias, `process.env.X`, `import.meta.env.X`), with `name`, `file`, `line`, `source`, `required` (no default) and `default` when it is a literal (at most 120 characters). A name that looks like a secret (`secret`, `token`, `passw`, `key`, `auth`…) never has its default sent: `default_withheld: true`. |
| `dependencies` | `manifests` (`path`, `sha256`, `ecosystem`, `count`, `error`?) and `dependencies`: each direct dependency of a `pyproject.toml` (`[project]` and its optional groups, `[dependency-groups]`, Poetry's tables), a `requirements*.txt` (or a `.txt` in `requirements/`) or a `package.json`, with `manifest`, `ecosystem` (`pypi`, `npm`), `name`, `version` as declared (`""` when none; `(direct reference)` for a URL, Git or local path, never the URL) and `group`. A `pyproject.toml` needs `tomllib` (Python 3.11+) or `tomli`; without them it is listed with `error: "no TOML reader"`. |

Every kind adds `files` (each file a row came from, with the sha256 of the
bytes read; at most 100), `files_scanned` and `scanned_sha256` (the sha256
over every scanned file's `path sha256`, sorted). At most 200 rows; `more`
counts the rest. The source files are the code graph's candidates and the
documents and manifests come from a walk with the same guards: never a
folder a listing skips, a hidden file (so never a `.env`), a file that may
hold secrets or a symlink out of the project. A source file's extract is
cached by its sha256 in the repository map's parse cache. `partial` says the
project was larger than the daemon reads or the deadline passed.

### 5.11 Landing and mirroring the self-hosting source (additive, owner actions)

The owner actions `land_branch` and `push_main`, and the observations
`close_issue` needs (`docs/design/owner-actions.md`), on the repository this
machine names in `FABRIQUE_SELF_HOST_SOURCE` (§5.9), never one the request
names.

- `workspace.self_host.git` is read-only. `branches`: the local branches but
  `main`, newest first (at most 200). `land_preview` (`branch`): the branch,
  which must be one `branches` lists and pass `git check-ref-format
  --branch`; its commits in `main..branch`, the diffstat and changed files of
  `main...branch` (renames off, as land.sh), and `tests.files` of main's
  `docs/architecture/contexts.json`, from which the backend computes the
  protected paths. `origin`: the remote's URL without credentials.
  `push_preview`: after fetching origin's `main`, the commits of
  `origin/main..main`, `fast_forward`, `up_to_date`, `has_record` (land.sh's
  record of `main`, file or note) and `notes`. `fix_commit` (`issue`): after
  that fetch (`fetched` false when it failed), the commits on `main` whose
  message names `#<issue>`, each with `on_origin`.
- `workspace.self_host.land` runs `bash scripts/land.sh <branch>` as an
  argument list, never `--push`, after checking the branch is listed and
  still at `expected_head` (409 otherwise). The daemon's own `LAND_*`
  variables are dropped; `LAND_OWNER=1` is set for that process only when
  `land_owner` is `true`; `PYTHON_KEYRING_BACKEND` defaults to the null
  keyring so the proxy's Poetry install never waits on a keyring. Bounded by
  `FABRIQUE_SELF_HOST_LAND_TIMEOUT` (default 5,400 s; the process group is
  killed past it). The reply is the exit code and its meaning, whether `main`
  moved, `main` before and after, land.sh's record of the new `main` as it
  wrote it (a claim, as it says), and the output's last 6,000 characters.
- `workspace.self_host.push` fetches, then refuses (409) unless `main` and
  `origin/main` are where the preview saw them, `origin/main` is an ancestor
  of `main`, there is something to push and `main` has a record. It pushes
  `git push --atomic origin <main>:refs/heads/main` and
  `refs/notes/verification` when present, never `--force` or a `+` refspec,
  with this machine's credentials and no prompt (`git_env.py`), then fetches
  again and answers `before`, `after` and the refspecs. A remote that cannot
  be reached is a 502.

### 5.12 A declared program (additive, feature `program`)

`workspace.execute` with `sandbox.purpose: "program"` runs a program a
person declared in the company's stack profile, for the agents'
`workspace__run_program` (`backend/app/services/tools/program_runs.py`,
`docs/design/project-programs-and-screenshots.md`). It is announced as the
feature `program` of `workspace.execute` (§2.1), and the backend offers the
tool only when every daemon announces it. A daemon from before it reads the
purpose as `test`, which is why the backend never sends it unannounced.

The request adds, inside `sandbox`: `workdir: "ro"` and `program`
(`folder`, the working directory under `/workspace` as a plain relative
path; `artifacts_mb`; `metrics_file`, one `.json` name under `/artifacts`).
`command` is plain words (letters, digits and `_ . = : / + -`, no `..`, no
absolute path), the declared argv and one `--<name>=<value>` per typed
parameter; the daemon checks that again and refuses anything else
(`refused: true`), after the command policy. What the daemon does
(`program_run.py`):

- `container` only. A request whose own `mode` is `host` or `bwrap` is
  refused with `error: "sandbox unavailable: a program runs only in
  container mode, not <mode>"` and nothing runs; `FABRIQUE_SANDBOX_REQUIRED`
  never raises a program request to a container.
- `--network none` whatever `network` says; the worktree mounted `:ro` at
  `/workspace` (its Git metadata read-only as today); writable only `/tmp`
  (512 MiB tmpfs) and `/artifacts`, the folder
  `<FABRIQUE_ARTIFACTS_ROOT>/<scope>/<run id>/` created for this run outside
  every worktree; `--pull=never`; the dependency cache not mounted, the
  environment volume read-only when asked.
- Time, CPUs, memory (with `--memory-swap` equal) and processes as asked,
  each capped by `FABRIQUE_PROGRAM_MAX_SECONDS`, `_CPUS`, `_MEMORY_MB` and
  `_PIDS`; `/artifacts` by the smaller of `artifacts_mb` and
  `FABRIQUE_PROGRAM_MAX_ARTIFACTS_MB`. No request raises a maximum.
- Before it starts: run folders older than
  `FABRIQUE_ARTIFACTS_RETENTION_DAYS` are removed, and a free space under the
  root below `FABRIQUE_ARTIFACTS_MIN_FREE_MB` refuses the run. While it runs,
  the folder is measured every second (no symlink followed) and the container
  is killed past its cap (`artifact_limit: "watch"`; a bind mount has no
  per-folder quota). It can overshoot by about a second of writes.

The reply is the usual one (`stdout` and `stderr` apart, `exit_code`,
`timed_out`, `sandbox` with `workdir_mode: "ro"` and `purpose: "program"`)
plus `program`: `run_id`, `limits` (as applied), `maxima`, `free_mb_before`,
`pruned_runs`, `stdout_truncated`, `artifacts` (`files`: each `path`,
`bytes` and `sha256` read back from disk after the run, a symlink as
`link: true` and never read; `total_bytes`, `truncated` past 1,000 entries,
`limit_mb`, `artifact_limit`, `killed_for_size`, `peak_bytes`) and
`metrics_file` (`content_b64`, the whole file when it is a regular file of
at most 256 KiB, or `reason`). The daemon never parses a metric and never
sends a hash of the metrics file: the backend decodes and hashes what it
received. A run killed for size has `success: false` and an `error` naming
the cap. Fixtures: `workspace.execute/program-refused-in-host.json` and
`program-refused-in-bwrap.json`; the container runs are covered by
`vscode-daemon/tests/test_program_run.py` (a fake runtime) and
`test_program_run_container.py` (a real one, skipped without it).

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
`workspace.map`, `workspace.related`, `workspace.graph`, `workspace.signatures`,
`workspace.hash`, `workspace.sandbox.reset`, `workspace.inspect` and
`workspace.browse` (`workspace.execute` also with `full_log`, replayed
against an in-memory object store, and read back and hashed by the backend's
side; a console assistant's consultation on the host; a request without
`sandbox` refused by a floor whose runtime is missing; and a declared program
refused in `host` and in `bwrap` mode, §5.12); the other subjects are
covered by their schema, and their handlers by their own tests
(`workspace.app.check`'s probe and full log in
`backend/tests/test_browser_image.py` and `test_run_logs.py`, its security
probe in `test_security_probe.py`, which validates a real reply against the
schema). To add one, write the file with the request and
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
