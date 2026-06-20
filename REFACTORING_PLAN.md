# 🚀 Plan Maestro: La Fabrique Enterprise v2.0

Este documento contiene la estrategia de refactorización y mejora para el ecosistema de La Fabrique. 
Sigue las fases en orden para garantizar la estabilidad del sistema.

---

## 🏛️ Fase 1: Cimientos de Acero (Backend Refactor) — ✅ COMPLETADO
**Objetivo:** Salir del `main.py` gigante y pasar a una arquitectura modular y tipada.

1.  **Migración a SQLModel:** ✅ Reemplazado `psycopg2` por **SQLModel**.
    *   Modelos creados para: `Company`, `Agent`, `Sprint`, `BacklogItem`, `AuditLog`, `BusinessUnit`.
2.  **Modularización por Routers:** ✅ `main.py` dividido en `backend/app/routers/`:
    *   `auth.py`, `enterprise.py`, `sprint.py`, `agents.py`, `support.py`.
3.  **Capa de Servicios:** ✅ Lógica de negocio movida a `backend/app/services/`:
    *   `outbox_service.py`, `agent_service.py`, `discovery_service.py`, `task_worker.py`.

---

## 🧠 Fase 2: Inteligencia Auto-Sanable (LangGraph Evolution) — ✅ COMPLETADO
**Objetivo:** Agentes con bucles de retroalimentación y memoria persistente.

1.  **Bucle de Feedback (Self-Correction):** ✅ `real_execution_node` ahora realiza validación sintáctica (compile) y reintenta con feedback específico.
2.  **Validación de Salida (Guardrails):** ✅ `validate_design_node` asegura diagramas Mermaid no vacíos y bien formateados.
3.  **RAG sobre Grafos (Neo4j):** ✅ `EngramProcessor` integra búsqueda semántica (pgvector) con búsqueda estructural (Neo4j) para un contexto RAG superior.

---

## 🎨 Fase 3: Frontend de Alta Performance (React Upgrade) — ✅ COMPLETADO
**Objetivo:** UI reactiva, liviana y con mejor manejo de estado.

1.  **Zustand Store:** ✅ Migración completa a Zustand (`useAppStore.ts` y `agentStore.ts`).
2.  **TanStack Query (React Query):** ✅ Implementados hooks personalizados en `useQueries.ts` para todas las operaciones de API.
3.  **Refactor de Componentes:** ✅ `CompanyNew`, `ControlPanel`, `KanbanBoard`, `Generate`, `GovernancePanel` y `AnalyticsDashboard` refactorizados para usar hooks de Query/Mutation.

---

## 🛡️ Fase 4: Governance y Observabilidad — 🔄 EN PROGRESO
**Objetivo:** Control de costos y trazabilidad total.

1.  **Cuotas en Tiempo Real:** ✅ COMPLETADO
    *   `enforce_business_unit_quota()` en `llm-proxy/app.py` frena llamadas al LLM cuando `token_quota_used >= token_quota_limit` (HTTP 429).
    *   `track_usage_in_db()` actualiza el contador tras cada respuesta exitosa.
    *   El modelo `BusinessUnit` en `backend/app/models/company.py` expone `token_quota_limit` y `token_quota_used`.
    *   ✅ **NUEVO:** `task_worker._execute_task()` propaga el header `X-Business-Unit-ID` al proxy vía `bu_id` extraído del NATS payload `task.assign`.

2.  **OpenTelemetry Tracing:** ✅ COMPLETADO (instrumentación) / ✅ DESPLEGADO (Jaeger) / ✅ DAEMON INSTRUMENTADO
    *   `backend/telemetry.py` configura `TracerProvider` con exportador OTLP/HTTP.
    *   `llm-proxy/app.py` instrumentado con `FlaskInstrumentor` y `RequestsInstrumentor`.
    *   `backend/agents/graph.py` usa `get_tracer(__name__)` en los nodos de LangGraph.
    *   ✅ Jaeger `all-in-one` añadido a `docker-compose.yml` (puerto 16686 UI, 4318 OTLP).
    *   ✅ `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` inyectado en `backend` y `llm-proxy`.
    *   ✅ **NUEVO:** `vscode-daemon/daemon.py` instrumentado con OTel — cada handler NATS (`workspace.generate`, `workspace.execute`) crea un span con atributos de workspace, conteo de archivos, exit code y éxito. Exporta al mismo endpoint Jaeger cuando `OTEL_EXPORTER_OTLP_ENDPOINT` está configurado. Degrada gracefully si el paquete OTel no está instalado.

3.  **Human-in-the-Loop mejorado:** ✅ COMPLETADO
    *   `TaskQueue` model y `POST /v1/approve_task` existen y funcionan.
    *   ✅ **NUEVO:** Al rechazar una tarea, `approve_task` reinicia el `BacklogItem` a `todo` y publica un evento `task.assign` con `hitl_feedback` en el payload NATS.
    *   ✅ **NUEVO:** `task_worker._execute_task()` inyecta el feedback humano en el prompt del agente con una sección `## Human Review Feedback` prominente antes del contexto de memoria.
    *   ✅ **NUEVO:** El GlassRoom recibe un evento `task.hitl_rework` cuando una tarea es re-procesada tras rechazo humano.

---

## 🗂️ Deuda Técnica Identificada
**Estos elementos no bloquean el sistema pero deben abordarse en sprints dedicados.**

1.  **Modelos LLM desactualizados en `model_factory.py`:**
    *   ✅ **RESUELTO:** `ROLE_MODEL_MAP` actualizado de `gemini-1.5-pro` / `gemini-1.5-flash` → `gemini-2.5-pro` / `gemini-2.5-flash`.
    *   El proxy ya soportaba resolución dinámica — sólo era necesario actualizar el mapa.

2.  **~~Autenticación JWT no implementada~~:** ✅ COMPLETADO
    *   `POST /v1/auth/login` genera tokens HS256. `get_current_user` + `require_roles()` protegen todos los endpoints sensibles.
    *   `PyJWT ^2.12.0` añadido; clave configurable vía `JWT_SECRET_KEY`.

3.  **Sin suite de tests:** ✅ COMPLETADO
    *   `backend/tests/test_crypto.py` — 9 tests para cifrado/descifrado Fernet (round-trip, fallback plaintext, clave inválida).
    *   `backend/tests/test_auth_dependencies.py` — 14 tests para `create_access_token`, `get_current_user` (expirado, manipulado, clave errónea, claim faltante) y `require_roles` (Owner/Manager/Viewer).
    *   `backend/tests/test_company_service.py` — 12 tests para `init_company`, `approve_task` (aprobación, rechazo, feedback HitL, OutboxEvent) y `get_quotas`.
    *   `backend/tests/test_sprint_planner.py` — 21 tests para helpers deterministas (`_build_stack_summary`, `_find_po_agent`, fallbacks de planificación) y la ruta de fallback de `plan_foundation_sprint`.
    *   `backend/tests/test_auth_router.py` — 10 tests de integración para `/v1/auth/register`, `/v1/auth/login` y protección JWT de endpoints sensibles vía `TestClient`.
    *   Total: **75 tests, todos en verde**.

4.  **~~Credenciales sin cifrado real~~:** ✅ COMPLETADO
    *   `backend/app/crypto.py` implementa cifrado Fernet simétrico (AES-128-CBC con HMAC-SHA256).
    *   `ProjectCredential.encrypted_value` ahora almacena el token Fernet; fallback a `plaintext:<value>` si `FERNET_SECRET_KEY` no está configurado.
    *   `FERNET_SECRET_KEY` añadido a `Settings`; generación con `Fernet.generate_key()`.
    *   `cryptography ^48.0.1` añadido a `pyproject.toml`.

5.  **VS Code Extension mínima:** ✅ COMPLETADO
    *   Nuevo comando `factory.glassroomPanel` — abre un panel WebView nativo en VS Code que conecta al endpoint SSE del backend (`GET /v1/glassroom/stream`) y renderiza los eventos de los agentes en tiempo real, con tarjetas codificadas por color por rol, auto-scroll, pausa y reconexión automática.
    *   El panel es un singleton: se reutiliza si ya está abierto; soporta el mensaje `reconnect` para cambiar de sprint sin recargar.
    *   El antiguo comando `factory.streamGlassroom` (terminal + curl) se conserva como fallback legacy.

---

## 🏗️ Estándares de Ingeniería y Git Strategy
**Objetivo:** Mantener el orden y la disciplina del repositorio.

1.  **Git Flow Disciplinado:**
    *   No trabajar directamente sobre `main` ni `develop`.
    *   Cada cambio debe ir en su propia rama `feature/` o `bugfix/`.
2.  **Conventional Commits:**
    *   Usar prefijos: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
3.  **Código Tipado:**
    *   Usar `typing` de Python en todo el backend.
    *   Usar interfaces y tipos estrictos en el frontend (TypeScript).
4.  **Cero Acoplamiento:**
    *   No mezclar lógica de base de datos con controladores de FastAPI.
    *   Inyectar dependencias siempre que sea posible.

## 🛠️ Instrucciones para Copilot / AI Assistant
- **Principio de Atomicidad:** No intentes aplicar todo el plan de una vez. Trabaja fase por fase.
- **Git Flow:** Al iniciar una tarea, pregunta por el nombre de la rama siguiendo la convención.
- **Commits:** Sugiere commits siguiendo la convención de Conventional Commits.
- **Documentación:** Lee el archivo `CONTRIBUTING.md` para más detalles sobre las convenciones.
- **Seguridad:** No modifiques las variables de entorno ni las claves de acceso.
- **Convenciones:** Mantén el estilo de código existente (FastAPI + Pydantic v2).
