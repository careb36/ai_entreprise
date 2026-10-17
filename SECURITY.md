# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in La Fabrique, please report it responsibly.

**Do not** open a public GitHub issue for security vulnerabilities.

Instead, please report vulnerabilities by opening a [private security advisory](https://github.com/careb36/le-fabrique/security/advisories/new) on GitHub.

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested remediation

We aim to acknowledge reports within 48 hours and will keep you informed of our progress.

## Security Measures

La Fabrique implements the following security measures:

### Authentication & Authorization
- JWT-based authentication with HS256 signing
- Role-Based Access Control (RBAC) for both human and AI agent roles
- Company-scoped data isolation (multi-tenancy)
- Owner > Manager > Viewer role hierarchy

### Data Protection
- Fernet encryption for credentials at rest (`FERNET_SECRET_KEY`)
- bcrypt password hashing
- HMAC-SHA256 audit log signing (`AUDIT_SIGNING_KEY`)

### Infrastructure
- Token budget enforcement per company at the LLM proxy layer
- NATS JetStream for secure message broker communication
- Docker Compose isolation for all services

### Audit & Observability
- Structured JSON logging compatible with ELK/Datadog/Splunk
- OpenTelemetry distributed tracing
- Immutable Engram audit trail in Neo4j

## Environment Variables

The following environment variables are **required** for secure operation:

| Variable | Purpose | Required |
|----------|---------|----------|
| `JWT_SECRET_KEY` | JWT token signing | Yes |
| `FERNET_SECRET_KEY` | Credential encryption | Recommended |
| `AUDIT_SIGNING_KEY` | Audit log HMAC signing | Recommended |
| `POSTGRES_PASSWORD` | Database password | Yes |
| `NEO4J_PASSWORD` | Neo4j password | Yes |

Generate secure keys with:
```bash
# JWT secret
python -c "import secrets; print(secrets.token_urlsafe(48))"

# Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Audit signing key
python -c "import secrets; print(secrets.token_hex(32))"
```
