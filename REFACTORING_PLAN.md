# 🚀 Plan Maestro: La Fabrique Enterprise v2.0

Este documento contiene la estrategia de refactorización y mejora para el ecosistema de La Fabrique. 
Sigue las fases en orden para garantizar la estabilidad del sistema.

---

## 🏛️ Fase 1: Cimientos de Acero (Backend Refactor)
**Objetivo:** Salir del `main.py` gigante y pasar a una arquitectura modular y tipada.

1.  **Migración a SQLModel:** Reemplazar `psycopg2` pelado por **SQLModel** (SQLAlchemy + Pydantic). 
    *   Crear modelos para: `Company`, `Agent`, `Sprint`, `BacklogItem`, `AuditLog`, `BusinessUnit`.
2.  **Modularización por Routers:** Dividir `main.py` en la carpeta `backend/app/routers/`:
    *   `auth.py`: Registro y login de humanos.
    *   `enterprise.py`: Gestión de compañías, cuotas y Business Units.
    *   `sprint.py`: Board, backlog y sprints.
    *   `agents.py`: Orquestación de LangGraph y Glassroom (SSE).
    *   `support.py`: Tickets de soporte (`bug_reports`) y logs.
3.  **Capa de Servicios:** Mover la lógica de negocio pesada a `backend/app/services/`:
    *   `outbox_service.py`: Procesamiento de eventos NATS confiable.
    *   `agent_service.py`: Ejecución de grafos de LangGraph.
    *   `discovery_service.py`: Detección automática de stacks tecnológicos.

---

## 🧠 Fase 2: Inteligencia Auto-Sanable (LangGraph Evolution)
**Objetivo:** Agentes con bucles de retroalimentación y memoria persistente.

1.  **Bucle de Feedback (Self-Correction):** 
    *   Modificar `backend/agents/graph.py`.
    *   Si `real_execution_node` falla, volver a `generate_code_node` con el error de los tests.
2.  **Validación de Salida (Guardrails):** 
    *   Asegurar que el Arquitecto genere Mermaid válido antes de pasar al Desarrollador.
3.  **RAG sobre Grafos (Neo4j):** 
    *   Mejorar la consulta de "Engrams" para que el agente use decisiones pasadas en el contexto actual.

---

## 🎨 Fase 3: Frontend de Alta Performance (React Upgrade)
**Objetivo:** UI reactiva, liviana y con mejor manejo de estado.

1.  **Zustand Store:** 
    *   Reemplazar `AppContext.tsx` con un store centralizado en `frontend/src/store/useAppStore.ts`.
2.  **TanStack Query (React Query):** 
    *   Implementar hooks para todas las llamadas a `/v1/`. 
    *   Caching automático de sprints y backlog.
3.  **Virtualización:** 
    *   Usar `@tanstack/react-virtual` para el feed del Glassroom y listas largas de tareas.

---

## 🛡️ Fase 4: Governance y Observabilidad
**Objetivo:** Control de costos y trazabilidad total.

1.  **Cuotas en Tiempo Real:** 
    *   Validar `token_quota` en cada llamada al LLM Proxy. Frenar la ejecución si se supera el límite de la Business Unit.
2.  **OpenTelemetry Tracing:** 
    *   Instrumentar los nodos de LangGraph para ver la latencia y fallos en Jaeger/SigNoz.
3.  **Human-in-the-Loop:** 
    *   Mejorar el endpoint `/approve_task` para permitir feedback textual del PO humano que el Agente deba procesar.

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
