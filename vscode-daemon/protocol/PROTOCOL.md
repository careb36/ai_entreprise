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
| `features` | Per subject, the optional request fields added within version 1 that it honours (§5.1, §5.1.1, §5.2). |

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
| `workspace.execute` | request | `daemon.py`, `shell_exec.py`, `sandbox.py`, `run_logs.py` | `command`, `workspace_root`, `sandbox`?, `command_policy`?, `timeout_seconds`?, `full_log`? | `success`, `stdout`, `stderr`, `exit_code`, `timed_out`?, `sandbox`?, `full_log`? |
| `workspace.browse` | request | `host_folders.py` | `path`? | `host`, `path`, `parent`, `entries`, `shortcuts`, `roots`, `truncated` |
| `workspace.inspect` | request | `host_folders.py` | `path`, `snapshot`? | `host`, `path`, `name`, `exists`, `is_dir`, `git`, `snapshot`? (with `playwright`: the version the project pins, `playwright_pin.py`, #315) |
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
| `workspace.graph` | request | `code_graph.py` | `workspace_root`, `known` (path to sha256), `max_files`? | `files` (`path`, `sha256`, `status`, `defs`, `refs`, `truncated`) for the files whose sha256 the backend does not hold, `unchanged`, `removed`, `more`, `partial` |
| `workspace.console.prepare` | request | `console_run.py` | `workspace_root`, `git` | `ok`, `worktree`, `branch`, `head`, `guard`, … |
| `workspace.console.collect` | request | `console_run.py` | `workspace_root`, `head`, `guard` | `ok`, `files`, `refused`, `restored`, … |
| `workspace.sandbox.reset` | request | `sandbox_cache.py` | `workspace_root`, `scope`? | `volume`, `volume_reset`, `quarantine_cleared`, `environment_reset` (§5.1.2), … |
| `workspace.git.publish` | request | `git_workspace.py` | `workspace_root`, `branch`, `base`?, `title`?, `body`?, `forge_policy`? (§5.3) | `ok`, the branch, commit and request URL, `forge_policy`, `remote_contacted` |
| `workspace.git.merge` | request | `local_merge.py` | `workspace_root`, `branch`, `base`, `commit`?, `forge_policy`? | `ok`, `base`, `before`, `after`, `fast_forward`, `already` |
| `workspace.git.diff` | request | `git_diff.py` | `workspace_root`, `commit`, `base`?, `forge_policy`? | `ok`, `base_commit`, `merge_base`, `commit`, `files` (per file the hunks, `sha256_old`, `sha256_new`), `truncated` |
| `workspace.git.base_status` | request | `git_base.py`, `git_local.py` | `workspace_root`, `base`?, `forge_policy`? (§5.3) | `is_repo`, and how the base stands against its remote, `forge_policy`, `remote_contacted`; read locally whatever the policy: `current_branch`, `clean`, `operation_in_progress`, `remote_url` (without credentials) |
| `workspace.git.sync_base` | request | `git_sync.py` | `workspace_root`, `base`?, `forge_policy`? (§5.3) | `ok`, `outcome` (`updated`, `up_to_date`, `ahead`, or the refusal: `forge_disabled`, `dirty`, `operation_in_progress`, `diverged`, `remote_unreachable`…), `reason`, `base`, `remote`, `remote_url`, `before`, `after`, `behind`, `ahead`, `forge_policy`, `remote_contacted` (§5.4) |
| `workspace.git.reviews` | request | `platform_reviews.py` | `workspace_root`, `url`, `provider`, `forge_policy`? (§5.3) | `ok`, `url`, `comments`, `truncated` |
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
`sandbox.purpose` takes `test`, `install`, `check` or `assistant` (a console
assistant's consultation, sent by `services/producers/console_assistants.py`
with the company's sandbox, its network setting and the names in
`sandbox_env` and `console_env`); anything else reads as `test`, so an older
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
`ahead`) or the remote could not be read (`remote_unreachable`). A refusal is
`ok: false` with the `outcome` and a `reason`, never an `error`: the refs and
the index are as they were. `updated` names `before` and `after`;
`up_to_date` and `ahead` move nothing. The remote-tracking branch is updated
only when the outcome is not a refusal. The reply is the daemon's
observation; the backend records it on the company (`base_sync_observed`).

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
side; a console assistant's consultation on the host; and a request without
`sandbox` refused by a floor whose runtime is missing); the other subjects are
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
