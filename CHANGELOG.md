# Changelog

All notable changes to the "La Fabrique" project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
* **Test suite (`backend/tests/`):** 75 pytest tests covering all new Phase 4 modules.
  - `test_crypto.py` (9 tests) — Fernet round-trip, unique tokens, wrong key, tampered ciphertext, plaintext fallback, invalid key.
  - `test_auth_dependencies.py` (14 tests) — `create_access_token` claims, `get_current_user` with expired/tampered/wrong-secret/missing-claim tokens, `require_roles` Owner/Manager/Viewer access matrix.
  - `test_company_service.py` (12 tests) — `init_company` persistence (company, sprint, backlog, outbox, credentials, scope), `approve_task` approval/rejection/HitL feedback/OutboxEvent, `get_quotas`.
  - `test_sprint_planner.py` (21 tests) — deterministic helpers (`_build_stack_summary`, `_build_team_roster`, `_find_po_agent`, `_ensure_execution_team`, `_filter_fallback_tasks`, `_build_execution_fallback`, `_build_self_improvement_fallback`, `build_self_improvement_objective`) and `plan_foundation_sprint` fallback paths.
  - `test_auth_router.py` (10 tests) — `POST /v1/auth/register`, `POST /v1/auth/login` (valid, wrong password, unknown user, JWT claim check), `GET /health` public access, JWT-protection of `/v1/enterprise/quotas` and `/v1/company/invite`.
  - `conftest.py` — shared in-memory SQLite engine fixture with per-test rollback isolation.
  - `python-multipart ^0.0.9` added to `pyproject.toml` (required by FastAPI's OAuth2 `PasswordRequestForm`).
* **VS Code Extension — native GlassRoom WebView panel (`vscode-extension/extension.ts`):**
  - New command `factory.glassroomPanel` opens a singleton `vscode.WebviewPanel` (column Two) that connects directly to `GET /v1/glassroom/stream` via the browser-native `EventSource` API inside the WebView.
  - Renders agent events as colour-coded cards (role → colour palette), auto-scrolls to the latest message, and provides Pause/Clear controls.
  - Auto-reconnects when the SSE connection drops; accepts a `reconnect` message from the extension host to switch sprints without panel teardown.
  - The legacy `factory.streamGlassroom` terminal+curl command is preserved as a fallback and renamed "Stream Glassroom Events (terminal)" in the command palette.
  - `package.json`: new `factory.glassroomPanel` command + activation event registered.
* **OTel instrumentation for `vscode-daemon/daemon.py`:**
  - New `_setup_telemetry()` function initialises a `TracerProvider` with `BatchSpanProcessor` and `OTLPSpanExporter` when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Service name: `fabrique-vscode-daemon`.
  - Each NATS handler (`workspace.generate`, `workspace.execute`) is wrapped in a span with messaging/workspace attributes (`file_count`, `files_written`, `command`, `exit_code`, `success`).
  - Degrades gracefully (logs a warning, uses no-op tracer) when the `opentelemetry` packages are absent — daemon startup is never blocked.
  - Completes full end-to-end trace coverage: backend agent → NATS → daemon → Jaeger.

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
