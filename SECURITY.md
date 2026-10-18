# SECURITY.md

## La Fabrique – AI-Augmented Software Factory

**Version:** 1.0 (Draft for review)  
**Author:** Security Engineer  
**Date:** 2025-04-08  
**Status:** Pending Architect and PO approval  

---

## 1. Introduction

This document establishes the foundational security policies for La Fabrique, covering dependency management, data handling, and access control. It also includes an initial threat model to identify key risks and their mitigation strategies. All teams (Development, QA, Operations) must adhere to these policies. The Security Engineer will maintain this document and update it as the platform evolves.

---

## 2. Dependency Management

### 2.1 Software Bill of Materials (SBOM)

- Every release (including pre-release and demo builds) **must** include a machine-readable SBOM.
- SBOMs shall be generated automatically using **Syft** for container images and language-level package managers (pip, npm).
- Supported formats: SPDX 2.3 and CycloneDX 1.4 (both generated and stored alongside build artifacts).
- SBOMs must be uploaded to a centralized repository (e.g., **GitHub Dependency Graph** or a dedicated S3 bucket) for vulnerability scanning.

### 2.2 Vulnerability Scanning & Patching

- All dependencies (direct and transitive) are scanned **on every commit** using **Trivy** or **Snyk** integrated into the CI pipeline (GitHub Actions).
- **Critical and High** severity vulnerabilities block the build unless a documented exception is approved by the Security Engineer and Architect.
- Medium severity vulnerabilities must be addressed within the next two sprints.
- Automated pull requests (e.g., Dependabot, Renovate) are enabled for all repositories, with priority overrides for security patches.

### 2.3 Dependency Approval Policies

- Only packages from trusted registries (PyPI, npm, official Docker Hub, GitHub Packages) are allowed.
- New dependencies require a lightweight review in the repository’s `DEPENDENCIES.md` (or issue discussion) covering:
  - Purpose and necessity
  - License compatibility (must be OSI-approved, no GPL v3 if used in internal services)
  - Known vulnerabilities and maintenance status
- **Prohibited**: Deprecated packages, packages with known malicious intent, unmaintained libraries (no commits in >1 year without justification).

### 2.4 Container Base Images

- Prefer distroless or slim base images (e.g., `python:3.12-slim`, `node:20-alpine`).
- All base images must be pinned to digest values (not tags like `latest`) to ensure immutability.
- Regularly scan and rebuild images (at least monthly or after a critical CVE disclosure).

---

## 3. Data Handling

### 3.1 Data Classification

La Fabrique processes the following data categories:

| Category | Examples | Classification | Handling Requirements |
|----------|----------|----------------|----------------------|
| **User Input** | Prompts, uploaded files, chat messages | Public (but require privacy) | Encrypted in transit (TLS 1.3+), stored encrypted at rest (AES-256). Retention limited to session + audit window (30 days). |
| **Generated Code** | AI-produced source code, diffs | Internal | Logged in Neo4j for traceability; access restricted to project members. Code that may contain secrets (e.g., passwords, API keys) is scanned pre-commit. |
| **Configuration & Secrets** | API keys, model endpoints, database passwords | Confidential | Never stored in source code. Managed via external secret store (HashiCorp Vault or cloud-native). Only containerized services with least‑privilege IAM roles can access secrets. |
| **System Logs** | Application logs, audit trails | Internal | Retention: 90 days for diagnostics, 1 year for audit. Access limited to Operations and Security teams. |

### 3.2 Encryption Standards

- **In transit**: TLS 1.3 (minimum) for all APIs, database connections (PostgreSQL, Neo4j), and inter-service communication (mutual TLS in service mesh).
- **At rest**: Encrypt all persistent volumes (EBS/PDB) using platform-managed keys (KMS). Databases (PostgreSQL, Neo4j) must have encryption at rest enabled.
- **Key management**: Secrets are rotated every 90 days; encryption keys follow the principle of least privilege with automatic key rotation.

### 3.3 Data Minimization & Retention

- Collect only data necessary for the function (e.g., agent logs are pruned after 30 days unless part of an active debugging session).
- User-provided data (e.g., sample prompts) can be anonymized and aggregated for model improvement only with explicit consent; otherwise deleted after the session.
- Implement automatic deletion cronjobs for log archives exceeding retention limits.

### 3.4 Data Loss Prevention (DLP)

- Scanner agents must mask or redact any detected secrets (e.g., AWS keys, GitHub tokens) in logs and AI-generated outputs.
- Use static analysis (e.g., **detect-secrets**, **truffleHog**) on both user input and generated code to prevent accidental exposure.
- All data egress (outside the cluster or service mesh) is logged and audited.

---

## 4. Access Control

### 4.1 Identity and Authentication

- All human access to La Fabrique services (API, UI, registry) is authenticated via **OAuth 2.0 / OpenID Connect** with multi-factor authentication (MFA) mandatory.
- Service-to-service authentication uses **mutual TLS** with short-lived certificates (24h) issued by the internal certificate authority (cert-manager).
- Machine accounts (CI/CD, automation) use **GitHub Actions OIDC** tokens to assume short-lived roles in AWS/GCP—no long-lived static credentials.

### 4.2 Authorization Policy (RBAC)

Access is granted based on roles. The following roles and their permissions are defined for the `fastapi-backend` service (enforced via FastAPI middleware and Casbin):

```yaml
# Example: sample-rbac-policy.yaml
roles:
  admin:
    permissions:
      - "read:all"
      - "write:all"
      - "delete:all"
      - "manage:users"
      - "manage:agents"
  developer:
    permissions:
      - "read:agents"
      - "write:agents"       # Can create/update own agents
      - "read:sessions"
      - "write:sessions"     # Can start/stop sessions
      - "read:logs"          # Own project logs
  viewer:
    permissions:
      - "read:agents"        # Public agents only
      - "read:sessions"      # Read-only session history
  automation:
    permissions:
      - "write:pipelines"
      - "read:secrets"       # Only specific secrets via Vault
```

- All role assignments are managed through the **Identity Provider** (IdP) groups.
- The API gateway (Kong or Envoy) enforces coarse-grained access; fine-grained checks are performed at the FastAPI application layer.
- Permission changes are logged and must be approved by the Architect for production environments.

### 4.3 Network Access Control

- All services run inside a **Kubernetes mesh** (Istio) with strict mTLS and authorization policies.
- External ingress is limited to the FastAPI gateway (443) and optionally the UI (static hosting via CDN).
- Development environments are isolated from production via separate clusters or namespaces with separate network policies.
- Database ports (5432 for PostgreSQL, 7687 for Neo4j) are not exposed to the public internet; only mesh sidecars may connect.

### 4.4 Principle of Least Privilege

- Every service container runs with a dedicated Kubernetes `ServiceAccount` that has exactly the permissions needed (e.g., `read-only` for Neo4j if the service only queries).
- Human developers accessing the cluster (for debugging) must use short-lived kubectl credentials (via `gcloud auth` or `aws eks get-token`) with role `readonly` by default; write access requires separate approval.

---

## 5. Initial Threat Model

The following table identifies the most critical threats to La Fabrique’s AI‑augmented platform, their impact, and the mitigation strategies we implement.

| # | Threat | Description | Impact | Likelihood | Mitigation Strategies |
|---|--------|-------------|--------|------------|----------------------|
| T1 | **Prompt Injection** | Malicious user injects instructions into the AI model to override intended behavior (e.g., leak system prompts, execute arbitrary code). | High (data breach, code injection) | Medium | 1. Input sanitization (strip control characters, limit prompt length). 2. Use model guardrails (e.g., content filter, instruction adherence). 3. Rate limiting per user/session. 4. Separate foundation model calls from user‑written code with strict context scoping. |
| T2 | **Unsanitized Generated Code** | AI produces code containing vulnerabilities (XSS, SQL injection, command injection) that a user unknowingly executes. | High (supply‑chain attack) | Medium | 1. Static analysis (Semgrep, CodeQL) on generated code before rendering. 2. Sandbox execution of generated code in isolated containers (gVisor). 3. Output scanning for dangerous patterns (e.g., `eval`, `exec`, `os.system`). |
| T3 | **Data Leakage via Agent Logs** | AI agent internal state or user conversation history is exposed through logs or error messages that are not properly redacted. | Medium (compliance, privacy) | High | 1. Implement structured logging with automatic field redaction for secrets. 2. Log levels restricted to `INFO` in production; `DEBUG` only in isolated sandboxes. 3. RBAC on log storage (see section 4.2). 4. Audit log retention and rotation. |
| T4 | **Privilege Escalation** | An attacker with limited API access (e.g., viewer) gains permissions to manage agents or secrets by exploiting a misconfigured role or endpoint. | Critical (full system compromise) | Low | 1. Mandatory Casbin policy review before each release. 2. Penetration testing quarterly focusing on authorization bypass. 3. HTTP method / path validation at API gateway. 4. Service‑to‑service tokens have separate scopes (no shared tokens). |
| T5 | **Dependency Supply‑Chain Attack** | A compromised upstream library (e.g., `requests`, `axios`) is used in production, allowing remote code execution or backdoor. | Critical (full compromise) | Low (but increasing) | 1. SBOM generation and continuous scanning (see section 2). 2. Pin all dependencies with hash verification (`pip hash`, `npm lockfile`). 3. Binary authorization (only signed images from approved registries). 4. Weekly vulnerability scans and automated PRs for patches. |
| T6 | **Model Poisoning (Training)** | If the factory uses fine‑tuned models, an adversary could inject poisoned data to cause the model to produce harmful outputs. | Medium (reputation) | Rare (at this stage) | 1. Use only trusted, vetted base models (e.g., official OpenAI, Hugging Face base). 2. Sandbox fine‑tuned models in a separate namespace with no access to production secrets. 3. Regular red‑team evaluations of model outputs. |
| T7 | **Denial of Service (DoS) via AI API** | An attacker sends many large prompts to exhaust the AI model API quota or compute resources. | Medium (availability) | Medium | 1. Rate limiting per IP, per user, per API key. 2. Automatic scaling of backend pods with horizontal pod autoscaler. 3. Quotas on token usage per tenant. 4. Implement circuit breakers for downstream AI model calls. |

### 5.1 Ongoing Threat Management

- **Monthly review** of the threat model by Security Engineer and Architect.
- **Severity thresholds**: Threats with Critical/High severity must be addressed before the feature or configuration is promoted to production.
- **Automated scanning**: Every new endpoint or data flow added to the architecture must be assessed against this threat model; additions are tracked in the security backlog.

---

## 6. Compliance and Monitoring

- **Audit Logging**: All access to sensitive operations (admin actions, secret retrieval, user creation) are logged to a centralized, immutable audit trail (e.g., AWS CloudTrail, GCP Audit Logs) with retention of 1 year.
- **Incident Response**: Follow the **La Fabrique Incident Response Plan** (separate document) – security incidents (breaches, successful attacks) are escalated to the Security Engineer and PO within 1 hour of detection.
- **Compliance Frameworks**: The platform targets SOC 2 Type II and ISO 27001 controls. Evidence collection (e.g., access reviews, vulnerability scans) is automated where possible.

---

## 7. Review and Approval

This document requires approval from:

- **Architect** – to confirm alignment with system architecture and feasibility.
- **Product Owner** – to accept resource commitments for security tooling and processes.

**Review frequency**: Annually, or upon significant architectural changes (e.g., new data store, new AI provider).

### Approval Signatures

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Security Engineer | (Author) | 2025-04-08 | (auto-generated) |
| Architect | ___________ | ___________ | ___________ |
| Product Owner | ___________ | ___________ | ___________ |

---

*End of SECURITY.md*