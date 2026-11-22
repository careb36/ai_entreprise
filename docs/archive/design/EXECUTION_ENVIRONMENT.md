# The execution environment (issue #315)

Agent-written code is installed, tested and started by the workspace daemon,
in the sandbox the company asked for, and the Verifier judges only what the
daemon reported. This note records where #315 stood on 2026-11-14, what this
slice adds, what was taken from AixleHQ/flow and what was not, and what is
left. The modules are described in [workspace](../../contexts/workspace.md),
[evidence](../../contexts/evidence.md) and
[configuration](../../CONFIGURATION.md).

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
own machine through a host runner. For the first slice design was taken and
no code was copied (Flow is Go and Rails); the second slice adapts two small
files as code (below, "Taken from Flow, as code"), credited in `NOTICE`.

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

## The second slice (2026-11-15)

- **Done: a pinned browser image** (`vscode-daemon/images/browser/`). Node 22
  from `node:22-bookworm-slim` pinned by its multi-architecture digest;
  Playwright 1.64.1 from a `package-lock.json` installed with `npm ci
  --ignore-scripts` (npm checks each tarball against its sha512 integrity
  hash); the Chrome Headless Shell that Playwright 1.64.1 launches headless
  (revision 1248, Chrome 156.0.8078.4), downloaded from the Playwright CDN
  and checked against a sha256 pinned per architecture through
  `TARGETARCH` (amd64 and arm64), unpacked where Playwright looks for it;
  the runtime libraries from Debian; `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`, so
  nothing is downloaded at run time; `USER node`, and every file readable by
  the person's own uid, which the daemon runs it as. The build runs the probe
  as root and as `node` on amd64 (under QEMU the browser aborts, as Flow
  found). `DELIVERY_BROWSER_IMAGE` makes the application check run a browser
  e2e command (QA's smoke suite, or a `web` profile's e2e) in it, in
  container mode only; the run records the content ID and digest it started
  from like every container run. A TLS-intercepting proxy is handled with an
  optional build secret (`extra_ca`), which changes nothing about what is
  accepted: the integrity hashes and the sha256 decide.
- **Done: the browser probe** (`vscode-daemon/browser_probe.py`,
  `images/browser/probe-browser.js`). Before a browser e2e command the daemon
  launches a browser where the command runs, paints a page and reads it back,
  and reports `browser_probe` (`available`, `from: image|project`, the
  versions or the reason). `e2e_run` says "browser present: yes / no / not
  observed" from it. Only a probe of the image's own pinned Playwright turns
  an unexplained e2e failure into `runtime_missing`: the project's
  `node_modules` are shaped by agent-written code, so a "no" from there only
  annotates.
- **Done: full logs as evidence** (`vscode-daemon/run_logs.py`,
  `services/workspace/run_logs.py`). The install, the tests and the
  application check ask for the whole output; the daemon keeps each stream up
  to 50 MiB in a temporary file while the command runs (the reply's tail is
  unchanged), uploads it to the JetStream object store `FABRIQUE_RUN_LOGS`
  as `run-<hex>/<stream>`, and says which objects and how many bytes, never
  a hash. The backend reads each back, computes size and sha256 and records
  `full_log` beside the tail; an older daemon is `observed: false` with the
  reason. The daemon's NATS user may write that bucket's chunks and metadata
  and read its info, never purge or delete; an object it overwrites later
  would no longer match the sha256 on the evidence. **Retention**: the
  janitor's retention (`backend/janitor/retention.py`) deletes database rows
  and has no artifact store to follow, so the bucket keeps its own:
  objects expire after `FULL_LOG_RETENTION_DAYS` (30) and the oldest go
  first beyond `FULL_LOG_BUCKET_MAX_MB` (4096; `discard: old`, unlike
  nats-py's default `new`, so a full bucket never refuses new evidence). The
  evidence's sha256 outlives the object.
- **Done: install state per sandbox mode** (`dependency_install.install_key`):
  `<root>` for host, as records were kept before, `<mode>:<root>` otherwise.
  The key follows the mode the company asks for; a daemon that forces another
  (`FABRIQUE_SANDBOX_REQUIRED`) is visible in the run's `sandbox` report but
  does not change the key.
- **Done: the cache in the application check's container**, mounted as a test
  run's and kept after it (`purpose: check`).

Exercised for real here (Docker 29.9): the image built for amd64 with the
probe passing as root and as `node`; a Playwright test (`npx --no-install
playwright test e2e`, the smoke command) against a real Node application in
that image through `app_run.check_application`, network off, capabilities
dropped, the content ID recorded, the probe reporting Chromium 156 from the
image; the probe as an arbitrary uid (4321) with the network off; the full
log round trip against a real `nats-server` 2.10 with JetStream and the
generated permissions (the daemon's purge refused). The arm64 image was not
built here (no QEMU); its sha256 was computed from the archive, which holds
an aarch64 binary. Building needed the `extra_ca` secret: the session's
proxy intercepts TLS, and npm refused its certificate without it.

Both headless shell sha256 values were re-checked for #529 from another
machine behind a different proxy, and each archive's MD5 matched the
`x-goog-hash: md5=` the origin bucket stores for the object
(`storage.googleapis.com/chrome-for-testing-public/156.0.8078.4/…`, where
`cdn.playwright.dev` redirects): `dD2se6+Z6qcuIhM16pZvBw==` for
`linux64`, `Y7JjEbYwKEdpZANoezVM1Q==` for `linux-arm64`. That header is
the origin's own record, not a value from the party that computed the
pinned sha256; a network with no proxy at all has still not been used. The
first `browser-image-probe.yml` run (43746887213) built both architectures
under QEMU on the runner; the arm64 probe's browser aborted under emulation,
so the arm64 image has not been seen to run.

### Taken from Flow, as code

Flow's `docker/base/Dockerfile` and `docker/base/probe-browser.js` at commit
f9efa22 (Apache-2.0) are small enough to adapt, and were, with their notice
in each file's header and an entry in `NOTICE`: the per-architecture
download checked against a pinned sha256 (`TARGETARCH`, `case`, `sha256sum
-c`), the Playwright version pinned so the browser baked at build time is
the one launched at run time, a shared `PLAYWRIGHT_BROWSERS_PATH` readable by
a non-root user, and the build-time probe run as root and as the
unprivileged user on amd64 only. Changed: Flow keeps the chrome-for-testing
channel its MCP launches and deletes the headless shell; a Playwright test
run headless launches the headless shell, so this image keeps only that, and
downloads it directly to check it, since `playwright install` verifies
nothing. The probe drives Playwright directly instead of the MCP server and
prints one machine-readable line.

## What remains for #315

- **What an install leaves in a container** — built in the third slice
  (below). Left: an install that runs as root in the container (a daemon run
  as root) still writes into the image's `site-packages`, outside the volume;
  the dependency cache itself is keyed by project only, not by company.
- **Publishing the browser image.** Published from the release workflow
  (#529): `images.yml` pushes `ghcr.io/<owner>/<repo>/browser-sandbox:X.Y.Z`
  for `linux/amd64` with buildx — outside the five-image matrix, signed by
  digest with keyless cosign and attested with its CycloneDX SBOM as the
  five others. The manual `browser-image-probe.yml` builds both
  architectures under QEMU and runs the arm64 image with its probe; its
  browser aborts under emulation, so arm64 is published only once it has
  run on arm64 hardware (open). Operators pin
  `DELIVERY_BROWSER_IMAGE` to the digest the run printed
  (`docs/CONFIGURATION.md`, `docs/OPERATIONS.md`); pinning it in the compose
  file is left to the deployment. A project whose own `@playwright/test` is
  not one of the built releases looks for its own browser revision; since
  the fourth slice (below) it runs in the image of its release when one is
  built, and the mismatch is recorded when none is.
- **The full log in the UI.** The evidence records each object and its sha256;
  a view that streams an object back and checks it against the sha256 is not
  built.
- **A versioned daemon protocol with a heartbeat** (Flow's `PROTOCOL.md` §5.10
  and §7) is in place (`vscode-daemon/protocol/PROTOCOL.md`); the daemon's
  abilities are still learned from its replies' shape, not announced.

## The third slice: the environment volume (2026-11-18)

In container mode an install kept only what it wrote into the worktree:
Node's `node_modules` survived, a Python user installation or Go's module
cache did not. With `DELIVERY_SANDBOX_ENV` on, the daemon
(`vscode-daemon/sandbox_cache.py`, `prepare_env` / `finish_env` /
`reset_env`) mounts a second named volume, `fabrique-env-<key>`, at
`/fabrique-env`, with `PYTHONUSERBASE`, `POETRY_VIRTUALENVS_PATH`,
`WORKON_HOME`, `GOPATH` and `GOMODCACHE` pointing into it (and `GOCACHE` for
an install), and its `bin` folders appended to `PATH`, after the image's own.

- **Keyed like the cache, and narrower.** The key is the project (the main
  checkout of a worktree, as for the cache), the request's `scope` (the
  backend's `company:<id>`), the image the company asked for and the sha256
  of the project's Python and Go manifests (`manifests_digest`: the root and
  its direct folders, not dot folders). Two companies on one folder, two
  projects, two images or two sets of manifests never share an environment.
  The state, failure count and quarantine are per project and company, and
  every volume of it carries `dev.fabrique.sandbox-env.project=<project
  key>` beside its own `dev.fabrique.sandbox-env=<key>`.
- **Written only by an install.** `purpose: install` mounts it read-write and
  creates it; every other run (tests, the application check, an assistant)
  mounts it read-only, and not at all before an install filled it
  (`state: absent`).
- **Reset and quarantine as the cache.** A failed install removes that
  environment and counts; three in a row quarantine the project for that
  company until `POST /v1/companies/{id}/sandbox/cache/reset`, which now also
  removes every environment of the project and company (listed by the project
  label and the daemon's own state, each removed only with its name and
  label) and, once they are gone, drops the company's container-mode install
  records so the next delivery installs again. At most three environments are
  kept per project and company; a successful install evicts the least
  recently installed.

Taken as design: GitHub Actions' cache keyed on `hashFiles` of the lock files
(what Copilot coding agent's `copilot-setup-steps.yml` uses), and Flow's
ownership by name and label, already followed by the cache. No code was
copied. The threat model is in the pull request.

## The fourth slice: a browser image per Playwright release (2026-11-18)

A browser build matches one Playwright release. A project that pins another
`@playwright/test` than the image's looks for its own browser revision and
fails (`runtime_missing`) or, worse, runs against a build its tests were not
written for. One image cannot serve every project, so there is one per
release, and the check picks the one the project pins.

- **The releases are data.** `vscode-daemon/images/browser/playwright_versions.json`
  lists each release with its lockfile folder (the default's is the image's
  own `package-lock.json`, the others' `playwright/<release>/`) and the
  Chrome Headless Shell its `playwright-core`'s `browsers.json` names (the
  revision, the version and the sha256 of each architecture's archive).
  Today: 1.63.0 (revision 1243, Chrome 153.0.8010.12) and 1.64.1 (revision
  1248, Chrome 156.0.8078.4, the default). The Dockerfile's `ARG` defaults
  build the default release (a test keeps them equal), and
  `PLAYWRIGHT_LOCK_DIR` selects the lockfile.
- **Built per release, recorded by digest.** The version job of `images.yml`
  runs `playwright_matrix.py`, which checks every value's shape (an exact
  version, a lockfile folder next to the list that pins that version, digits,
  a browser version, sha256 values) and prints the matrix; `publish-browser`
  builds each release with its values passed through the environment as
  build arguments (never pasted into a script), tags it
  `X.Y.Z-playwright-<release>` (the default also `X.Y.Z` and `latest`),
  signs and attests it by the digest buildx reported, and keeps a
  `.digest.json` record beside its SBOM. Only the amd64 sha256 is passed, as
  only `linux/amd64` is published. Nothing changed about when it runs: a
  `v*` tag, as before.
- **The version the project pins is the daemon's observation.**
  `vscode-daemon/playwright_pin.py` reads `package.json` and the first
  lockfile that names the package (`package-lock.json`,
  `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lock`), with the
  standard library, at the project's root, never through a symlink and never
  past 16 MiB, and reports only a value that is a version.
  `workspace.inspect`'s snapshot carries it. The backend asks for a fresh
  snapshot right before the application check, after the install (which may
  have written the lockfile), and never reads the version from the
  producer's report.
- **Exact, fallback, unknown.** `services/workspace/browser_images.py`
  reads `DELIVERY_BROWSER_IMAGES` (each image by digest; a tag is refused and
  recorded) and chooses: the release's image; a release not listed runs in
  `DELIVERY_BROWSER_IMAGE` with the mismatch on the evidence
  (`app_run.browser_image.mismatch`); a project with no Playwright runs the
  image's own; a version nobody could tell is `unknown`, and the loop starts
  the application without the browser suite and records the suite as not
  run with the reason as `runtime_missing`, which the Verifier's `e2e_run`
  check already reads as unverifiable. The evidence code is unchanged.
- **Without the list, nothing changes.** `DELIVERY_BROWSER_IMAGES` empty keeps
  the single `DELIVERY_BROWSER_IMAGE`, and no extra inspection is made.

The 1.63.0 values were read from `playwright-core@1.63.0`'s `browsers.json`
on the npm registry; both archives were downloaded and hashed, and each
archive's MD5 matched the `x-goog-hash` the origin bucket
(`storage.googleapis.com/chrome-for-testing-public/153.0.8010.12/…`) stores
for it (`mo62E1sc2lbej1R3dikeNQ==` for `linux64`,
`iryuNu+qLajWde8Piw6NGw==` for `linux-arm64`), behind the session's
TLS-intercepting proxy as in #529. The 1.63.0 lockfile was made with `npm
install --package-lock-only --ignore-scripts`. The 1.63.0 image itself was not
built here (no Docker daemon in the session): its build-time probe on the
first `v*` tag is the first time its browser launches, and a failure there
fails that release's job alone (`fail-fast: false`). 1.62.1 was left out
because its arm64 archive is not on the chrome-for-testing bucket; 1.65.x
was less than two weeks old.

**Adding a release:** read `revision` and `browserVersion` of
`chromium-headless-shell` from that release's `playwright-core`
`browsers.json`; download both archives from
`cdn.playwright.dev/builds/cft/<version>/{linux64,linux-arm64}/` and hash
them, checking each against the origin's `x-goog-hash`; write
`playwright/<release>/package.json` pinning `@playwright/test` exactly and
its lockfile with `npm install --package-lock-only --ignore-scripts`; add the
entry. `test_browser_images_playwright.py` and `playwright_matrix.py` check
it. Moving the default also moves the Dockerfile's `ARG` defaults and the
image's own `package.json` and lockfile.

Taken as design: GitHub Actions' matrix from a JSON output
(`fromJSON(needs.<job>.outputs.<name>)`), and Microsoft's own Playwright
images, which are published one tag per release (`v1.64.1-noble`) for the
same reason. No code was copied. The threat model is in the pull request.
