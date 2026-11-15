# Quality standards

What La Fabrique holds every project to, by discipline. The catalogue lives in
`backend/app/services/evidence/standards.py` and is the same for every company: no
model, no request and no procedure edits it. The standards go into every
prompt by role (the PO's draft, the Architect's design, each task, QA's
review), they bind before any task-specific instruction, and a company
procedure adds to them, never relaxes them.

Where a standard can be checked without a model, it is, and the standard says
so: the Verifier records a `standards` check over the files a deliverable
declares (a secret refutes the delivery; code without a test, a `TODO` left
behind and an unpinned dependency are findings a reviewer sees), the
specification check warns on a quality area left out, and the checks the loop
already runs (the risk tier, the acceptance tests per criterion, the
architecture's cycles, the audit chain, the history check) are named where
they apply. `GET /v1/standards` serves the catalogue; the Procedures page
shows it.

The specialised developer roles (`BackendDeveloper`, `FrontendDeveloper`,
`DevOps`, `DataEngineer`) are developers: every standard below that binds the
Developer binds them too, beside the ones of their own discipline
(`standards.DISCIPLINES`). The Designer is bound by the standards for every
role and by the UX standard. No role is ever held to less than it was.

A test (`backend/tests/test_standards.py`) keeps this document in step with
the catalogue: every standard below is in the code, and every standard in the
code is below.


## Architecture

**STD-ARCH-1** — Every decision is a recorded ADR  
*Binds:* Architect, Developer.  
A decision a developer would otherwise take alone (storage, framework, boundaries, error handling, authentication) is written as an ADR with its context, the decision and its consequences, before code depends on it.  
*Checked:* the architecture check warns when no decision is recorded.

**STD-ARCH-2** — Modules with one responsibility and explicit interfaces  
*Binds:* Architect, Developer.  
Each module is responsible for one thing and says what it never does; modules talk through named interfaces with a contract a test can hold them to; no dependency runs in a circle.  
*Checked:* the architecture check blocks a dependency cycle and a module without a responsibility.

**STD-ARCH-3** — Work inside the module boundaries  
*Binds:* Developer.  
A task changes the module its requirements map to, and its tests and documentation; a change to another module or to an interface is a decision for the Architect, raised as a question, not made in passing.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.


## Development

**STD-DEV-1** — No code without a test  
*Binds:* Developer.  
Every code file delivered comes with a test that exercises it, in the project's test framework, deterministic and runnable in CI; a bug fix comes with the test that would have caught it.  
*Checked:* a delivery with a code file and no test file is a finding.

**STD-DEV-2** — Nothing half done  
*Binds:* Developer, QA.  
A deliverable has no TODO, FIXME or placeholder left in it; what cannot be done in the task is said in the summary and raised as a question.  
*Checked:* a TODO, FIXME, XXX or HACK left in a delivered file is a finding.

**STD-DEV-3** — Small, named, typed  
*Binds:* Developer.  
Identifiers in English, functions that fit on a screen, types where the language has them, errors handled where they can be and reported with what to do; no dead code, no commented-out code.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.


## QA

**STD-QA-1** — One acceptance test per criterion, written first  
*Binds:* QA, Developer.  
Every acceptance criterion of the specification has a test named after its id, written before the implementation and failing until it exists; the criterion is done when that test passes in a run the factory observed.  
*Checked:* the criteria_tests check, and acceptance_tests_untouched when a test is rewritten.

**STD-QA-2** — Review against the criteria, not the prose  
*Binds:* QA.  
QA judges a deliverable against the acceptance criteria it covers, the architecture and these standards; an approval names what was checked, a rework request names what is missing and where.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.

**STD-QA-3** — Tests are deterministic and isolated  
*Binds:* QA, Developer.  
A test does not depend on the clock, the network, the order of other tests or shared mutable state; a flaky test is a bug, never retried into green.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.


## Security

**STD-SEC-1** — No secret in the repository, ever  
*Binds:* every role.  
Keys, tokens, passwords and private keys live in the environment or a secret store, never in a file the repository tracks; configuration reads them by name.  
*Checked:* a secret-looking value in a delivered file refutes the delivery.

**STD-SEC-2** — Validate at the boundary, least privilege inside  
*Binds:* Developer, QA.  
Every input from outside (a request, a file, an environment variable, a model's answer) is validated and typed at the boundary; a component gets the permissions it needs and no more; authorization is checked on every route against the owner of the resource.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.

**STD-SEC-3** — Dependencies pinned and reviewed  
*Binds:* Developer.  
A dependency is added with a pinned version and a lock file, from a registry the project already uses; a change to authentication, secrets, payments or the pipeline waits for a person.  
*Checked:* an unpinned dependency in a manifest is a finding; the risk tier holds a high-risk delivery for a person.


## Evidence and audit

**STD-AUD-1** — Claims and observations never share a field  
*Binds:* every role.  
What an agent says it did is a claim; what the daemon, the tests and the checks saw is an observation. A summary lists the files written and the tests run as claims, so the factory can hold them to what it observed.  
*Checked:* every delivery is verified against the daemon's report; the audit chain links every row.

**STD-AUD-2** — Decisions are recorded with their reason  
*Binds:* ScrumMaster, QA.  
An approval, a rework request, a triage, a procedure adopted or reverted carries who decided and why, in a fixed format a parser checks; a decision without a reason is not one.  
*Checked:* the history check refuses a task done without an approval or a person.


## Documentation

**STD-DOC-1** — A change to behaviour documents itself  
*Binds:* Developer, Architect.  
A new or changed public interface, command, configuration variable or user-facing behaviour updates the documentation in the same delivery (README, the API reference, the changelog); the architecture document follows the approved architecture.  
*Checked:* the approved architecture is written to docs/ARCHITECTURE.md with the daemon's hash.

**STD-DOC-2** — Code explains why, not what  
*Binds:* Developer.  
A module starts with what it is for and what it never does; a comment says why something is done the way it is, never restates the code.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.


## Operations

**STD-OPS-1** — Configured by environment, observable by default  
*Binds:* Developer, Architect, DevOps.  
Configuration comes from the environment with safe defaults; a service exposes a health check, logs in a structured form with no secret in it, and fails loudly rather than silently.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.


**STD-OPS-2** — Pipelines and infrastructure are reviewed code  
*Binds:* DevOps, Architect.  
CI pipelines, container files and infrastructure are code in the repository: pinned images and actions, least-privilege credentials taken from the environment, every step reproducible locally, and a rollback written down before the change ships.  
*Checked:* the risk tier holds a CI pipeline change for a person (high) and marks container files medium.


## Data

**STD-DATA-1** — Schema changes are migrations, reversible and tested  
*Binds:* DataEngineer, BackendDeveloper, Architect.  
A change to a schema is a versioned migration with its downgrade, written so it runs on a table that already holds rows (a default or a backfill for a required column), with a test that applies it; data is never changed by hand outside a migration.  
*Checked:* the risk tier marks a migration medium.


## UX

**STD-UX-1** — Accessible by default  
*Binds:* Designer, FrontendDeveloper.  
Every screen meets WCAG 2.2 AA: it works with the keyboard alone, names every control for a screen reader, keeps text contrast at 4.5:1 or more, never says something with colour alone, and holds at a phone's width; every user-facing string is translatable.  
*Checked:* in the prompt alone; QA and the reviewers hold the deliverable to it.


## Product

**STD-PO-1** — A specification names its quality attributes with a number  
*Binds:* PO.  
Beside the requirements, a specification says what it expects of security, performance, reliability and accessibility, each with a target a test can measure; a promise without a number is not a target.  
*Checked:* the specification check warns on a quality area left out and on a target without a number.  
*Security target:* the project's security test plan (OWASP WSTG, issue #539) gives the security area a number a test can measure: the share of the WSTG tests that apply to the project observed passing. The documentation pack's check results report it (Risk, `measured`) beside the specification's own target; the Owner sets the threshold, the factory never does.

