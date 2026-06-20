# Changelog

All notable changes to the "La Fabrique" project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
* **Fernet credential encryption (`backend/app/crypto.py`):** `ProjectCredential.encrypted_value` is now encrypted at rest using AES-128-CBC + HMAC-SHA256 (Fernet). `encrypt_value()` / `decrypt_value()` utilities handle transparent plaintext fallback when `FERNET_SECRET_KEY` is absent (with a logged warning). `cryptography ^48.0.1` added to `pyproject.toml`.
* **HitL feedback propagation:** `POST /v1/approve_task` now operates on `BacklogItem.id` directly. On rejection, the item is reset to `todo` and a `task.assign` OutboxEvent is published containing the reviewer's `hitl_feedback` text. The task worker injects that feedback as a `## Human Review Feedback` section in the agent prompt, and emits a `task.hitl_rework` GlassRoom event.
* **X-Business-Unit-ID propagation in task worker:** `task_worker._execute_task()` now accepts `bu_id` and forwards it to `LLMProxyClient.chat()` as the `X-Business-Unit-ID` header so per-unit token budgets are correctly enforced for agent-generated calls.
* **JWT Authentication middleware:** All sensitive API endpoints now require a valid JWT `Bearer` token. New endpoint `POST /v1/auth/login` (OAuth2 password-grant) exchanges email + password for a signed HS256 access token. Role-gated endpoints (`init_company`, ticket updates, log download) enforce `Owner`/`Manager` roles. Public endpoints (`/v1/auth/register`, `/v1/support/report_bug`, `/v1/support/chat`, `GET /health`) remain unauthenticated.
  - New module `backend/app/auth_dependencies.py` with `create_access_token()`, `get_current_user()` dependency, and `require_roles(*roles)` factory.
  - `JWT_SECRET_KEY`, `JWT_ALGORITHM`, and `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` added to `Settings` (config-driven, safe defaults).
  - `PyJWT ^2.12.0` added to `pyproject.toml` (no known vulnerabilities, no extra compile deps).
* **OpenTelemetry / Jaeger integration:** `docker-compose.yml` now includes a `jaeger:1.58 all-in-one` service (UI on port 16686, OTLP/HTTP collector on port 4318). Both `backend` and `llm-proxy` automatically export traces via `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318`.

### Fixed
* **`backend/llm/model_factory.py`:** Updated `ROLE_MODEL_MAP` from `gemini-1.5-pro` / `gemini-1.5-flash` to `gemini-2.5-pro` / `gemini-2.5-flash`. The proxy already supported dynamic model resolution; the map was the only thing holding back the upgrade.
* **`backend/llm/model_factory.py`:** Added missing `Any` import from `typing`. `create_completion()` raised `NameError` at runtime when `bu_id` was provided.

### Changed
* **`REFACTORING_PLAN.md`:** Updated with accurate Phase 4 status, technical debt inventory, and JWT now marked complete.

---

## [0.1.0] - 2023-10-27

### Added

* **Project Initialization:** Established the initial project structure, including the core application scaffolding, configuration files, and directory layout for the La Fabrique platform.
* **Core Agent Service:** Implemented the foundational agent orchestration service. This service is responsible for receiving high-level development tasks, decomposing them into sub-tasks, and managing the execution lifecycle.
* **Tooling Integration Framework (Alpha):** Created the basic infrastructure for integrating external developer tools. This initial version includes placeholder interfaces for linters, compilers, and version control systems.
* **Prompt Engineering Library:** Seeded the initial library of system prompts and templates for core tasks like code generation, debugging, and test creation. This provides a baseline for the AI's operational capabilities.
* **CI/CD Pipeline Foundation:** Set up the initial continuous integration and deployment pipeline using GitHub Actions. The pipeline currently handles automated builds and runs placeholder unit tests on every commit to the `main` branch.
* **Core Documentation:** Created essential project documentation including `README.md`, `CONTRIBUTING.md`, and this `CHANGELOG.md` to establish development standards and provide a baseline for future contributors.

[unreleased]: https://github.com/la-fabrique-dev/la-fabrique/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/la-fabrique-dev/la-fabrique/releases/tag/v0.1.0
