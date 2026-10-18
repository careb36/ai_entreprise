# SECURITY.md

## 1. Introduction and Scope

This document defines the security policy, threat model, and security controls for the **multi-agent execution platform**.

The platform consists of:

- **React frontend** for user and tenant administration.
- **FastAPI APIs** for edge services, authentication, and control plane operations.
- **LangGraph workflow runtime** for agent orchestration, tool calling, and human-in-the-loop interrupts.
- **Neo4j** graph database for agent/workflow state and relationship data.
- **PostgreSQL** for relational tenant, user, audit, and configuration data.
- **Kubernetes** as the deployment and runtime environment.

This policy applies to all source code, infrastructure, CI/CD pipelines, containers, and operational practices. All team members, contractors, and operators must follow this policy.

The security checklist in [Section 7](#7-security-checklist) is the canonical pre-merge and pre-deployment checklist. It must be linked from `CONTRIBUTING.md` and the CI documentation. This document has been reviewed against the current Sprint 0 infrastructure: FastAPI edge, LangGraph runtime, React SPA, Neo4j/PostgreSQL data stores, and Kubernetes deployment paths.

---

## 2. Security Principles

1. **Deny by default.** Every request, tool call, workflow node, and network path must explicitly grant access.
2. **Least privilege.** Services and users receive only the minimum permissions required.
3. **Defense in depth.** No single control is sufficient. Assume any layer can fail.
4. **Zero trust.** Authenticate and authorize every request, even inside the cluster.
5. **Secure defaults.** The easiest path must be the secure path.
6. **Treat all model input and tool output as untrusted.** Prompt injection and malicious tool responses are expected failure modes.
7. **Keep secrets out of code, prompts, logs, and artifacts.**
8. **Audit everything.** Security-relevant events must be logged, monitored, and retained.

---

## 3. Platform Security Architecture and Trust Boundaries

The platform is divided into trust zones:

| Trust Zone | Components | Trust Level |
|------------|------------|-------------|
| Public internet / untrusted clients | Browser users, API consumers | Untrusted |
| Edge / API layer | FastAPI services, Ingress, WAF | Semi-trusted |
| Application runtime | LangGraph workers, tool execution pods, backend services | Semi-trusted |
| Data stores | PostgreSQL, Neo4j | Trusted |
| Kubernetes control plane and nodes | kube-apiserver, etcd, kubelet, container runtime | Trusted |
| CI/CD pipeline | Git repository, builders, image registry, deployment automation | Trusted |
| External services | OIDC provider, LLM providers, external tools/APIs | Semi-trusted / untrusted |

Data flows across these boundaries through:

- Browser to FastAPI over TLS 1.2+.
- FastAPI to LangGraph workers over internal cluster network with service identity.
- LangGraph workers to Neo4j/PostgreSQL with scoped credentials.
- LangGraph workers to external LLM/tool providers over TLS.
- CI/CD to Kubernetes registry and cluster with short-lived credentials.

---

## 4. Threat Model

### 4.1 Assets

| Asset | Sensitivity | Impact if Compromised |
|-------|-------------|------------------------|
| User PII and tenant data | High | Legal, reputational, trust |
| Prompt and tool outputs | High | Data exfiltration, prompt injection |
| Workflow and agent definitions | High | Unauthorized actions, data access |
| API keys, OAuth tokens, service secrets | Critical | Full platform compromise |
| Model endpoints and provider credentials | High | Cost abuse, data leakage |
| Source code and CI/CD pipeline | High | Supply chain compromise |
| Neo4j and PostgreSQL data | High | Data breach, unauthorized access |
| Kubernetes cluster and workloads | Critical | Full infrastructure compromise |

### 4.2 Threat Actors

- Anonymous internet users.
- Authenticated malicious users.
- Malicious or compromised tenants.
- Malicious prompt authors or tool providers.
- Insiders with excessive or misused access.
- Automated bots and scanners.
- Supply chain attackers.

### 4.3 Top Threats and Controls

| # | Threat | Likelihood | Impact | Primary Controls |
|---|--------|-----------|--------|------------------|
| T1 | Prompt injection through user content or tool outputs causing unauthorized tool calls or data exfiltration | High | High | Tool allowlists, human-in-the-loop for sensitive tools, input/output validation, instruction hierarchy, canary tokens, tenant/session isolation |
| T2 | Broken object-level authorization in FastAPI or LangGraph nodes leading to cross-tenant access | High | High | OIDC scopes, resource ownership checks, Postgres RLS, Neo4j tenant filtering, deny-by-default node permissions |
| T3 | Secret leakage in source code, CI logs, container images, or environment variables | Medium | Critical | Secret manager, pre-commit and CI secret scanning, masked logs, no plaintext secrets in Git, image scanning |
| T4 | Supply chain compromise via malicious dependencies, base images, or compromised CI/CD | Medium | High | Pinned lockfiles, Dependabot/Renovate, SCA scanning, SBOM, signed images, distroless non-root containers, protected branches |
| T5 | SSRF via LangGraph tool execution allowing internal network access | Medium | High | Egress network policies, URL allowlists, sandboxed tool pods, SSRF filtering |
| T6 | Insecure SSE exposure or CORS misconfiguration allowing unauthorized event stream access | Medium | Medium | Authenticated SSE, CORS allowlist, CSRF protection, CSP headers |
| T7 | Kubernetes misconfiguration, pod escape, or over-permissive RBAC | Low | Critical | Pod Security Standards, least-privilege RBAC, NetworkPolicy, no default ServiceAccount, read-only root FS, runtime monitoring |
| T8 | Data exfiltration through LLM outputs or unredacted logs | Medium | High | PII minimization, output filtering, DLP review, centralized redaction, audit logs |
| T9 | Denial of service or cost abuse via uncontrolled agent loops or tool calls | Medium | Medium | Rate limits, max iterations, token/cost caps, per-user quotas |
| T10 | Model abuse, harmful content, or policy violations | Medium | Medium | Content safety filters, allowed-use policy, monitoring and alerting |

---

## 5. Security Controls

### 5.1 Secrets Management

- **No plaintext secrets** in Git, configuration files, environment variables, CI logs, or container images.
- All production secrets are stored in a secret manager, e.g. HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager.
- Kubernetes Secrets are consumed via **External Secrets Operator** or equivalent. Kubernetes Secrets are encrypted at rest using the cloud provider KMS or equivalent.
- `.env` files are git-ignored. Only `.env.example` with placeholder values is committed.
- **Pre-commit hooks** run `gitleaks` or equivalent secret scanning.
- **CI secret scanning** blocks merge requests containing secrets or suspected credentials.
- Service-to-service authentication uses short-lived service accounts or mTLS. No shared long-lived passwords.
- Secrets are never passed into LLM prompts, tool inputs, or model context.
- Secret rotation is required every **90 days** or immediately on suspected compromise.
- Access to secret managers is restricted to the minimum set of operators and service identities.

### 5.2 Authentication and Authorization

#### Authentication

- Human users authenticate via **OIDC/OAuth2** through a trusted identity provider.
- SPA uses **Authorization Code with PKCE**; client secrets are never embedded in the React frontend.
- **MFA is required** for all human user accounts.
- Access tokens are short-lived, maximum **15 minutes**. Refresh tokens rotate on every use.
- Session cookies use `HttpOnly`, `Secure`, and `SameSite=Strict`.
- Machine-to-machine authentication uses scoped API keys or OAuth2 client credentials. API keys are hashed at rest and have expiry and IP/network restrictions where feasible.
- Backend service identities use Kubernetes ServiceAccounts with bound OIDC tokens. No default ServiceAccount is used.

#### Authorization

- Role-based access control is enforced at every layer:
  - **FastAPI dependencies** verify authentication, required scopes, tenant membership, and object ownership.
  - **PostgreSQL** uses row-level security based on `tenant_id` and user context.
  - **Neo4j** queries always include tenant/project filters. Parameterized Cypher is mandatory.
  - **LangGraph nodes** inherit the caller’s tenant/user context and execute with only the permissions required for that node.
- Deny by default at all layers. Permissions are additive only through explicit policy.
- Tenant isolation is mandatory. No cross-tenant data may appear in any query, tool output, or log.
- High-privilege operations, e.g. tenant admin changes, require step-up authentication or a fresh MFA challenge.

### 5.3 LLM Prompt and Tool Security

- **All model input and tool output is treated as untrusted.**
- System prompts follow an instruction hierarchy that makes user content clearly delimited and lower priority than developer/system instructions.
- Prompt injection defenses include:
  - Delimited user input: `"""..."""`, XML tags, or similar.
  - Explicit instructions that tool calls and data access must follow the user’s authenticated tenant context.
  - Canary tokens in system prompts to detect prompt injection attempts.
- Tools are registered in a per-node **allowlist**. A node may only call tools explicitly declared for that workflow step.
- Sensitive tools, e.g. write operations, external sending, code execution, or data deletion, require **human-in-the-loop approval** via LangGraph interrupts.
- Tools run in isolated, sandboxed pods or containers with no access to cluster secrets or internal services unless explicitly required.
- Tool outputs are validated, sanitized, and escaped before being returned to the model or UI. Outputs are never trusted as instructions.
- Arbitrary code execution is prohibited unless it runs in a dedicated, network-isolated sandbox with no access to internal resources.
- LLM calls never include secrets, passwords, full database records, or other tenants’ data.
- PII minimization is enforced. Only the minimum necessary data is sent to external LLM providers.
- Rate limits, maximum step counts, token caps, and cost caps are enforced per user, tenant, and workflow.
- Content safety filters are applied to both model inputs and outputs where supported by the provider.
- All prompts, tool calls, and model outputs are logged for audit, with secrets and PII redacted.

### 5.4 Dependency and Container Scanning

- All dependencies are pinned using lockfiles: `requirements.lock`, `poetry.lock`, `package-lock.json`, or equivalent.
- **Dependabot/Renovate** is enabled for automated dependency update pull requests.
- CI runs software composition analysis on every merge request:
  - Python: `pip-audit` or `trivy fs`.
  - Node: `npm audit --omit=dev` or `trivy fs`.
  - Containers: `trivy image`, `grype`, or equivalent.
- **SBOM** is generated for every release using Syft or similar.
- Container images are signed with **Cosign** or equivalent. Admission policies verify signatures before deployment.
- Base images are **distroless** or minimal Alpine. Services run as **non-root** users with **read-only root filesystem**.
- High and critical vulnerabilities block merge and deployment. Remediation SLA:
  - Critical: 48 hours.
  - High: 7 days.
  - Medium: 30 days.
- Container scanning results are uploaded to the security dashboard and reviewed by the security engineer.

### 5.5 Network and Kubernetes Hardening

- All external traffic uses TLS 1.2+.
- Ingress enforces HTTPS and security headers: CSP, X-Content-Type-Options, Referrer-Policy, and HSTS.
- CORS is restricted to known frontend origins. SSE endpoints require authentication and CORS allowlisting.
- Kubernetes NetworkPolicies default-deny ingress and egress. Only required service-to-service communication is allowed.
- Pods run with **Pod Security Standards** `restricted` or `baseline` as appropriate.
- Kubernetes RBAC follows least privilege. No wildcard `*` permissions in Roles or ClusterRoles.
- Default ServiceAccount is disabled. Each workload uses a dedicated ServiceAccount with minimal permissions.
- Containers run as non-root, with read-only root filesystem, and no shell where possible.
- Egress traffic from tool execution pods is restricted to an allowlist of required external endpoints.
- Runtime security monitoring, e.g. Falco, alerts on suspicious pod behavior, unexpected exec, or anomalous network connections.
- Admission policies, e.g. OPA/Gatekeeper or Kyverno, enforce security context, image registry allowlist, and network policy requirements.

### 5.6 Data Protection

- Data at rest is encrypted:
  - PostgreSQL and Neo4j volumes use encrypted storage.
  - Kubernetes Secrets are encrypted with KMS.
- Data in transit is encrypted using TLS 1.2+ for all internal and external traffic.
- Backups are encrypted and access-restricted.
- Tenant data deletion requests are honored within 30 days.
- Database users for services have least privilege:
  - PostgreSQL: table-level and row-level security enforced by `tenant_id`.
  - Neo4j: dedicated database user with property-level filtering.
- No production data may be copied to development or staging environments without redaction.

### 5.7 Logging and Monitoring

- Centralized log aggregation with structured logs.
- Security-relevant events are logged:
  - Successful and failed authentication.
  - Authorization denials.
  - Tool calls and human-in-the-loop approvals.
  - Secret access attempts.
  - Kubernetes pod/container anomalies.
  - CI/CD deployment events.
- Logs are redacted for secrets, tokens, and PII before storage.
- Logs are immutable and retained for at least 90 days. Security logs retained for 12 months.
- Alerting is configured for:
  - Multiple failed login attempts.
  - Cross-tenant access attempts.
  - Prompt injection canary token detection.
  - Unexpected tool or network behavior.
  - High/critical vulnerability findings in CI or registry.
  - Kubernetes runtime security events.

---

## 6. Incident Response

### 6.1 Reporting

Report suspected security incidents or vulnerabilities to:

- **Email:** `security@example.com` or the internal private security channel.
- **Slack:** `#security-incident`
- Do **not** post sensitive details in public or team-wide channels.

Include:

- Affected service, endpoint, or asset.
- Steps to reproduce.
- Observed impact or potential impact.
- Relevant logs or screenshots, if safe to include.
- Whether the issue is active and ongoing.

### 6.2 Severity Classification

| Severity | Definition | Examples |
|----------|------------|----------|
| Critical | Active exploitation, full platform compromise, secret leakage, cross-tenant data access | Leaked production secret, remote code execution, cluster admin access |
| High | Likely compromise, significant data exposure, broken authn/z | Broken object-level authorization, unauthorized tool execution |
| Medium | Limited impact, requires specific conditions | Misconfigured CORS, missing security header, moderate vulnerability |
| Low | Hardening gap, theoretical risk | Non-exploitable vulnerability, missing rate limit |

### 6.3 Response Process

1. **Detect and declare.** Any team member can declare an incident. The security engineer owns triage.
2. **Assign severity** using the table above.
3. **Contain.** Revoke affected credentials, isolate compromised pods, block malicious actor or source IP, redirect traffic if needed.
4. **Eradicate.** Remove exploit path, patch vulnerability, rotate secrets, rebuild affected container images.
5. **Recover.** Restore service from clean backups if necessary, verify no persistence remains, monitor for recurrence.
6. **Post-incident.** Blameless postmortem within 5 business days. Track action items to completion.

### 6.4 Communication

- Critical/high incidents must be escalated to the Architect and Product Owner immediately.
- Legal/regulatory notification is determined by the Architect and management.
- External communication is restricted to authorized personnel only.

---

## 7. Security Checklist

This checklist must be linked from `CONTRIBUTING.md` and the CI documentation. All items must pass or be explicitly reviewed before merge or deployment.

### Code and Configuration

- [ ] No plaintext secrets in code, configuration, environment files, or tests.
- [ ] All dependencies pinned in lockfiles.
- [ ] No known high or critical vulnerabilities from SCA scans.
- [ ] API endpoints enforce authentication and authorization dependencies.
- [ ] All database queries enforce tenant isolation: Postgres RLS and Neo4j tenant filters.
- [ ] Parameterized Cypher used for all Neo4j queries. No string concatenation.
- [ ] CORS, CSP, and security headers correctly configured.
- [ ] Input validation and output encoding applied to all user and model data.

### LLM and Tool Security

- [ ] LangGraph tool allowlist defined for every node.
- [ ] Sensitive tool calls require human-in-the-loop approval.
- [ ] Tool outputs treated as untrusted and validated/escaped.
- [ ] No secrets or cross-tenant data included in LLM prompts.
- [ ] Rate limits, max steps, and token/cost caps configured.
- [ ] Prompt injection canary tokens present in system prompts.
- [ ] Content safety filters enabled where applicable.

### Container and Kubernetes

- [ ] Container image scanned; no high or critical vulnerabilities.
- [ ] Image signed with Cosign or equivalent.
- [ ] Container runs as non-root with read-only root filesystem.
- [ ] Kubernetes manifest passes Pod Security Standards `restricted` or `baseline`.
- [ ] NetworkPolicy applied; egress restricted to required endpoints.
- [ ] No default ServiceAccount used; dedicated least-privilege ServiceAccount in use.
- [ ] No wildcard RBAC permissions.
- [ ] Resource limits and requests defined.

### CI/CD and Deployment

- [ ] Secret scanning passes.
- [ ] SCA, SAST, and container scanning pass.
- [ ] Security regression tests pass.
- [ ] Merges require at least one code review.
- [ ] Protected branches enabled for `main`/`release`.
- [ ] Deployment uses signed artifacts from trusted registry.

### Operational

- [ ] Security-relevant events logged and redacted.
- [ ] Alerts configured for suspicious events.
- [ ] Incident response contacts documented.
- [ ] Secrets rotation schedule documented and active.

---

## 8. Reporting Vulnerabilities

Do not open public issues for security vulnerabilities. Instead, report via the private security channel or email `security@example.com`.

We follow a coordinated disclosure process:

- Acknowledge report within **2 business days**.
- Validate and triage within **5 business days**.
- Provide a fix timeline based on severity.
- Credit researchers when the issue is resolved, unless anonymity is requested.

---

## 9. References

- `CONTRIBUTING.md` — must link to the security checklist in Section 7.
- `docs/CI.md` — must link to the security checklist in Section 7.
- `docs/architecture/adrs/` — architecture decision records for security-sensitive choices.
- `docs/incident-response/runbooks/` — incident runbooks and contact information.
- `infra/k8s/` — Kubernetes manifests, NetworkPolicies, and Pod Security Standards configuration.
- `scripts/security/` — secret scanning, SCA, and container scanning helper scripts.