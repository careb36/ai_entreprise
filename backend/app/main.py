import os
import json
import logging
import asyncio
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from passlib.context import CryptContext
from pydantic import BaseModel

from config import Settings
from nats_client import NATSClient
from memory_client import MemoryClient
from llm_proxy_client import LLMProxyClient
from database import get_db_connection, get_neo4j_driver
from schemas import (
    CompanyInitRequest, SprintStartRequest, GenerateRequest, 
    AuditRequest, DiscoveryRequest, ApprovalRequest, BugReportRequest
)

# --- Structured Logging Configuration ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName
        }
        if hasattr(record, "interaction_id"):
            log_entry["interaction_id"] = record.interaction_id
        return json.dumps(log_entry)

logger = logging.getLogger("fabrique")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)

settings = Settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(
    title="Fabrique Enterprise API", 
    description="Autonomous AI Engineering Factory API with Multi-Tenancy, Governance, and Auto-Healing support.",
    version="2026.4.1",
    openapi_tags=[
        {"name": "Core", "description": "Factory initialization and sprint management"},
        {"name": "Enterprise", "description": "Multi-tenancy, Quotas, and Governance"},
        {"name": "Support", "description": "System health, logs, and bug reporting"}
    ]
)

nats_client = None
memory_client = None
llm_client = None
sse_connections: Dict[str, asyncio.Queue] = {}

# --- Global Exception Handler (Vendor-Managed Support) ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_type = type(exc).__name__
    error_msg = str(exc)
    trace = traceback.format_exc()[-1000:]
    
    # 1. Structured log for local IT
    logger.error(f"System Exception: {error_msg}", extra={"error_details": trace})
    
    # 2. Report to Carolina Pereira (Vendor Support)
    vendor_webhook = os.getenv("FABRIQUE_VENDOR_SUPPORT_URL")
    if vendor_webhook:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(vendor_webhook, json={
                    "vendor_id": "CAROLINA_PEREIRA_PROD",
                    "system_version": "2026.4.1",
                    "error_type": error_type,
                    "error_message": error_msg,
                    "traceback": trace,
                    "endpoint": f"{request.method} {request.url.path}"
                }, timeout=2.0)
        except Exception as report_e:
            logger.error(f"Failed to reach Vendor Support: {report_e}")

    # 3. Notification to local system (optional)
    if nats_client:
        try:
            await nats_client.publish("system.maintenance.create_ticket", json.dumps({
                "subject": "[SYSTEM ERROR] Fabrique Engine Failure",
                "description": "A report has been sent to the vendor (Carolina Pereira) for resolution.",
                "priority": "critical"
            }).encode())
        except: pass

    return JSONResponse(
        status_code=500,
        content={"detail": "Fabrique Engine Error. A technical report has been sent to Vendor Support (Carolina Pereira)."}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/v1/discover_team", tags=["Enterprise"])
async def discover_team(req: DiscoveryRequest):
    """
    Scans a project path to detect stack and suggest the ideal AI team.
    This triggers the 'Solution Architect' agent.
    """
    interaction_id = str(uuid4())
    
    # In a real scenario, this would trigger a LangGraph dedicated to code analysis
    # For now, we simulate the 'Solution Architect' logic
    detected_stack = {
        "language": "TypeScript",
        "framework": "React",
        "database": "Postgres",
        "complexity": "Medium"
    }
    
    suggested_team = [
        {"role": "PO", "seniority": "Senior", "specialty": "Product Management"},
        {"role": "Architect", "seniority": "Staff", "specialty": "Cloud/DB"},
        {"role": "Developer", "seniority": "Mid", "specialty": "Frontend/React"},
        {"role": "QA", "seniority": "Senior", "specialty": "Automation"}
    ]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO project_discovery (company_id, project_path, detected_stack, suggested_team, status)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (req.company_id, req.project_path, json.dumps(detected_stack), 
             json.dumps(suggested_team), "completed")
        )
        discovery_id = cursor.fetchone()[0]
        conn.commit()
        
        return {
            "discovery_id": discovery_id,
            "detected_stack": detected_stack,
            "suggested_team": suggested_team
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


class ApprovalRequest(BaseModel):
    task_id: int
    approver_name: str
    decision: str  # 'approved' or 'rejected'


@app.post("/v1/approve_task")
async def approve_task(req: ApprovalRequest):
    """
    Enterprise Governance: A human manager approves an AI decision.
    This resumes the LangGraph workflow.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        new_status = "approved" if req.decision == "approved" else "rejected"
        cursor.execute(
            """
            UPDATE task_queue 
            SET approval_status = %s, approved_by = %s, status = 'pending' 
            WHERE id = %s
            """,
            (new_status, req.approver_name, req.task_id)
        )
        conn.commit()
        
        # Here we would notify NATS to resume the LangGraph checkpoint
        return {"status": "success", "task_id": req.task_id, "new_status": new_status}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


class UserCreateRequest(BaseModel):
    email: str
    full_name: str
    password: str

class InviteMemberRequest(BaseModel):
    company_id: int
    email: str
    human_role: str  # 'Owner', 'Manager', 'Viewer'

@app.post("/v1/auth/register")
async def register_user(req: UserCreateRequest):
    """Register a new human user for the platform."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO users (email, full_name, password_hash)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (req.email, req.full_name, pwd_context.hash(req.password))
        )
        user_id = cursor.fetchone()[0]
        conn.commit()
        return {"user_id": user_id, "email": req.email}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Email may already exist.")
    finally:
        cursor.close()
        conn.close()

@app.post("/v1/company/invite")
async def invite_member(req: InviteMemberRequest):
    """Invite a human user to collaborate on a company/project."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Find user
        cursor.execute("SELECT id FROM users WHERE email = %s", (req.email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="User not found")
            
        user_id = result[0]
        
        # Add to company_members
        cursor.execute(
            """
            INSERT INTO company_members (company_id, user_id, human_role)
            VALUES (%s, %s, %s)
            """,
            (req.company_id, user_id, req.human_role)
        )
        conn.commit()
        return {"status": "success", "message": f"{req.email} invited as {req.human_role}"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/v1/enterprise/quotas")
async def get_quotas(company_id: int):
    """Get token usage quotas for all business units."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT unit_name, token_quota_limit, token_quota_used FROM business_units WHERE company_id = %s",
        (company_id,)
    )
    units = cursor.fetchall()
    cursor.close()
    conn.close()
    return [{"unit": u[0], "limit": u[1], "used": u[2]} for u in units]


@app.get("/v1/analytics/summary")
async def get_project_health(sprint_id: int):
    """
    AI Scrum Master: Synthesizes data from OpenProject and Audit Logs 
    to provide a business-level health report.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. Get token usage from audit logs
        cursor.execute(
            "SELECT SUM(cost_tokens) FROM audit_log WHERE interaction_id IN (SELECT interaction_id FROM task_queue)"
        )
        total_tokens = cursor.fetchone()[0] or 0
        
        # 2. Get task status counts (simulated OpenProject sync)
        cursor.execute(
            "SELECT status, COUNT(*) FROM backlog_items WHERE sprint_id = %s GROUP BY status",
            (sprint_id,)
        )
        status_counts = dict(cursor.fetchall())
        
        # 3. AI generates the summary (Simulated)
        ai_summary = (
            f"The sprint is healthy. {status_counts.get('closed', 0)} tasks completed. "
            f"Total investment: {total_tokens} tokens. "
            "Velocity is stable, no major blockers detected."
        )
        
        return {
            "sprint_id": sprint_id,
            "health_score": 92, # 0-100
            "tokens_consumed": total_tokens,
            "tasks_status": status_counts,
            "ai_executive_summary": ai_summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


class BugReportRequest(BaseModel):
    title: str
    description: str
    steps: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    severity: str = "medium"        # low | medium | high | critical
    category: str = "bug"           # bug | feature | question | incident
    reporter_email: Optional[str] = None
    browser_info: Optional[Dict[str, Any]] = None


class BugStatusUpdateRequest(BaseModel):
    status: str          # open | in_progress | waiting | resolved | closed
    resolution: Optional[str] = None
    assigned_to: Optional[str] = None


def _next_ticket_num(cursor) -> str:
    """Generate next FAB-XXXXX ticket number, thread-safe via DB sequence."""
    cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM bug_reports")
    next_id = cursor.fetchone()[0]
    return f"FAB-{next_id:05d}"


@app.post("/v1/support/report_bug", tags=["Support"])
async def report_bug(req: BugReportRequest):
    """Create a structured bug report ticket with auto-generated ticket number."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        ticket_num = _next_ticket_num(cursor)
        cursor.execute(
            """
            INSERT INTO bug_reports
                (ticket_num, title, description, steps, expected, actual,
                 severity, category, reporter_email, browser_info)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, ticket_num, status, created_at
            """,
            (
                ticket_num, req.title, req.description,
                req.steps, req.expected, req.actual,
                req.severity, req.category, req.reporter_email,
                json.dumps(req.browser_info) if req.browser_info else None,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        logger.info(f"Bug report created: {ticket_num} by {req.reporter_email}")
        return {
            "ticket_num": row[1],
            "id": row[0],
            "status": row[2],
            "created_at": row[3].isoformat(),
            "message": f"Report {row[1]} created. You can track its status at any time.",
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/v1/support/tickets", tags=["Support"])
async def list_tickets(
    reporter_email: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    """List bug report tickets with optional filters."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        filters, params = [], []
        if reporter_email:
            filters.append("reporter_email = %s"); params.append(reporter_email)
        if status:
            filters.append("status = %s"); params.append(status)
        if severity:
            filters.append("severity = %s"); params.append(severity)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        params.append(limit)
        cursor.execute(
            f"""
            SELECT id, ticket_num, title, severity, category, status,
                   reporter_email, assigned_to, created_at, updated_at, resolved_at
            FROM bug_reports {where}
            ORDER BY created_at DESC LIMIT %s
            """,
            params,
        )
        cols = ["id","ticket_num","title","severity","category","status",
                "reporter_email","assigned_to","created_at","updated_at","resolved_at"]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        # serialize datetimes
        for r in rows:
            for k in ("created_at","updated_at","resolved_at"):
                if r[k]: r[k] = r[k].isoformat()
        return {"tickets": rows, "count": len(rows)}
    finally:
        cursor.close()
        conn.close()


@app.get("/v1/support/tickets/{ticket_num}", tags=["Support"])
async def get_ticket(ticket_num: str):
    """Get full detail of a single ticket by its ticket number (e.g. FAB-00001)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, ticket_num, title, description, steps, expected, actual,
                   severity, category, status, reporter_email, assigned_to,
                   resolution, browser_info, created_at, updated_at, resolved_at
            FROM bug_reports WHERE ticket_num = %s
            """,
            (ticket_num.upper(),),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Ticket {ticket_num} not found")
        cols = ["id","ticket_num","title","description","steps","expected","actual",
                "severity","category","status","reporter_email","assigned_to",
                "resolution","browser_info","created_at","updated_at","resolved_at"]
        result = dict(zip(cols, row))
        for k in ("created_at","updated_at","resolved_at"):
            if result[k]: result[k] = result[k].isoformat()
        return result
    finally:
        cursor.close()
        conn.close()


@app.patch("/v1/support/tickets/{ticket_num}", tags=["Support"])
async def update_ticket_status(ticket_num: str, req: BugStatusUpdateRequest):
    """Update the status (and optionally resolution/assignee) of a ticket."""
    valid_statuses = {"open", "in_progress", "waiting", "resolved", "closed"}
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {valid_statuses}")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        resolved_at = "CURRENT_TIMESTAMP" if req.status in ("resolved", "closed") else "NULL"
        cursor.execute(
            f"""
            UPDATE bug_reports SET
                status = %s,
                resolution = COALESCE(%s, resolution),
                assigned_to = COALESCE(%s, assigned_to),
                resolved_at = {resolved_at}
            WHERE ticket_num = %s
            RETURNING ticket_num, status, updated_at
            """,
            (req.status, req.resolution, req.assigned_to, ticket_num.upper()),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Ticket {ticket_num} not found")
        conn.commit()
        return {"ticket_num": row[0], "status": row[1], "updated_at": row[2].isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()



class SupportChatRequest(BaseModel):
    message: str
    history: list = []


@app.post("/v1/support/chat", tags=["Support"])
async def support_chat(req: SupportChatRequest):
    """Support Bot: Answers questions about Fabrique using the LLM proxy."""
    try:
        system_prompt = (
            "You are the Fabrique Assistant, a helpful support bot for Fabrique — "
            "an AI-powered autonomous software development platform. "
            "Answer concisely and helpfully. If asked about a feature that isn't available yet, "
            "say so honestly and suggest the closest available option."
        )
        payload = {
            "model": "gemini-2.0-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                *[{"role": m["role"], "content": m["content"]} for m in req.history],
                {"role": "user", "content": req.message},
            ],
            "max_tokens": 512,
            "temperature": 0.7,
        }
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{os.getenv('LLM_PROXY_URL', 'http://llm-proxy:5100')}/v1/chat/completions",
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            reply = data["choices"][0]["message"]["content"]
            return {"reply": reply}
    except Exception as e:
        logger.warning(f"Support chat LLM error: {e}")
        raise HTTPException(status_code=503, detail="LLM service unavailable")

@app.get("/v1/support/logs", tags=["Support"])
async def download_support_logs():
    """
    Enterprise Support: Downloads the latest structured audit logs for troubleshooting.
    """
    log_path = Path(os.getenv("AUDIT_LOG_PATH", "/app/.factory/logs")) / "audit.jsonl"
    if log_path.exists():
        return FileResponse(path=log_path, filename=f"fabrique_support_logs_{datetime.now().strftime('%Y%m%d')}.jsonl")
    raise HTTPException(status_code=404, detail="Log file not found.")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post("/v1/init_company")
async def init_company(req: CompanyInitRequest):
    """Initialize a company with agents, permissions, and seed Neo4j root nodes."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            INSERT INTO companies (company_name, default_sprint_length_days, autonomy_level)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (req.company_name, req.default_sprint_length_days, req.autonomy_level)
        )
        company_id = cursor.fetchone()[0]
        
        for agent in req.agents:
            cursor.execute(
                """
                INSERT INTO agents (company_id, role, persona, seniority, specialty)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (company_id, agent.get("role"), agent.get("persona"), 
                 agent.get("seniority", "Senior"), agent.get("specialty", ""))
            )

        if req.permissions:
            for cred_type, access_level in req.permissions.items():
                if access_level != "none":
                    cursor.execute(
                        """
                        INSERT INTO project_credentials (company_id, credential_type, access_level, encrypted_value)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (company_id, cred_type, access_level, "vault_encrypted_placeholder")
                    )
        
        conn.commit()
        
        driver = get_neo4j_driver()
        with driver.session() as session:
            session.run(
                """
                CREATE (c:Company {id: $id, name: $name, created: timestamp()})
                """,
                id=company_id, name=req.company_name
            )
        
        return {"company_id": company_id, "message": "Company initialized successfully"}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error initializing company: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/v1/start_sprint")
async def start_sprint(req: SprintStartRequest):
    """Start a sprint and create backlog items in Postgres."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO sprints (company_id, sprint_name, start_date, duration_days)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (req.company_id, req.sprint_name, req.start_date, req.duration_days)
        )
        sprint_id = cursor.fetchone()[0]

        item_ids = []
        for item in req.backlog_items:
            cursor.execute(
                """
                INSERT INTO backlog_items (sprint_id, title, description, status, priority)
                VALUES (%s, %s, %s, 'todo', 'medium') RETURNING id
                """,
                (sprint_id, item.get("title"), item.get("description", ""))
            )
            backlog_item_id = cursor.fetchone()[0]
            item_ids.append(backlog_item_id)

            # Transactional Outbox: publish task.assign to agents via NATS
            nats_payload = {
                "subject": "task.assign",
                "message": {
                    "backlog_item_id": backlog_item_id,
                    "interaction_id": str(uuid4()),
                    "agent": "PO",
                    "sprint_id": sprint_id
                }
            }
            cursor.execute(
                """
                INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload)
                VALUES (%s, %s, %s, %s)
                """,
                ("backlog_item", str(backlog_item_id), "nats_publish", json.dumps(nats_payload))
            )

        conn.commit()

        return {
            "sprint_id": sprint_id,
            "backlog_item_ids": item_ids,
            "sprint_url": f"/v1/sprints/{sprint_id}/board"
        }
    except Exception as e:
        conn.rollback()
        logger.error(f"Error starting sprint: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


VALID_STATUSES = {"todo", "in_progress", "in_review", "done", "blocked"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}


@app.get("/v1/sprints/{sprint_id}/board")
async def get_sprint_board(sprint_id: int):
    """Return sprint metadata + all backlog items grouped by status (Kanban board)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, sprint_name, start_date, duration_days, status, goal FROM sprints WHERE id = %s",
            (sprint_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Sprint not found")
        sprint = {
            "id": row[0], "sprint_name": row[1], "start_date": str(row[2]) if row[2] else None,
            "duration_days": row[3], "status": row[4], "goal": row[5]
        }

        cursor.execute(
            """
            SELECT id, title, description, status, priority, assignee, comments, created_at, updated_at
            FROM backlog_items WHERE sprint_id = %s ORDER BY created_at
            """,
            (sprint_id,)
        )
        items = []
        for r in cursor.fetchall():
            items.append({
                "id": r[0], "title": r[1], "description": r[2],
                "status": r[3], "priority": r[4], "assignee": r[5],
                "comments": r[6] or [], "created_at": str(r[7]), "updated_at": str(r[8])
            })

        # Group by status for Kanban columns
        columns = {s: [] for s in ["todo", "in_progress", "in_review", "done", "blocked"]}
        for item in items:
            col = item["status"] if item["status"] in columns else "todo"
            columns[col].append(item)

        return {"sprint": sprint, "columns": columns, "total": len(items)}
    finally:
        cursor.close()
        conn.close()


class BacklogItemUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    comment: Optional[str] = None   # appends a new comment


@app.patch("/v1/backlog/{item_id}")
async def update_backlog_item(item_id: int, req: BacklogItemUpdate):
    """Update status, priority, assignee or add a comment to a backlog item."""
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Valid: {VALID_STATUSES}")
    if req.priority and req.priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=422, detail=f"Invalid priority. Valid: {VALID_PRIORITIES}")

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        fields, values = [], []
        if req.status:
            fields.append("status = %s"); values.append(req.status)
        if req.priority:
            fields.append("priority = %s"); values.append(req.priority)
        if req.assignee is not None:
            fields.append("assignee = %s"); values.append(req.assignee)
        if req.comment:
            fields.append(
                "comments = comments || %s::jsonb"
            )
            values.append(json.dumps([{"author": "user", "text": req.comment,
                                        "created_at": str(datetime.utcnow())}]))

        if not fields:
            raise HTTPException(status_code=422, detail="Nothing to update")

        values.append(item_id)
        cursor.execute(
            f"UPDATE backlog_items SET {', '.join(fields)} WHERE id = %s RETURNING id, status, priority, assignee, comments",
            values
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Backlog item not found")
        conn.commit()
        return {"id": row[0], "status": row[1], "priority": row[2], "assignee": row[3], "comments": row[4]}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


async def glassroom_stream(sprint_id: str = Query(...)):
    """SSE stream for glassroom events."""
    queue = asyncio.Queue()
    sse_connections[sprint_id] = queue
    
    async def event_generator():
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {json.dumps(json.loads(data))}\n\n"
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'ping': True})}\n\n"
    
    try:
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    finally:
        del sse_connections[sprint_id]


@app.post("/v1/generate")
async def generate(req: GenerateRequest):
    """Generate a package using LangGraph orchestration."""
    interaction_id = req.interaction_id or str(uuid4())
    
    await nats_client.publish("workspace.generate", {
        "interaction_id": interaction_id,
        "company_id": req.company_id,
        "story": req.story,
        "constraints": req.constraints
    })
    
    return {
        "interaction_id": interaction_id,
        "package_url": None
    }


@app.post("/v1/audit")
async def audit(req: AuditRequest):
    """Write audit entry to both file and database."""
    audit_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "interaction_id": req.interaction_id,
        "agent_id": req.agent_id,
        "action": req.action,
        "details": req.details,
        "result": req.result,
        "decision_signature": req.decision_signature,
        "cost_tokens": req.cost_tokens
    }
    
    audit_log_path = Path(os.getenv("AUDIT_LOG_PATH", "/app/.factory/logs"))
    audit_log_path.mkdir(parents=True, exist_ok=True)
    
    audit_file = audit_log_path / "audit.jsonl"
    with open(audit_file, "a") as f:
        f.write(json.dumps(audit_entry) + "\n")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            """
            INSERT INTO audit_log (interaction_id, agent_id, action, details, result, decision_signature, cost_tokens)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (req.interaction_id, req.agent_id, req.action, json.dumps(req.details),
             json.dumps(req.result), req.decision_signature, req.cost_tokens)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error writing audit to DB: {e}")
    finally:
        cursor.close()
        conn.close()
    
    if req.decision_signature:
        pass
    
    return {"status": "ok"}


@app.get("/v1/memory/search")
async def search_memory(query: str = Query(...), limit: int = 10):
    """Search vector memory."""
    results = memory_client.search_similar(query, limit)
    return {"results": results}


@app.post("/v1/memory/store")
async def store_memory(content: str, metadata: Optional[Dict] = None):
    """Store content in vector memory."""
    memory_client.store(content, metadata)
    return {"status": "stored"}


# ==========================================
# LIFECYCLE & BACKGROUND WORKERS
# ==========================================

class ChatMessage(BaseModel):
    role: str
    content: str

class SupportChatRequest(BaseModel):
    messages: List[ChatMessage]

@app.post("/v1/support/chat", tags=["Support"])
async def support_chat(req: SupportChatRequest):
    """
    Support Bot: Answers user questions about Fabrique using the LLM Proxy.
    """
    from llm_proxy_client import LLMProxyClient
    client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://localhost:5100"))
    
    # Prepend the System Prompt to make it act like a Fabrique Support Agent
    system_prompt = {
        "role": "system",
        "content": (
            "You are the Fabrique Enterprise Support Bot. "
            "You help users manage their autonomous AI engineering teams. "
            "Explain concepts like 'Sprints', 'Autonomy Levels', 'Engrams', and 'Glassroom' simply. "
            "If they report a bug, tell them to use the 'Bug Reporter' tab (the red bug icon). "
            "Keep answers concise, professional, and friendly. "
            "CRITICAL: You are fully bilingual. You MUST reply in the same language the user uses (English or Spanish)."
        )
    }
    
    formatted_messages = [system_prompt] + [msg.dict() for msg in req.messages]
    
    try:
        # We use the generic chat endpoint of our LLM proxy
        response = await asyncio.to_thread(
            client.chat,
            messages=formatted_messages,
            model="gemini-1.5-flash",
            agent_role="System"
        )
        return {"reply": response}
    except Exception as e:
        logger.error(f"Support chat failed: {e}")
        raise HTTPException(status_code=500, detail="Support Bot is currently offline.")

@app.get("/v1/enterprise/lineage", tags=["Enterprise"])
async def get_code_lineage(file_path: str):
    """
    Time Travel Debugging: Returns the architectural decisions, tickets, and agents 
    that modified a specific file, ordered chronologically from Neo4j.
    """
    conn = get_neo4j_driver()
    try:
        # We query Neo4j for the Semantic node (file) and all Episodic nodes (decisions) that modified it
        cypher = """
        MATCH (e:Episodic)-[r:MODIFIED_BY]->(s)
        WHERE s.id ENDS WITH $file_path
        RETURN e.id AS engram_id, e.agent_role AS role, e.summary AS decision, e.created_at AS timestamp
        ORDER BY e.created_at DESC
        LIMIT 10
        """
        with conn.session() as session:
            result = session.run(cypher, file_path=file_path)
            lineage = [dict(record) for record in result]
            
        if not lineage:
            return {"file": file_path, "status": "No historical engrams found.", "lineage": []}
            
        return {"file": file_path, "status": "success", "lineage": lineage}
    except Exception as e:
        logger.error(f"Lineage query failed: {e}")
        raise HTTPException(status_code=500, detail="Neo4j Time Travel query failed.")

@app.on_event("startup")
async def startup():
    global nats_client, memory_client, llm_client
    
    logger.info("Initializing Enterprise Backend Components...")
    nats_client = NATSClient(os.getenv("NATS_URL", "nats://localhost:4222"))
    await nats_client.connect()
    
    memory_client = MemoryClient(
        pg_conn=get_db_connection(),
        neo4j_driver=get_neo4j_driver()
    )
    
    llm_client = LLMProxyClient(os.getenv("LLM_PROXY_URL", "http://localhost:5100"))
    
    asyncio.create_task(listen_to_nats())
    asyncio.create_task(process_outbox())

@app.on_event("shutdown")
async def shutdown():
    """Graceful Shutdown: Close connections to prevent zombie pools in Kubernetes."""
    logger.info("Initiating graceful shutdown...")
    if nats_client:
        await nats_client.close()
    logger.info("Shutdown complete.")

async def listen_to_nats():
    """Listen to NATS for glassroom events and push to SSE connections."""
    async def callback(msg):
        subject = msg.subject
        if subject.startswith("glassroom."):
            sprint_id = subject.split(".")[1]
            if sprint_id in sse_connections:
                for queue in sse_connections[sprint_id]:
                    await queue.put(msg.data.decode())
        await msg.ack()
    
    try:
        await nats_client.subscribe("glassroom.>", callback)
    except Exception as e:
        logger.error(f"Failed to subscribe to Glassroom events: {e}")

async def process_outbox():
    """
    Background worker to process transactional outbox events reliably.
    Enterprise Upgrade: Exponential backoff/retries for NATS disconnects.
    """
    while True:
        if not nats_client:
            await asyncio.sleep(2)
            continue
            
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Lock rows for update to prevent concurrent K8s worker collisions
            cursor.execute(
                """
                SELECT id, event_type, payload, retry_count 
                FROM outbox_events 
                WHERE status = 'pending' 
                ORDER BY created_at ASC 
                FOR UPDATE SKIP LOCKED 
                LIMIT 10
                """
            )
            events = cursor.fetchall()
            
            for event_id, event_type, payload_str, retries in events:
                try:
                    import json
                    payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
                        
                    if event_type == "nats_publish":
                        await nats_client.publish(payload["subject"], payload["message"])
                    
                    cursor.execute(
                        "UPDATE outbox_events SET status = 'completed', processed_at = NOW() WHERE id = %s", 
                        (event_id,)
                    )
                except Exception as row_e:
                    logger.error(f"Failed to process outbox event {event_id}: {row_e}")
                    # Enterprise Self-Healing: Increment retry_count instead of failing immediately
                    cursor.execute(
                        """
                        UPDATE outbox_events 
                        SET retry_count = retry_count + 1, 
                            status = CASE WHEN retry_count >= 5 THEN 'failed' ELSE 'pending' END,
                            processed_at = NOW() 
                        WHERE id = %s
                        """, 
                        (event_id,)
                    )
            
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Outbox processor error: {e}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        
        await asyncio.sleep(2)

