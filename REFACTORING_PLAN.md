# Estado de la refactorización

Registro de lo que se propuso, lo que se hizo y dónde vive hoy. No es una hoja de
ruta: lo que falta de verdad está en [`docs/IMPROVEMENT_PLAN.md`](docs/IMPROVEMENT_PLAN.md)
y en los issues abiertos.

Última revisión: 2026-10-30, contra `main`.

## 1. Backend modular — hecho

- `psycopg2` fuera; **SQLModel** para todas las tablas.
- `main.py` ya no contiene rutas: solo monta routers, CORS y el ciclo de vida
  (121 líneas). La lógica HTTP vive en `backend/app/routers/`, once módulos:
  `auth`, `enterprise`, `sprint`, `agents`, `agent_runs`, `support`, `github`,
  `inbox`, `messages`, `procedures`, `workspace`.
- La lógica de negocio está en `backend/app/services/`: `outbox_service`,
  `agent_service`, `discovery_service`, `task_worker`, `verification`,
  `reviewer_agent`, `board`, `risk`.
- Un router no toca la base de datos: recibe `get_session` y
  `auth_deps.get_current_user` inyectados.

## 2. Bucle de agentes — hecho

- El bucle de corrección existe: `real_execution_node` valida y reintenta con el
  error concreto.
- La validación de salida existe: `validate_design_node` rechaza diagramas
  Mermaid vacíos o mal formados.
- RAG sobre grafo: `EngramProcessor` combina búsqueda semántica en pgvector con
  búsqueda estructural en Neo4j.

## 3. Frontend — hecho

- Estado con **Zustand** (`agentStore.ts`, `toastStore.ts`).
- Llamadas a la API con **TanStack Query**, centralizadas en
  `frontend/src/hooks/useQueries.ts`.
- Componentes de board, panel de control, gobierno, analítica y generación
  consumen esos hooks en lugar de llamar a la API a mano.

## 4. Gobierno y observabilidad — hecho

Lo que este documento daba como pendiente ya está en `main`:

- **Cuotas por Business Unit en tiempo real.** `llm-proxy/app.py` aplica
  `enforce_business_unit_quota` antes de cada llamada al modelo y actualiza el
  consumo después; añade una cabecera de aviso al acercarse al límite.
- **Trazas OpenTelemetry.** `opentelemetry-api`, `-sdk` y `-exporter-otlp` son
  dependencias del backend, y el cliente del proxy emite spans. Jaeger está en
  `docker-compose.yml` (`:16686`) y los servicios aparecen como `fabrique-api`,
  `agent-worker` y `llm-proxy`.
- **Feedback humano.** El endpoint antiguo `POST /v1/approve_task` está retirado
  y responde 410 indicando el reemplazo. El reemplazo es
  `POST /v1/agent_runs/{run_id}/approve`, que guarda un `AgentRunApproval` con
  `decision`, `feedback` y `rejection_category`, firmado en el registro de
  auditoría. El agente recibe ese texto como corrección.

## 5. Lo que este plan pedía y ya no aplica

- «Cada cambio en una rama `feature/` o `bugfix/`»: hoy las ramas son tipadas
  (`fix/`, `docs/`, `ci/`, `chore/`) y salen siempre de `main`. La convención
  vigente está en [`CONTRIBUTING.md`](CONTRIBUTING.md).
- «Lee `CONTRIBUTING.md` para las convenciones»: sigue siendo cierto, y ahora
  también [`CODING_STANDARDS.md`](CODING_STANDARDS.md), que tiene los comandos.

## 6. Lo que queda de verdad

Nada de esta lista. El trabajo pendiente de saneamiento y las mejoras de
producto viven en issues y en `docs/IMPROVEMENT_PLAN.md`, no aquí: mantener dos
hojas de ruta es cómo una de las dos se queda mintiendo.
