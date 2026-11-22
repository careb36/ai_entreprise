---
name: ciberseguridad
description: Secure development for any change: the OWASP Top 10 as concrete rules, threat modelling with OWASP pytm, bandit, pip-audit, npm audit and Trivy or their equivalents, input and path validation, network, secrets, outputs, LLM calls and evidence. Use it when adding or changing inputs, files, network access, dependencies, authentication, APIs, prompts or LLM calls, containers or deployment.
---

# Cybersecurity and OWASP

Use this skill when adding or changing inputs, files, network access,
dependencies, authentication, APIs, prompts or LLM calls, containers or
deployment. The gates are run and their results reviewed; they are never
silenced.

In La Fabrique this skill sits beside the Microsoft SDL skills next to it:
`threat-model` for the threat model of a new design, `sdl-review` for reviewing
a change before it lands, and `sdl-program` for the periodic review. This one
adds the OWASP rules and the gates per stack; a review's report follows the
format of `sdl-review`.

## 1. Fit it to the project

Before running anything, identify the stack and the real gates:

```bash
git rev-parse --show-toplevel
ls pyproject.toml package.json Makefile Dockerfile 2>/dev/null
grep -nE '^(security|threat-model|test|check):' Makefile 2>/dev/null
```

- La Fabrique has no `Makefile`: the gate is `scripts/check.sh full` (what CI
  runs), and `scripts/land.sh` runs it before landing. Semgrep and gitleaks run
  weekly in `security-baseline.yml`; for a sensitive change, run them by hand
  (the commands are in `sdl-review/reference.md`).
- A design's threat model goes in `docs/design/` (the `threat-model` skill);
  triage and exceptions go in `SECURITY.md`.
- In another project: use `make threat-model` or `make security` when they
  exist; otherwise the equivalents in section 2, recording the exact command.

## 2. Minimum gates

### Python

```bash
python -m bandit -r . -q
python -m pip_audit
```

When the project has a pytm model:

```bash
python scripts/threat_model.py   # or make threat-model
```

### Node / TypeScript

```bash
npm audit --omit=dev
npm run lint                    # with security rules where they exist
```

### Containers and images

```bash
trivy image <image>             # when available
```

### Every stack

```bash
# the project's tests, build and checks
scripts/check.sh full   # La Fabrique
# elsewhere: make check / make test / npm test / pytest
```

`bandit` and `npm audit` end clean or with every finding triaged. `pip-audit`
and `npm audit` may report transitive dependencies with no compatible fixed
version: record the residual risk, the compensating control and a review date.
Never use `|| true` to hide the result.

## 3. OWASP pytm (threat modelling)

pytm is OWASP's Python tool for STRIDE threat models built from actors, trust
boundaries, processes, data stores and flows.

1. Install it in the development environment:
   ```bash
   pip install pytm
   ```
2. Model the system in a versioned script (`scripts/threat_model.py`,
   `security/threat_model.py`, or whichever already exists).
3. Generate the model and review the diff:
   ```bash
   python scripts/threat_model.py
   git diff -- docs/threat_model.md
   ```
4. Classify **every new threat** in the triage as **Applicable**, **Partial**
   or **Not applicable**, with its mitigation or the reason.

pytm is generic: buffer overflows, SSI, LDAP, hardware and other threats may
not apply. The value is in the explicit triage, not in the total. Threats are
never deleted from the model to "clean up" the result.

Update the model when adding processes, endpoints, data stores, trust
boundaries, outbound internet access or sensitive data. Map LLM threats to the
OWASP Top 10 for LLM Applications.

## 4. OWASP Top 10 as concrete rules

| Risk | Rule |
|---|---|
| A01 Broken access control | Authorisation on the server, not in the UI; IDs and paths validated; least privilege; local-only APIs by default. |
| A02 Cryptographic failures | Verified TLS; SHA-256 for integrity; never secrets in clear; no `verify=False` without a recorded exception. |
| A03 Injection | No `shell=True`, `eval`/`exec` or concatenated SQL; inputs validated and escaped; parameterised queries. |
| A04 Insecure design | A threat model and its triage before a flow is exposed; limits, timeouts and gates by design. |
| A05 Security misconfiguration | CSP, `nosniff`, `frame-ancestors`, no debug in production, local bind by default, least permissions. |
| A06 Vulnerable components | Pinned dependencies (constraints, lockfile); audited; optional extras; no needless weight. |
| A07 Authentication failures | Passwords and API keys hashed securely; rate limits; session and Host validated; an operator's name is not authentication. |
| A08 Integrity failures | Hashes of corpora and artefacts, literal citations, an append-only audit log, verified downloads. |
| A09 Logging and monitoring failures | Logs without secrets or needless personal data; persistent audit; internal errors only in the local log. |
| A10 SSRF | A domain allowlist, TLS, timeouts, a size limit, validated redirects; no arbitrary URLs accepted. |

## 5. Required patterns

### Inputs and paths

- Validate type, length, range and format at the trust boundary.
- Join paths with a safe helper (`safe_join_path` or equivalent) and validate
  names; never `os.path.join(base, user_data)`.
- Atomic writes (a temporary file, then `os.replace`); temporary files through
  `tempfile`.
- Never rely on validation done only in the UI.

### Commands and processes

- `subprocess.run([...], shell=False, check=True, timeout=...)`.
- No `eval`, `exec`, `pickle` of untrusted data, or unsafe `yaml.load`.
- Never interpolate input into a command line.

### Network

- Every URL goes through a scheme and host allowlist; no private IPs, no
  embedded credentials.
- Verified TLS, timeouts, a byte limit and a check of the file type.
- Never accept an arbitrary URL from a user to download or open.
- External integrations (LLMs, APIs, webhooks) sit behind an explicit
  configuration gate and send no sensitive data they do not need.

### Outputs

- Escape for the context (HTML, attributes, JS, JSON); no `innerHTML` with
  unescaped data.
- Generic errors to the client; the detail only in the local log or audit.
- No secrets, tokens, raw prompts or personal data in reports.

### Secrets

- Never in code, tests, logs, screenshots, a versioned `.env` or git.
- Environment variables or a secret manager; `.env.example` without real
  values.
- A key exposed is rotated, and the history is reviewed.

### Dependencies

- Versions pinned in a lockfile or constraints; optional extras.
- Audit; upgrade the chain when a compatible version exists.
- Without a compatible version, record the residual risk and the mitigation;
  never silence the tool.

### LLM

- The model proposes; a person approves. Give it no tool and run none of its
  output unless the project's design bounds and records it (in La Fabrique:
  the sandbox, the command policy, the Verifier and the evidence rules in
  `docs/POSITIONING.md`).
- Validate the output JSON; delimit untrusted content (prompt injection);
  redact sensitive data from the prompt.
- Record the provider, the model and the gate, never a prompt with personal
  data.

## 6. Red flags (do not approve)

- `shell=True`, `os.system`, `eval`, `exec`, unsafe `pickle` or `yaml.load`.
- A user's URL without an allowlist; `verify=False`; downloads without a limit.
- Paths or commands concatenated with user data.
- Secrets or tokens in the diff, the logs or the tests.
- `innerHTML` with unescaped data; invented citations or evidence.
- Automatic approvals, or an LLM with write access outside a bounded and
  recorded design.
- `make security`, `pip-audit` or `npm audit` silenced, or threats deleted
  from the model.

## 7. Definition of done

- [ ] The stack and commands identified; the gates run.
- [ ] The pytm model updated and its diff reviewed (when it applies).
- [ ] New threats triaged; no *Applicable* High or Critical threat without a
      mitigation, a test or an ADR.
- [ ] `bandit` and `npm audit` clean or triaged; `pip-audit` with its residual
      risk recorded.
- [ ] Tests, build and `scripts/check.sh full` (or `make check`) green.
- [ ] No secrets or needless personal data in the diff.
- [ ] New paths, network access, files, authentication or LLM calls come with
      a control and a test.
- [ ] The evidence (model, triage, tests) versioned with the change.

## 8. Reporting findings

Give the severity, the file and line, the exploitation path, the data
affected, the mitigation, the test that covers it, and the residual risk with
a review date. Include no secrets, tokens or personal data.

## 9. Limits

pytm models the design and does not run the system; bandit and eslint are
static analysis; pip-audit and npm audit look at the installed environment,
not the real deployment. No tool replaces a manual review or a penetration
test. An accepted risk is written down and signed by whoever decides it.
