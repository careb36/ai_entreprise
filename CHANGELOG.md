# Contributing to La Fabrique

This file is the **single authoritative source** for how work enters La Fabrique:
branch naming, pull request flow, review requirements, merge policy, versioning,
releases, and hotfixes. It applies equally to human contributors and to AI agents
operating in this repository.

- **Do not restate these rules elsewhere.** Link to them: `CONTRIBUTING.md#<anchor>`.
- If a tool configuration (`.github/workflows/`, `CODEOWNERS`, `commitlint.config.js`,
  branch protection) disagrees with this document, **the tool is wrong**. Fix the tool
  in the same pull request and note it in the PR description.
- Changes to this document require **two approvals: DevOps and the Product Owner**.

---

## Quick reference

| I want to… | Do this |
|---|---|
| Start work | Branch from up-to-date `main`, named per [§2.2](#22-short-lived-branch-naming) |
| Land work | Open a PR against `main` using the [PR template](.github/PULL_REQUEST_TEMPLATE.md) |
| Merge | Get approvals per [§4.4](#44-review-requirements), pass all checks, enter the merge queue |
| Ship a release | Follow [§7](#7-release-process) and the [release checklist](#74-release-checklist) |
| Fix production | Follow [§8](#8-hotfix-process) |
| Know if my change is breaking | Check the [public surface table](#62-public-surface) |
| Contribute as an agent | Read [§5](#5-agent-contributors) first |

---

## 1. Ground rules

1. `main` is **always releasable**. A red `main` is a P1 incident, not a backlog item.
2. Every change arrives by pull request. There are no direct pushes to protected branches,
   for anyone, ever — including release managers.
3. Every PR is traceable to a ticket and to a GlassRoom run (human or agent). Work with
   no provenance is not reviewable and will be closed.
4. Prefer the smallest change that delivers the increment. Stack large work into
   reviewable PRs rather than one 2,000-line diff.
5. If you cannot explain the rollback in one sentence, the change is too big.

### Service levels

| Event | Target |
|---|---|
| First review on a PR | 1 business day |
| Re-review after changes | 4 business hours |
| Hotfix acknowledgement (business hours) | 1 hour |
| Agent-authored PR triaged by a human | 1 business day |

---

## 2. Branching model

La Fabrique uses **trunk-based development**. `main` is the only long-lived development
branch. There is no `develop` branch and there will not be one: a second integration
point doubles the merge surface and makes agent work non-deterministic.

### 2.1 Long-lived branches

| Branch | Purpose | Who may merge | Protected |
|---|---|---|---|
| `main` | Trunk. Always releasable. All features land here. | Via merge queue only | Yes |
| `release/<major>.<minor>` | Release stabilization and patch maintenance for a shipped minor line. Cut from `main` at RC. | Release manager only | Yes |

`release/*` branches accept **only** `hotfix/*` and `release/*` PRs. Features never land
on a release branch; they go to `main` and ship in the next minor.

### 2.2 Short-lived branch naming