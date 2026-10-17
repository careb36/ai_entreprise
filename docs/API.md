# API and integration guide

The backend is a FastAPI service. Its interactive API explorer is available at
[`/docs`](http://localhost:8000/docs) when the stack is running; FastAPI also
serves the live schema at `/openapi.json`. The checked-in [`openapi.yaml`](../openapi.yaml)
is regenerated from that same app during backend CI. It is JSON-formatted YAML
(JSON is a valid YAML document), so no additional generator dependency is needed.

## Getting started

Use `http://localhost:8000` as the local API base URL. Most examples below assume
that the caller has registered and logged in:

```sh
export API=http://localhost:8000
export TOKEN='<access_token returned by /v1/auth/login>'
```

Register and obtain a bearer token:

```sh
curl -X POST "$API/v1/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","full_name":"API Developer","password":"change-me"}'

curl -X POST "$API/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"change-me"}'
```

Use `-H "Authorization: Bearer $TOKEN"` on authenticated requests. The API
enforces company membership and roles when authentication is enabled (the
default); use the `company_id` returned by `/v1/auth/me` or company initialization.
`Owner` can invite members, `Manager` can change company data, and `Viewer` can
read company-scoped data. The Glassroom SSE endpoint is public because browser
`EventSource` cannot set an Authorization header.

## Endpoint examples

These examples cover every HTTP operation in the generated OpenAPI document.
Request and response schemas for each operation are also available from Swagger
UI and `openapi.yaml`. Replace IDs and sample values with those from your account.

### Health, authentication, and company setup

```sh
# GET /health
curl "$API/health"

# POST /v1/auth/register
curl -X POST "$API/v1/auth/register" -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","full_name":"API Developer","password":"change-me"}'

# POST /v1/auth/login
curl -X POST "$API/v1/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"change-me"}'

# GET /v1/auth/me
curl "$API/v1/auth/me" -H "Authorization: Bearer $TOKEN"

# POST /v1/company/invite
curl -X POST "$API/v1/company/invite" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_id":1,"email":"teammate@example.com","human_role":"Viewer"}'

# POST /v1/init_company
curl -X POST "$API/v1/init_company" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"Example Co","default_scope_size":"M"}'

# POST /v1/approve_task (retired; returns 410)
curl -X POST "$API/v1/approve_task"

# GET /v1/enterprise/quotas
curl "$API/v1/enterprise/quotas?company_id=1" -H "Authorization: ******"

# POST /v1/enterprise/quotas/{bu_id}/reset
curl -X POST "$API/v1/enterprise/quotas/1/reset" -H "Authorization: ******"
```

### Sprints, backlog, and analytics

```sh
# POST /v1/plan_sprint
curl -X POST "$API/v1/plan_sprint" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"company_id":1,"prompt":"Add an API health dashboard","scope_size":"S"}'

# POST /v1/plan_self_improvement_sprint
curl -X POST "$API/v1/plan_self_improvement_sprint" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"company_id":1,"scope_size":"S"}'

# POST /v1/start_sprint
curl -X POST "$API/v1/start_sprint" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_id":1,"sprint_name":"API examples","start_date":"2026-10-17","scope_size":"S","backlog_items":[{"title":"Document endpoint","description":"Add an API usage example","priority":"medium","assignee":"Developer"}]}'

# GET /v1/sprints/{sprint_id}/board
curl "$API/v1/sprints/1/board" -H "Authorization: Bearer $TOKEN"

# PATCH /v1/backlog/{item_id}
curl -X PATCH "$API/v1/backlog/1" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"in_progress","comment":"Starting work"}'

# POST /v1/sprints/{sprint_id}/dispatch
curl -X POST "$API/v1/sprints/1/dispatch" -H "Authorization: Bearer $TOKEN"

# GET /v1/analytics/summary
curl "$API/v1/analytics/summary?sprint_id=1" -H "Authorization: Bearer $TOKEN"

# GET /v1/analytics/orchestration_health
curl "$API/v1/analytics/orchestration_health?company_id=1&weeks=8" \
  -H "Authorization: Bearer $TOKEN"
```

### Agents, audit, memory, and model catalogue

```sh
# POST /v1/generate (requires AGENT_GRAPH_ENABLED=true)
curl -X POST "$API/v1/generate" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_id":1,"story":"Add a health endpoint","constraints":"Keep the change small"}'

# POST /v1/audit
curl -X POST "$API/v1/audit?company_id=1" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"interaction_id":"example-run","action":"review","result":{"status":"complete"}}'

# GET /v1/audit/verify
curl "$API/v1/audit/verify?interaction_id=example-run&company_id=1" \
  -H "Authorization: Bearer $TOKEN"

# GET /v1/memory/search
curl --get "$API/v1/memory/search" --data-urlencode 'query=API authentication' \
  --data-urlencode 'company_id=1' -H "Authorization: Bearer $TOKEN"

# POST /v1/memory/store (content is a query parameter; metadata is the JSON body)
curl -X POST "$API/v1/memory/store?content=API%20integration%20notes&company_id=1" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"source":"developer-guide"}'

# GET /v1/models
curl "$API/v1/models"

# POST /v1/discover_team
curl -X POST "$API/v1/discover_team" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"project_path":"/workspace/project","company_id":1}'

# GET /v1/enterprise/lineage
curl --get "$API/v1/enterprise/lineage" --data-urlencode 'file_path=backend/app/main.py' \
  --data-urlencode 'company_id=1' -H "Authorization: Bearer $TOKEN"
```

### Agent-run evidence and governance

```sh
# POST /v1/agent_runs
curl -X POST "$API/v1/agent_runs" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_id":1,"external_ref":"ci:build-123","producer":"github-actions","artifact":"Build passed"}'

# GET /v1/agent_runs
curl "$API/v1/agent_runs?company_id=1&producer=github-actions&limit=50" \
  -H "Authorization: Bearer $TOKEN"

# GET /v1/agent_runs/{run_id}
curl "$API/v1/agent_runs/1?company_id=1" -H "Authorization: Bearer $TOKEN"

# POST /v1/agent_runs/{run_id}/verify
curl -X POST "$API/v1/agent_runs/1/verify" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_id":1,"evidence":{"source":"ci","conclusion":"success"}}'

# GET /v1/agent_runs/{run_id}/verifications
curl "$API/v1/agent_runs/1/verifications?company_id=1" -H "Authorization: Bearer $TOKEN"

# POST /v1/agent_runs/{run_id}/approve
curl -X POST "$API/v1/agent_runs/1/approve" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_id":1,"approver_name":"API Developer","decision":"approved"}'

# GET /v1/agent_runs/{run_id}/approvals
curl "$API/v1/agent_runs/1/approvals?company_id=1" -H "Authorization: Bearer $TOKEN"
```

### Support and GitHub integration

```sh
# POST /v1/support/report_bug
curl -X POST "$API/v1/support/report_bug" -H 'Content-Type: application/json' \
  -d '{"title":"Example issue","description":"The example endpoint returned an error","severity":"low"}'

# GET /v1/support/tickets
curl "$API/v1/support/tickets?status=open&limit=20" -H "Authorization: Bearer $TOKEN"

# GET /v1/support/tickets/{ticket_num}
curl "$API/v1/support/tickets/FAB-00001" -H "Authorization: Bearer $TOKEN"

# PATCH /v1/support/tickets/{ticket_num}
curl -X PATCH "$API/v1/support/tickets/FAB-00001" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"in_progress","assigned_to":"support"}'

# POST /v1/support/chat
curl -X POST "$API/v1/support/chat" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"How do I create a sprint?"}]}'

# GET /v1/support/logs
curl "$API/v1/support/logs" -H "Authorization: Bearer $TOKEN" -o audit.jsonl

# POST /v1/integrations/github/bindings
curl -X POST "$API/v1/integrations/github/bindings" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"company_id":1,"owner":"example-org","repo":"example-repo","webhook_secret":"<secret>","inclusion_rule":"all"}'

# POST /v1/integrations/github/webhook (GitHub normally signs and sends this request)
curl -X POST "$API/v1/integrations/github/webhook" \
  -H 'X-GitHub-Event: pull_request' -H 'X-Hub-Signature-256: sha256:<signature>' \
  -H 'Content-Type: application/json' \
  -d '{"action":"opened","repository":{"full_name":"example-org/example-repo"},"pull_request":{"number":1}}'

# POST /v1/integrations/github/backfill
curl -X POST "$API/v1/integrations/github/backfill" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"company_id":1,"owner":"example-org","repo":"example-repo"}'
```

### Live Glassroom events (SSE)

```sh
# GET /v1/glassroom/stream — -N disables curl buffering
curl -N "$API/v1/glassroom/stream?sprint_id=1"
```

The response uses `Content-Type: text/event-stream`; each message is JSON in a
`data:` line followed by a blank line. The first payload is a
`stream.connected` event. Historical events are replayed before live messages;
the stream emits `{"ping":true}` every 15 seconds to keep the connection alive.
If NATS is offline, the stream emits `stream.unavailable` and remains open.
Events do not use named SSE `event:` fields, so clients should parse the JSON
from each `data` event. Example:

```text
data: {"type":"status","semantic_type":"task.progress","source":"task_worker","semantic_version":1,"event_id":"<uuid>","sprint_id":"1","task_id":7,"task":"Add API docs","message":"Gathering context","timestamp":"2026-10-17T18:00:00+00:00"}
```

`semantic_type` is the stable event discriminator when present; not every legacy
event has every field. Current types include:

| Event family | `semantic_type` examples | Meaning |
|---|---|---|
| Stream | `stream.connected`, `stream.unavailable` | Connection established or NATS unavailable |
| Task lifecycle | `task.queued`, `task.claimed`, `task.progress`, `task.blocked`, `task.recovered`, `task.error` | Assignment, work progress, or failure |
| Review | `task.review_started`, `task.review_handoff`, `task.approved`, `task.rework_scheduled`, `task.review_error` | Review and rework flow |
| Context and output | `task.memory_retrieved`, `task.architect_consult_started`, `task.architect_consulted`, `task.deliverable_ready`, `task.artifact_generation` | Context gathering, consultation, and output |
| History and run | `task.history_comment`, `task.status_snapshot`, `assistant.history_context`, `assistant.telemetry`, `sprint.retrospective` | Replayed history and summary events |
| LangGraph | `kickoff.started`, `kickoff.agent_started`, `kickoff.agent_message`, `kickoff.agent_completed`, `kickoff.agent_failed`, `kickoff.needs_split`, `kickoff.completed`, `hitl.rejection_received`, `quota.warning` | Optional internal graph workflow |

## NATS event contracts

NATS messages are UTF-8 JSON. `task.assign` and `workspace.generate` are
published by backend workers/outbox; the local VS Code daemon subscribes to
`workspace.generate` and `workspace.execute`. The backend bridges
`glassroom.<sprint_id>` messages into SSE. `task.update`, `task.progress`, and
`audit.write` are also listed in the NATS client subject catalog; they are not
required for the HTTP integration flow.

| Subject | Purpose |
|---|---|
| `task.assign` | Assign a backlog item to an agent worker |
| `workspace.generate` | Ask the daemon or agent worker to generate workspace files |
| `workspace.execute` | Ask the daemon to run a command in a workspace |
| `glassroom.<sprint_id>` | Publish activity for one sprint; bridged to SSE |
| `system.maintenance.create_ticket` | Internal system-error notification |
| `task.update`, `task.progress`, `audit.write`, `system.maintenance.*` | Additional catalogued/internal subjects |

Example assignment and workspace messages:

```json
{
  "subject": "task.assign",
  "message": {
    "backlog_item_id": 7,
    "interaction_id": "a7c8b932-92b9-4c89-8c48-3e40f4fcb35f",
    "agent": "Developer",
    "sprint_id": 1
  }
}
```

```json
{
  "company_id": 1,
  "sprint_id": 1,
  "story": "Add an API health dashboard",
  "constraints": "Keep the change small"
}
```

`workspace.execute` accepts a `command`, optional `workspace_root`, and
optional `sprint_id`. It replies to NATS request/reply when a reply subject is
set; non-assistant command results are also summarized to `glassroom.<sprint_id>`.
Use a restricted workspace and do not pass untrusted shell commands.

## HTTP status and error meanings

Error responses generally use FastAPI's `{"detail": ...}` shape. Validation
details (422), quota/upstream errors, and some integration errors may return a
structured `detail` value instead of a string. The API does not currently
promise a separate stable machine-readable error code; branch on HTTP status.

| Status | Meaning / common causes |
|---|---|
| `400 Bad Request` | Invalid request data or invalid state transition |
| `401 Unauthorized` | Invalid login credentials or invalid/missing GitHub webhook signature |
| `403 Forbidden` | Authenticated user lacks the required company role or resource ownership |
| `404 Not Found` | Company, sprint, ticket, agent run, or repository binding does not exist |
| `409 Conflict` | Repository already bound, or internal graph disabled (`AGENT_GRAPH_ENABLED=false`) |
| `410 Gone` | Retired `POST /v1/approve_task`; use `POST /v1/agent_runs/{run_id}/approve` |
| `413 Content Too Large` | Ingested agent artifact exceeds the 2 MiB limit |
| `422 Unprocessable Entity` | Request schema validation failed or sprint scope/limits are invalid |
| `500 Internal Server Error` | Unexpected backend error or analytics query failure |
| `502 Bad Gateway` | An upstream LLM proxy or GitHub API request failed |
| `503 Service Unavailable` | Authentication, memory, model catalogue, or LLM service is not configured/available |

## Integrating a new producer

The preferred integration for external coding agents is the agent-run evidence
API, not the optional internal LangGraph producer:

1. Register/login and obtain a token. Initialize or select a company, then use
   its `company_id` for all evidence.
2. POST the producer's run to `/v1/agent_runs` with `external_ref`, `producer`,
   `artifact`, and optional `model`, `agent_role`, `subject`, and `claims`.
   The server computes artifact digests; do not submit or trust caller-supplied
   hashes or signatures.
3. Query `/v1/agent_runs?company_id=...` or
   `/v1/agent_runs/{run_id}?company_id=...` to retrieve the stored run.
4. Submit independent observations with
   `/v1/agent_runs/{run_id}/verify`. Keep producer claims separate from
   verification evidence; an agent's statement that its tests passed is a claim,
   not an observation.
5. Record a human decision using `/v1/agent_runs/{run_id}/approve`. Approval
   history is append-only; rejection feedback is recorded and does not trigger
   an automatic redispatch.

For local backend development, install the locked dependencies from `backend/`
with Poetry, set up `.env` from `.env.example`, and start services with
`docker compose up -d`. Run the API with `cd backend && PYTHONPATH=.:app
poetry run uvicorn main:app --reload --host 0.0.0.0 --port 8000`. Run tests with
`cd backend && PYTHONPATH=.:app poetry run pytest tests -q`. After changing a
route or schema, regenerate the committed reference with
`cd backend && PYTHONPATH=.:app poetry run python scripts/generate_openapi.py`;
backend CI repeats generation and fails if `openapi.yaml` is stale.
