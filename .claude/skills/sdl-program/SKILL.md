---
name: sdl-program
description: Run La Fabrique's periodic security programme review, based on Microsoft SDL practices 1 (standards, metrics and governance), 9 (monitoring and response) and 10 (training) - bring SECURITY.md, the security baseline, the bug bar, the exception register, the security metrics (open findings, time to fix, dependency age), the incident response plan and the training notes up to date, and open issues for the gaps. Use it quarterly, before cutting a release (docs/OPERATIONS.md, Releasing), after a security incident, or when asked whether the project's security documentation is current. Not for reviewing a single change; that is sdl-review.
---

# SDL programme review

The per-change skills (`sdl-review`, `threat-model`) only work if the
standards they check against are current. This skill keeps those standards,
the numbers that show whether they are met, the response plan and the
training notes up to date. It follows Microsoft SDL practices 1, 9 and 10;
the sources and the repository mapping are in
[`../sdl-review/reference.md`](../sdl-review/reference.md).

**When:** once a quarter; before a release tag (step 1 of
`docs/OPERATIONS.md`, Releasing, is a good moment, so the release notes can
name security changes); after any incident; when a reviewer finds the
documents disagree with the code.

**Yearly:** the person-led penetration test of `SECURITY.md` §16 (business
logic, cross-tenant SSO, NATS and the daemon, Slack and Teams spoofing, prompt
injection through agent output, the deployed TLS), reported in the OWASP WSTG
chapter-5 structure. In the quarterly review, check its date: more than a year
since the last report, or a release that changed authentication, roles or the
daemon since, is a gap to file. Its findings are filed as issues and each fix
ships with a test (practice 7, `../sdl-review/reference.md`).

**Output:** one pull request with the document updates and a CHANGELOG line,
plus one issue per gap that cannot be closed in that PR. Every number in the
PR states where it came from and when it was read; a number with no source is
a claim and is marked so.

## 1. Collect the numbers (practice 1: metrics)

Read, do not estimate. Record each with its command and date.

| Metric | How to read it |
|---|---|
| Open Dependabot alerts by severity and surface (shipped vs build-only) | `gh api repos/{owner}/{repo}/dependabot/alerts --paginate -q '[.[] \| select(.state=="open")] \| group_by(.security_advisory.severity) \| map({sev: .[0].security_advisory.severity, n: length})'`; for the shipped surface, `cd frontend && npm audit --omit=dev` |
| Open code-scanning alerts (Trivy SARIF) | `gh api repos/{owner}/{repo}/code-scanning/alerts -q '[.[] \| select(.state=="open")] \| length'` |
| Last weekly baseline result | `gh run list --workflow security-baseline.yml --limit 4`; read the failing job's findings |
| Open security issues, and age of the oldest per severity | `gh issue list --label security --state open --json number,title,createdAt,labels` (create the `security` label and severity labels if they do not exist) |
| Time to fix | For security issues closed this period: closed minus created, median and worst, against the bug bar below |
| Dependency age | `cd backend && poetry show --outdated`; `cd frontend && npm outdated`; note majors held back by `.github/dependabot.yml` `ignore` and whether each reason still holds |
| Actions not pinned by sha | `grep -rn "uses: " .github/workflows \| grep -v "@[0-9a-f]\{40\}"` |
| Audit chain health | `GET /v1/audit/chain` on a staging deployment: no `hash_mismatch` other than a documented key rotation |

Compare with the previous review (the last `SECURITY.md` §5.4 snapshot or the
previous programme PR). A rising number is reported, not hidden: the SDL is
explicit that full compliance at all times is not realistic, and that visible
risk is better than hidden risk.

## 2. Standards and governance (practice 1)

Check and update `SECURITY.md`:

- [ ] Header: "Last reviewed" carries today's date and the reviewer's role (not
      a name).
- [ ] Every control described is either implemented (cite the file) or marked
      as a target. Known drift to check each time: §3 (OIDC/PKCE, mTLS,
      15-minute tokens) against the HS256 JWT in `docs/contexts/platform.md`;
      §8.3 (written before `vscode-daemon/sandbox.py` and
      `command_policy.py` existed); §5.4 alert counts.
- [ ] The reporting contact in §9.1 and §14 is real, or the placeholder is
      replaced by the repository's private vulnerability reporting
      (GitHub Security Advisories), and the PR says which.
- [ ] **Bug bar.** §9.2 maps severity to a response time. Confirm it is still
      the rule, that triage uses it, and that it has not been loosened; a
      change to it is an owner decision recorded in the PR.
- [ ] **Exception register.** Every accepted risk (a Dependabot `ignore`, a
      compensating control such as `safeReturnPath()`, a non-gating scan like
      `safety check || true`) has: why, the interim mitigation, the plan to
      remove it, an expiry date and who accepted it. Keep the register as a
      table in `SECURITY.md` (a new section) and renew or close every expired
      entry; an expired exception goes back to the owner.
- [ ] **Baseline.** The minimum the pipeline enforces (what is gating on a PR,
      what only runs weekly) is written down and matches the workflows
      (`security-scan.yml`, `security-baseline.yml`, `backend-tests.yml`).
- [ ] **Roles.** Who accepts risk (the repository owner), who meets the
      standard (every contributor and every agent, via `CLAUDE.md` and
      `AGENTS.md`), who maintains the programme. Use roles, not names.
- [ ] The standards still cite their frameworks (OWASP ASVS, OWASP Top 10 for
      LLM Applications, NIST SSDF) and the SDL practices.

## 3. Monitoring and response (practice 9)

- [ ] **Logs.** List what is logged for security events (logins and failures,
      token create and revoke, role changes, refused commands, sandbox mode
      changes, chain verification failures) and where. Confirm none carries a
      secret (spot-check recent logs and `/health`).
- [ ] **Signals.** `/metrics` and `/health/deep` (`docs/OPERATIONS.md`) cover
      availability. Note which security signals have no metric or alert, and
      open an issue for the most useful one rather than all at once.
- [ ] **Audit evidence.** The chain verifies; retention (`docs/OPERATIONS.md`,
      Retention) and chain archiving still match what operators do; the
      archived `AUDIT_SIGNING_KEY` history is where the runbook says.
- [ ] **Incident response plan.** SECURITY.md §9 names: contacts that work,
      first actions per incident type (a leaked key → `docs/OPERATIONS.md`,
      Rotate keys; a compromised daemon machine; a malicious dependency; an
      agent that ran harmful code), and how fixes ship (a release per
      `docs/OPERATIONS.md`, Releasing, including for third-party and adapted
      code listed in `NOTICE`).
- [ ] **Rehearsal.** Once a year at least, walk one scenario through the plan
      on a staging deployment (for example, rotate `JWT_SECRET_KEY` and
      `NATS_DAEMON_PASSWORD` as if leaked) and record the date, what failed
      and the follow-up issues.

## 4. Training (practice 10)

The project's contributors are people and agents; both need the "why, what and
how" for their role.

- [ ] **Agents.** `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`
      and SECURITY.md §11 state the rules agents follow, and they agree with
      each other and with `docs/POSITIONING.md` (claims vs observations,
      rule 6). Fix drift between them.
- [ ] **People.** A short onboarding section (in `CONTRIBUTING.md` or a
      linked page) points a new contributor to: SECURITY.md §10 and §11, the
      design rules in `docs/POSITIONING.md`, the `threat-model` and
      `sdl-review` skills, and one real past incident or near miss from this
      repository with what it taught (for example the `decision_signature`
      hash taken from the request body, issue #39). Update it when an incident
      teaches something new.
- [ ] **Practice.** Record which features this period got a threat model
      (`docs/design/`, PR links); threat modeling is a skill learned by doing
      it, and the count shows whether it is happening.

## 5. File the results

1. One branch from `main`, one PR: `SECURITY.md` updates, the metrics table
   with sources, the exception register, the onboarding note, a line under
   `[Unreleased]` in `CHANGELOG.md`.
2. One issue per gap that needs code or an owner decision, labelled
   `security` with a severity from §9.2.
3. In the PR description, the coverage: which of the sections above were
   done, which were skipped and why.

These are model-produced updates to the project's security documentation; a
person who owns the repository reviews and accepts them, and owns every risk
acceptance in the register.

Sources: Microsoft SDL practices 1 (*Establish security standards, metrics,
and governance*,
https://www.microsoft.com/securityengineering/sdl/practices/security-program-management),
9 (*Implement security monitoring and response*,
https://www.microsoft.com/securityengineering/sdl/practices/monitoring-and-response)
and 10 (*Provide security training*,
https://www.microsoft.com/securityengineering/sdl/practices/security-training).
The procedure and wording are this project's own.
