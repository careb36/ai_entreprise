# Cobertura del front sobre la API (fase 1)

Relevamiento hecho el 2026-10-18 sobre `main` en `93c24f5`. La especificación se
generó con `app.openapi()` desde el código del backend de ese commit: **57
operaciones**, con las etiquetas Auth, Enterprise, Core, Agents, Agent Runs,
Support, GitHub y Workspace, más `/health`, `/health/deep` y `/metrics` sin
etiqueta. El lado del front se relevó en `frontend/src` (sin tests): llamadas en
`services/api.ts`, hooks en `hooks/useQueries.ts`, y qué pantalla montada en
`App.tsx` usa cada hook.

Estados:
- **cubierto**: hay una pantalla montada que dispara la llamada;
- **cliente sin UI**: existe la función o el hook, pero ninguna pantalla montada lo usa;
- **sin cliente**: el front no lo llama;
- **n/a**: no está pensado para la UI (lo llama GitHub, Prometheus u otro productor).

## Tabla de cobertura

| Método y path | `api.ts` | Hook | Pantalla (ruta) | Estado |
|---|---|---|---|---|
| GET `/health` | `health` | `useQuery` en `App.tsx` (`useHealth` solo en el `ControlPanel` sin montar) | `App.tsx`: indicador "Online" del encabezado | cubierto |
| GET `/health/deep` | — | — | — | sin cliente |
| GET `/metrics` | — | — | — | n/a (Prometheus) |
| POST `/v1/auth/login` | `login` | `useAuthStore.login` | `Login` (`/login`) | cubierto |
| GET `/v1/auth/me` | `me` | `useAuthStore.loadMe` | `ProtectedRoute` (todas las rutas protegidas) | cubierto |
| POST `/v1/auth/register` | `register` | — | `Register` (`/register`) | cubierto |
| POST `/v1/company/invite` | — | — | — | sin cliente |
| GET `/v1/agents` | `getAgents` | `useAgents` | `AgentSettings` (`/agents`) | cubierto |
| POST `/v1/agents` | `createAgent` | `useCreateAgent` | `AgentSettings`: "Nuevo agente" | cubierto |
| POST `/v1/agents/generate_prompt` | `generateAgentPrompt` | `useGenerateAgentPrompt` | `AgentSettings`: generar prompt | cubierto |
| PATCH `/v1/agents/{agent_id}` | `updateAgent` | `useUpdateAgentConfig` | `AgentSettings`: guardar tarjeta de agente | cubierto |
| DELETE `/v1/agents/{agent_id}` | `deleteAgent` | `useDeleteAgent` | `AgentSettings`: eliminar agente | cubierto |
| GET `/v1/analytics/orchestration_health` | `getOrchestrationHealth` | `useOrchestrationHealth` | `AnalyticsDashboard` (`/sprints/:id/analytics`) | cubierto |
| GET `/v1/analytics/summary` | `getAnalyticsSummary` | `useAnalyticsSummary` | `AnalyticsDashboard`; resumen del sprint (`/sprints/:id`) | cubierto |
| POST `/v1/approve_task` | — | — | — | sin cliente; **el backend lo devuelve como 410 Gone** (ver punto 4 abajo) |
| GET `/v1/capabilities` | `capabilities` | `useCapabilities` | `App.tsx`: muestra u oculta Generar y Memoria | cubierto |
| POST `/v1/discover_team` | `discoverTeam` | `useDiscoverTeam` | `CompanyNew` (`/companies/new`) | cubierto |
| GET `/v1/enterprise/lineage` | — | — | — | sin cliente |
| GET `/v1/enterprise/quota_usage_history` | `getQuotaUsageHistory` | `useQuotaUsageHistory` | `QuotaPanel` (`/company/quotas`) | cubierto |
| GET `/v1/enterprise/quotas` | `getQuotas` | `useQuotas` | `QuotaPanel`; `GlassRoom`; resumen del sprint | cubierto |
| POST `/v1/enterprise/quotas/{bu_id}/reset` | `resetQuota` | `useResetQuota` | `QuotaPanel`: botón reiniciar por unidad | cubierto |
| POST `/v1/init_company` | `initCompany` | `useInitCompany` | `CompanyNew`; resumen del sprint (arranque rápido) | cubierto |
| GET `/v1/models` | (inline `api.get`) | `useModels` | `CompanyNew`, `AgentSettings` | cubierto |
| PATCH `/v1/backlog/{item_id}` | `updateBacklogItem` | `useUpdateBacklogItem` | `KanbanBoard` (`/sprints/:id/board`): selector de estado | cubierto |
| POST `/v1/plan_self_improvement_sprint` | `planSelfImprovementSprint` | `usePlanSelfImprovementSprint` | resumen del sprint (`Dashboard.tsx` montado como pestaña Resumen) | cubierto |
| POST `/v1/plan_sprint` | `planSprint` | `usePlanSprint` | resumen del sprint | cubierto |
| GET `/v1/sprints` | `listSprints` | `useSprints` | `Sprints` (`/sprints`) | cubierto |
| GET `/v1/sprints/{sprint_id}/board` | `getSprintBoard` | `useSprintBoard` | `SprintWorkspace` y sus pestañas, `TaskDetails` | cubierto |
| POST `/v1/sprints/{sprint_id}/dispatch` | — | — | — | sin cliente |
| POST `/v1/sprints/{sprint_id}/pause` | `pauseSprint` | `usePauseSprint` | `SprintWorkspace`, pestaña Tablero: "Pausar" | cubierto |
| POST `/v1/sprints/{sprint_id}/resume` | `resumeSprint` | `useResumeSprint` | `SprintWorkspace`, pestaña Tablero: "Reanudar" | cubierto |
| POST `/v1/start_sprint` | `startSprint` (sin uso) + inline en el hook | `useStartSprint` | resumen del sprint | cubierto |
| POST `/v1/audit` | — | — | — | n/a (escritura de auditoría interna) |
| GET `/v1/audit/verify` | `getAuditVerification` | `useAuditVerification` | `EvidenceDetail` (`/evidence/:runId`) | cubierto |
| POST `/v1/generate` | `generate` | `useGenerate` | `Generate` (`/generate`, solo con `agent_graph`) | cubierto |
| GET `/v1/glassroom/stream` | (EventSource en `store/agentStore.ts`) | — | `GlassRoom` (`/sprints/:id/live`) | cubierto |
| GET `/v1/memory/search` | `searchMemory` | `useSearchMemory` (sin uso) | `MemorySearch` (`/memory`) llama `api.get` directo | cubierto (el hook queda sin uso) |
| POST `/v1/memory/store` | — | — | — | sin cliente |
| POST `/v1/agent_runs` | — | — | — | n/a (lo usan productores externos para registrar corridas) |
| GET `/v1/agent_runs` | `getAgentRuns` | `useAgentRuns` | `Evidence` (`/evidence`), `GovernancePanel` (`/governance`), insignias del `KanbanBoard`, `TaskDetails` | cubierto |
| GET `/v1/agent_runs/{run_id}` | `getAgentRun` | `useAgentRun` | `EvidenceDetail` | cubierto |
| GET `/v1/agent_runs/{run_id}/approvals` | `getAgentRunApprovals` | `useAgentRunApprovals` | `EvidenceDetail` | cubierto |
| POST `/v1/agent_runs/{run_id}/approve` | `approveAgentRun` | `useApproveAgentRun` | `EvidenceDetail`: aprobar/rechazar | cubierto |
| GET `/v1/agent_runs/{run_id}/verifications` | `getAgentRunVerifications` | `useAgentRunVerifications` | `EvidenceDetail` | cubierto |
| POST `/v1/agent_runs/{run_id}/verify` | `verifyAgentRun` | `useVerifyAgentRun` | `EvidenceDetail`: botón Verificar | cubierto |
| POST `/v1/integrations/github/webhook` | — | — | — | n/a (lo llama GitHub) |
| POST `/v1/integrations/github/bindings` | — | — | — | sin cliente |
| POST `/v1/integrations/github/backfill` | — | — | — | sin cliente |
| POST `/v1/support/chat` | (inline `api.post`) | — | `SupportBot` (botón flotante, todas las rutas) | cubierto |
| GET `/v1/support/logs` | — | — | — | sin cliente |
| POST `/v1/support/report_bug` | (inline `api.post`) | — | `SupportBot`: reportar error | cubierto |
| GET `/v1/support/tickets` | (inline `api.get`) | — | `SupportBot`: buscar tickets por email | cubierto |
| GET `/v1/support/tickets/{ticket_num}` | (inline `api.get`) | — | `SupportBot`: consultar un ticket | cubierto |
| PATCH `/v1/support/tickets/{ticket_num}` | — | — | — | sin cliente |
| GET `/v1/workspace/status` | `workspaceStatus` | `useWorkspaceStatus` | `CompanyNew`: estado del daemon | cubierto |
| GET `/v1/workspace/browse` | `browseWorkspace` | — | `FolderPicker` dentro de `CompanyNew` | cubierto, pero ver hallazgo A |
| GET `/v1/workspace/inspect` | `inspectWorkspace` | — | `CompanyNew`: tarjeta del proyecto | cubierto |

Totales: 43 cubiertos, 10 sin cliente (incluido `approve_task`, retirado) y 4 n/a.
Ningún endpoint queda solamente como "cliente sin UI": los clientes sin uso que
hay apuntan a paths que no existen (ver punto 3).

## Llamadas del front a paths que el backend no tiene

| Llamada | Dónde | Efecto |
|---|---|---|
| GET `/v1/browse_fs` | `CompanyNew.tsx` (3 lugares, con `http://localhost:8000` fijo) | **404 en `main`**: el endpoint se eliminó en #111 (hallazgo A) |
| GET `/v1/tickets` | `api.getTickets` → `useTickets` | latente: ninguna pantalla usa el hook |
| POST `/v1/report_bug` | `api.reportBug` → `useReportBug` | latente: ninguna pantalla usa el hook |

## Verificación de los puntos de la fase 1

**Punto 2. Endpoints que el prompt da como "sin referencia".** Se confirman sin
cliente: `company/invite`, `approve_task`, `enterprise/lineage`,
`sprints/{id}/dispatch`, `memory/store`, `support/logs`, `github/bindings`,
`github/backfill` y `health/deep`. Falso positivo: `POST /v1/support/chat` está
cubierto, porque `SupportBot.tsx` lo llama directamente con `api.post`.

**Punto 3. `/v1/tickets` y `/v1/report_bug`.** No hay ningún prefijo que los
resuelva: `API_BASE` es solo el host. Son bugs, pero latentes, porque `useTickets`
y `useReportBug` no se usan en ninguna pantalla; `SupportBot` ya llama a los paths
correctos `/v1/support/...`. Propuesta: corregir los dos paths en `api.ts` y hacer
que `SupportBot` use esos clientes, o borrar los clientes y hooks sin uso.

**Punto 4. Controles para las mutaciones que sí están en el cliente.**

| Endpoint | Control | Pantalla |
|---|---|---|
| pause / resume | botones Pausar y Reanudar, solo en la pestaña Tablero | `/sprints/:id/board` |
| approve (corrida) | Aprobar/Rechazar con categoría y comentario | `/evidence/:runId` |
| verify | botón Verificar | `/evidence/:runId` |
| reset de cuotas | botón por unidad de negocio | `/company/quotas` |
| `workspace/*` | estado del daemon, selector de carpetas, tarjeta del proyecto | `/companies/new` |
| PATCH / DELETE agents | guardar y eliminar en cada tarjeta | `/agents` |

## Estado del relevamiento del 2026-10-17

`docs/audits/front-2026-10-17.md` **no está en el repo**. Su contenido se cargó
como los issues #83, #84 y #85, y los tres se cerraron el 2026-10-17 con los PRs
#87, #88 y #89. Después, #100 reorganizó la navegación (`/sprints`, una página
por sprint con pestañas, redirecciones de las rutas viejas). Cada punto,
comparado con el código actual:

| Punto del prompt | Estado en `main` | Evidencia |
|---|---|---|
| F2: no persistir `activeSprintId` antes de un 200 del board; limpiarlo ante 404 | resuelto (#87) | `useSprintBoard` limpia el id ante 403/404; `SprintWorkspace.test.tsx` cubre "waits for URL sprint validation before persisting" |
| F2: `/workspace/999` muestra "sprint-999" y conecta el stream | resuelto (#87, #100) | `/workspace/:id` redirige a `/sprints/:id/live`, que valida el id antes de conectar |
| F2: no reintentar 4xx; query del board compartida | resuelto | `lib/queryClient.ts`: `retry: !isClientError && count < 1`; el board usa una sola clave, `QUERY_KEYS.sprintBoard`, y deja de consultar periódicamente tras un 4xx |
| F2: `TaskDetails` distingue tarea, sprint y red | resuelto (#87, #100) | `TaskDetails.test.tsx`: "distinguishes a missing sprint from a missing task", reintento ante fallo de carga |
| F2: `EvidenceDetail` con id inválido | resuelto (#87) | distingue id mal formado, 404 y fallo de carga (`EvidenceDetail.test.tsx`) |
| F2: reintentar y enlace en todo error | resuelto en esas pantallas | `ErrorState` con Retry y enlace |
| F3: Dashboard con 5 `t()`, Home con 1, CompanyNew con 0 | resuelto (#89) | hoy hay 66 `t()` en `Dashboard.tsx` y 134 en `CompanyNew.tsx`; `Home.tsx` ya no existe (`/` redirige a `/evidence`) |
| F3: el selector de idioma muestra "EN" con la UI en español | resuelto (#89) | muestra el idioma activo (test `LanguageSwitcher` en `App.test.tsx`) |
| F3: `eslint-plugin-i18next` | **pendiente** | el front no tiene ESLint configurado (ni dependencia ni config); el workflow corre `npx eslint src \|\| true`. Mientras tanto, `src/i18n/coverage.test.ts` falla si una clave literal falta en en/es |
| F4: overflow del encabezado a ~1270px | resuelto (#88, #100) | navegación corta + menú de empresa. Re-verificado ahora con Playwright sobre el build de `main`: sin overflow a 1024, 1269, 1366 y 1440 px, en inglés y en español, con los 5 ítems visibles |
| F4: `/login` y `/register` con sesión | resuelto (#88) | redirigen (`Login.test.tsx`) |
| F4: cambiar de empresa | resuelto (#88, #100) | "Cambiar de empresa" en el menú de empresa, con 2+ membresías |
| F4: flags v7 de React Router | resuelto (#88) | `App.tsx:371` |
| F4: `/v1/auth/me` dos veces | era `StrictMode` en desarrollo (anotado en #85) | `meLoaded` no se persiste: una carga por página en producción |
| F4: el botón del SupportBot tapa contenido | resuelto (#88) | espacio inferior reservado en los contenedores con scroll |

Las cifras del prompt (CompanyNew de 2040 líneas, 547 claves, Home.tsx) son del
código anterior a esos PRs. Hoy CompanyNew tiene 1310 líneas y Home no existe.

## Hallazgos nuevos

**A. `main` está en rojo desde `93c24f5`.** Ese commit ("restore navigable
directory picker after upstream merge") volvió a poner el desplegable viejo de
`/v1/browse_fs` en `CompanyNew.tsx`:
- ese endpoint ya no existe en el backend (se quitó en #111 porque el backend,
  dentro de Docker, no ve el disco del usuario), así que "Examinar" recibe un 404;
- usa claves de i18n que #111 había quitado (`companyNew.directoryError`,
  `companyNew.unknownError`, `companyNew.close`) y textos fijos ("⬆ Subir",
  "Entrar") y `http://localhost:8000` fijo;
- reemplazó el diálogo nativo de Electron.

Fallan 5 tests (4 de `CompanyNew.test.tsx` y la cobertura de i18n), y con ellos
los workflows Frontend Build y Frontend Tests en `main`. Sospecho que el selector
nuevo mostró "daemon no conectado" porque el daemon no estaba corriendo, y que el
backend que estaba en ejecución todavía tenía `browse_fs` cargado en memoria.
**Necesita decisión**: ver pregunta 1.

**B. Código muerto.** `components/ControlPanel.tsx` y `pages/SprintBoard.tsx` no
se montan en ninguna ruta. `useSearchMemory`, `useTickets`, `useReportBug` y
`api.startSprint` no se usan.

**C. `approve_task` está retirado.** `POST /v1/approve_task` devuelve 410 a
propósito: la aprobación humana pasó a la evidencia, con
`POST /v1/agent_runs/{id}/approve` y la regla de POSITIONING de que se aprueba
una corrida verificada, no una tarea. El "aprobar/rechazar tarea desde el board"
de la fase 5 no puede usar ese endpoint. Propuesta: en el board, un enlace
"Revisar evidencia" a `/evidence/:runId` desde la insignia de veredicto que ya
existe, o aprobar la última corrida de la tarea desde el board con el endpoint de
corridas.

## Brechas priorizadas

| # | Brecha | Alcance | Propuesta |
|---|---|---|---|
| 1 | `main` en rojo por `browse_fs` (hallazgo A) | chico | decisión pendiente (pregunta 1) |
| 2 | `POST /v1/sprints/{id}/dispatch` sin control | chico | botón "Despachar tareas pendientes" junto a Pausar/Reanudar en la pestaña Tablero (fase 5) |
| 3 | `POST /v1/memory/store` sin control | chico | "Guardar en memoria" en `MemorySearch` (fase 5) |
| 4 | Clientes de soporte con paths inexistentes | chico | corregir y usarlos desde `SupportBot`, o borrarlos (fase 5) |
| 5 | Código muerto (hallazgo B) | chico | borrar `ControlPanel`, `SprintBoard` y los hooks sin uso |
| 6 | Aprobar desde el board | chico | con el endpoint de corridas, no con `approve_task` (hallazgo C) |
| 7 | `eslint-plugin-i18next` | medio | requiere introducir ESLint en el front: no hay configuración |
| 8 | `POST /v1/company/invite` sin UI | medio-grande | invitaciones desde el menú de empresa (Owner); **espera confirmación** |
| 9 | Integraciones GitHub (`bindings`, `backfill`) sin UI | grande | página de integraciones por empresa; **espera confirmación** |
| 10 | `GET /v1/enterprise/lineage` sin UI | medio | linaje por archivo desde `EvidenceDetail` o la tarea; **espera confirmación** |
| 11 | `GET /v1/support/logs` y `PATCH /v1/support/tickets/{n}` sin UI | medio | vista de mesa de ayuda para administradores; **espera confirmación** |
| 12 | `GET /health/deep` sin UI | chico | detalle por servicio al pasar el mouse sobre el indicador "Online" |

## Cambios de API que propondría (sin tocarlos)

- Un endpoint de una sola tarea (`GET /v1/backlog/{item_id}`), para que `/task/:taskId`
  no tenga que resolver la tarea a través del board del sprint activo.
- Listar las membresías invitadas y pendientes, si se construye la UI de invitaciones.
