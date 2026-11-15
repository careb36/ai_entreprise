# Proposal: keep the self-hosted runner for trusted code only, and move it to ephemeral containers on a host of its own

## Status

A design proposal, not an ADR: `docs/adr/` holds only the decisions rendered from `docs/architecture/architecture.json` (#329). Proposed (2026-11-15), issue #522 part C. Records the question and a recommendation; the runner's configuration is not changed by the pull request that adds this record. The owner decides and carries it out.

## Context

Every workflow in `.github/workflows/` runs on the self-hosted runners labelled `lafabrique` (seven instances on one machine) (`runs-on: [self-hosted, linux, x64, lafabrique]`), because GitHub-hosted runners are blocked by the account's spending limit (`e2e.yml`, `release.yml`). Only the SLSA generator's own reusable workflow runs on `ubuntu-latest`. What is known about that runner:

- **It is not ephemeral.** It keeps its state between jobs: `~/.npm`, Docker images and containers, the Docker daemon's login state unless a job isolates it (`images.yml` uses a job-local `DOCKER_CONFIG` for that reason), Poetry and uv caches, and anything a job leaves in its home directory (`docs/TESTING.md`, CI). A job that is compromised can leave a file, a cached package, a Git hook in a cached checkout or a tampered Docker image that a later job, a release job included, then uses.
- **It shares its machine with the workspace daemon** (`.github/CODEOWNERS` header; SECURITY.md §5.3). The daemon is the component that writes the user's files and runs commands for the agents; on that machine it has the user's projects and the NATS credentials of a daemon. A job that reaches the host reaches those.
- **It runs pull requests.** `backend-tests.yml`, `frontend-build.yml`, `docker-build.yml`, `e2e.yml`, `file-sizes.yml`, `security-scan.yml` and `workflows.yml` run on `pull_request`, so the code of every pull request (its tests, its `package.json` scripts, its Dockerfiles) runs on this runner, as does any branch a bot or an automated agent proposes. If the repository is public, or becomes so, a pull request from any fork does the same; GitHub's default asks for approval only for first-time contributors, so one approval makes later runs automatic. No workflow uses `pull_request_target`, so a fork's run gets a read-only token and no secrets; the runner's host is still exposed.
- **It holds publishing rights while a release runs.** On a tag, `release.yml` has `contents: write` and `images.yml` has `packages: write` and, since part C, `id-token: write` for keyless cosign signing. A persistent compromise from an earlier job can tamper with what those jobs build, sign and publish: the signature would then vouch for a tampered image, because it proves which workflow signed, not what the host looked like.

GitHub's own guidance is that self-hosted runners should almost never be used for public repositories, because anyone can open a pull request and run code on them, and that an ephemeral runner (one job, then discarded) limits what a compromised job can leave behind. The repository's visibility was not checked for this record.

## Options

1. **Keep it as it is.** No cost. Every risk above stays; the Code Owner review (#182) covers changes to workflows, but not code a pull request runs through the existing workflows (a test, a build script).
2. **Ephemeral runners on the same host.** Register the runner with `--ephemeral` inside a container started per job (a small supervisor loop, or actions-runner-controller on a local Kubernetes such as k3s), from an image rebuilt on a schedule. Removes the state carried between jobs. The host is shared with the daemon still, and a container escape or a mounted Docker socket (the e2e and image jobs need Docker: rootless Docker or a Docker-in-Docker sidecar, never the host's socket) reaches it.
3. **GitHub-hosted runners for untrusted pull requests.** Run code from forks, or from branches a bot proposed, where nothing persists and nothing of the owner's is near: a `runs-on` chosen by `github.event.pull_request.head.repo.fork` or by the branch prefix. Needs hosted minutes, which the spending limit blocks today (`e2e.yml`); standard hosted runners are free for public repositories.
4. **A separate host for the runner.** A machine or VM that has no daemon, no user projects and no NATS credentials. Removes the shared-host risk; combined with option 2 it also removes persistence.
5. **Require approval for every outside run.** Settings > Actions > General > "Require approval for all outside collaborators". Cheap and immediate; depends on a person reading each pull request's diff before approving, every time.

## Decision (recommended)

In order, each step useful alone:

1. Now: option 5 (approval for all outside collaborators), and never add `pull_request_target` or `workflow_run` on this runner.
2. Next: option 4, a host or VM of its own with no daemon and no user data, so the daemon's machine is out of reach of CI.
3. Then: option 2 on that host, an ephemeral runner per job in a container, with Docker provided rootless or by a sidecar rather than the host socket, and the release workflows (`release.yml`, `images.yml`) on a runner group of their own that pull requests cannot use.
4. When the account allows it: option 3 for pull requests from forks.

## Consequences

Until step 2, a pull request that runs code on the runner can reach the daemon's machine, and until step 3 a compromised job can persist into a release job, whose keyless signature would then vouch for what it built. The signatures and SBOMs added in part C still help: they tie each image to the workflow and tag that produced it and record what it contains, which is what an investigation needs, but they are not evidence that the runner was clean. Steps 2 and 3 change where the e2e's NATS container and the image builds run; `docs/TESTING.md` (CI) and `docs/OPERATIONS.md` follow when they are carried out.
