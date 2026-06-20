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
    *   **Pendiente:** el `task_worker` debe propagar el `X-Business-Unit-ID` header en todas las llamadas al proxy.

2.  **OpenTelemetry Tracing:** ✅ COMPLETADO (instrumentación) / ⬜ PENDIENTE (despliegue)
    *   `backend/telemetry.py` configura `TracerProvider` con exportador OTLP/HTTP.
    *   `llm-proxy/app.py` instrumentado con `FlaskInstrumentor` y `RequestsInstrumentor`.
    *   `backend/agents/graph.py` usa `get_tracer(__name__)` en los nodos de LangGraph.
    *   ✅ **NUEVO:** Jaeger `all-in-one` añadido a `docker-compose.yml` (puerto 16686 UI, 4318 OTLP).
    *   ✅ **NUEVO:** `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` inyectado en `backend` y `llm-proxy`.
    *   **Pendiente:** instrumentar `vscode-daemon` con OTel para trazas end-to-end completas.

3.  **Human-in-the-Loop mejorado:** 🔄 PARCIALMENTE COMPLETADO
    *   `TaskQueue` model y `POST /v1/approve_task` existen y funcionan.
    *   El campo `feedback` llega al endpoint pero no se reenvía al agente para que lo procese.
    *   **Pendiente (próxima tarea):** publicar el feedback textual como mensaje adicional en el contexto del agente al re-encolarse la tarea tras rechazo.

---

## 🗂️ Deuda Técnica Identificada
**Estos elementos no bloquean el sistema pero deben abordarse en sprints dedicados.**

1.  **Modelos LLM desactualizados en `model_factory.py`:**
    *   El mapa `ROLE_MODEL_MAP` usa `gemini-1.5-pro` / `gemini-1.5-flash`.
    *   El proxy ya soporta `gemini-2.5-pro` y `gemini-2.5-flash` con resolución dinámica.
    *   Actualizar las entradas del mapa para que los agentes usen modelos más capaces y económicos.

2.  **Autenticación JWT no implementada:**
    *   `auth.py` hace hash de contraseñas con bcrypt pero los endpoints no requieren token.
    *   Toda la API es públicamente accesible mientras no haya JWT/session middleware.
    *   Añadir `python-jose` + middleware de autenticación en los routers sensibles.

3.  **Sin suite de tests:**
    *   Sólo existe `backend/tests/test_glassroom_sse.py`.
    *   Añadir tests unitarios para `CompanyService`, `SprintPlanner` y los routers críticos con `pytest` + `httpx`.

4.  **Credenciales sin cifrado real:**
    *   `ProjectCredential.encrypted_value` guarda un texto plano o referencia sin Vault/KMS.
    *   Integrar `python-cryptography` (Fernet) como mínimo, o añadir soporte para HashiCorp Vault.

5.  **VS Code Extension mínima:**
    *   `vscode-extension/extension.ts` existe pero no está integrada con el daemon NATS.
    *   Conectar la extensión para mostrar eventos del GlassRoom directamente en VS Code.

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
