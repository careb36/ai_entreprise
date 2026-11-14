# The execution environment (issue #315)

Agent-written code is installed, tested and started by the workspace daemon,
in the sandbox the company asked for, and the Verifier judges only what the
daemon reported. This note records where #315 stood on 2026-11-14, what this
slice adds, what was taken from AixleHQ/flow and what was not, and what is
left. The modules are described in [workspace](../contexts/workspace.md),
[evidence](../contexts/evidence.md) and
[configuration](../CONFIGURATION.md).

## Where it stood

Already built before this slice: stack profiles per stack, overridable by
the deployment and the company (`services/workspace/stack_profiles.py`,
`Company.stack_profile`); the install before the tests when a manifest the
delivery wrote has a new sha256 (`services/workspace/dependency_install.py`,
the sha256 the daemon read back from disk, `Company.dependency_installs`
written only after exit 0); the application started, probed, e2e-tested
and stopped by the daemon (`vscode-daemon/app_run.py`,
`workspace.app.check`); QA's Playwright smoke test per criterion
(`services/evidence/browser_smoke.py`); and the Verifier's
`dependencies_installed`, `application_started` and `e2e_run` checks
(`services/evidence/app_check.py`), a refutation sending the task back
(`delivery_gate.CODE_PROBLEMS`).

## This slice

- **Which image ran** (`vscode-daemon/sandbox_image.py`). Before every
  container run (`workspace.execute` in container mode and the application
  check's container) the daemon asks the runtime for the image's content
  ID and registry digest (`image inspect`, the same JSON from Docker and
  Podman) and runs the container from that ID, so a tag that moves between
  the observation and the run cannot change what ran. An image not yet on
  the machine is pulled by the run and observed after it, and the report
  says so (`observed: after_run`). The report's `image_identity` carries
  `id`, `digest` (or `digest_reason` for an image built or loaded on the
  machine), `observed_by: daemon` and `observed`; host and bwrap modes,
  and a runtime that could not answer, give `id: null` with the `reason`.
  The backend records it with the sandbox report it already kept
  (`services/contracts/sandbox_report.py`): the `sandbox` check and the
  install check name the content ID and digest, and an identity that is not
  a `sha256:` content ID is recorded as none, with the reason.
- **The dependency cache** (`vscode-daemon/sandbox_cache.py`, opt-in with
  `DELIVERY_SANDBOX_CACHE`). In container mode a volume per project,
  `fabrique-cache-<key>` labelled `dev.fabrique.sandbox-cache=<key>`
  (`<key>` from the sha256 of the project's root; a task's worktree shares
  its main checkout's), is mounted at `/fabrique-cache` and the package
  managers' caches point there (pip, npm, Yarn, Go, Cargo,
  `XDG_CACHE_HOME`). After a run it is kept (reuse); after a failed install
  (the backend's install request says `purpose: install`, the daemon
  observes the exit status) it is removed (reset); three failed installs in
  a row quarantine the project's cache, with the reason in every report and
  in `workspace.status`, until a Manager resets it
  (`POST /v1/companies/{id}/sandbox/cache/reset`, the daemon's
  `workspace.sandbox.reset`, audited). The daemon only mounts or removes a
  volume that carries both its name and its label; a same-named volume
  without the label is refused and never removed. A new volume is made
  writable for the run's user by a fixed `chown` in a container with only
  `CHOWN` and no network (Docker under a non-root uid) or Podman's `:U`.
  `FABRIQUE_SANDBOX_CACHE=off` on the daemon's machine refuses it. The
  volume is writable by every run of the project, so one task's run can
  leave something in it for the next (a poisoned package cache): that is
  why it is off by default, why its state is in every report, and why a
  failed install removes it rather than trusting it.

Exercised against a real Docker 29 daemon (containerd image store): the
content ID and digest recorded before the run, an `alpine` image pulled by
the run and observed after it, the application check's container, the cache
created, written under uid 1000, reused by the next run, removed after each
failed install, quarantined at the third and lifted by a reset, and a
same-named unlabelled volume refused and left in place. With the containerd
image store Docker's image ID is the image index's digest, so `id` and
`digest` can coincide; with the classic store `id` is the configuration's
sha256 and differs from the registry digest.

## What Flow taught

AixleHQ/flow (Apache-2.0) runs agent sessions in containers on a person's
own machine through a host runner. Design was taken; no code was copied
(Flow is Go and Rails), so no NOTICE entry is needed.

- **Scope by ownership, never by enumeration**
  (`runner/protocol/PROTOCOL.md` §6.4, `env.scrub` and `env.reset`): act
  only on volumes selected by name and label; refuse a volume with the
  name and without the label (`volume_not_owned`, `DESIGN.md` §7.2 "as
  built"); never walk the disk. Taken as is.
- **Reuse and reset** (`docs/design/agent-runtimes/DESIGN.md` §7.1, §7.2):
  caches survive a good session, a failed session gets a reset. Taken, with
  a failed install as the failed session: a failing test run does not
  implicate the cache.
- **Out of rotation after three failures in a row**
  (`DESIGN.md` §5.2 and §7.2, `runners.max_consecutive_failures`, default 3;
  Return to service runs a reset first): taken as the quarantine and the
  Manager's reset, which clears the quarantine only once the volume is gone.

## What was not copied

- Flow keeps self-reports as fields of its records: a machine's check-in
  `facts`, its runner and protocol versions, recorded as it states them
  (`PROTOCOL.md` §5.9). Here
  a field is either the daemon's observation or labelled a claim; the cache
  state and the image identity are the daemon's observations of what the
  runtime did, and the backend refuses an identity that is not a content ID
  rather than storing whatever arrived.
- Flow returns no image digest (`PROTOCOL.md` §6.1: "no digest is
  returned"); a run there says which reference was pulled, not which bytes
  ran. Here every container run records the content ID it was started from.
- Flow's workspace volume and scrub of `/workspace/outputs`: the worktree is
  the person's files, mounted from their disk, and the task branch is the
  unit; there is nothing to scrub.

## What remains for #315

- **What an install leaves in a container.** The cache holds downloads, not
  installed packages: in container mode an install keeps only what it writes
  into the worktree (`node_modules` yes, a Python `site-packages` no). A
  per-project environment volume (a virtualenv, `GOPATH`) with the same
  lifecycle is next.
- **The application check's container** reports its image identity but does
  not mount the cache yet (its report says `not_used`).
- **A pinned browser image** for the Playwright smoke tests, built for amd64
  and arm64 with its tools pinned by version and checksum, as Flow's
  `docker/base/Dockerfile` pins and verifies each download per
  architecture; today the sandbox image is whatever
  `DELIVERY_SANDBOX_IMAGE` names, and the identity now records which one ran.
- **Full logs**: the daemon keeps a bounded tail; the whole log should go to a
  JetStream object store with the backend computing its sha256 on receipt
  (rule 6 of the positioning: never a hash from the party recorded).
- **A versioned daemon protocol with a heartbeat** (Flow's `PROTOCOL.md` §5.10
  and §7): today the backend learns a daemon's abilities from its replies'
  shape, and an older daemon is read as "reported no image identity".
- **Install state keyed by sandbox.** `Company.dependency_installs` is keyed
  by folder only; a company that moves from host to container mode is not
  re-installed until a manifest changes.
