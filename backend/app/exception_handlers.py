"""
Global exception handlers for the Fabrique Enterprise API.

An unhandled exception answers 500 with an error id and nothing else
(WSTG-v42-ERRH-01, ERRH-02): no traceback, no exception text, no path. The
traceback goes to the backend's own log under that id, where an operator
finds it. Two optional notices carry a redacted report, never the traceback,
the message, a file path or a request body (issue #541):

- ``system.maintenance.create_ticket`` on NATS, the internal bus;
- a POST to ``ERROR_REPORT_URL``, off unless the operator sets it (https only).

A report holds the error id, the exception's class, the route template
(``/v1/sprints/{sprint_id}``, never the concrete path or query) and the
method, plus ``ERROR_REPORT_SOURCE`` when set.
"""

import json
import secrets
import traceback

from config import settings
from fastapi import Request
from fastapi.responses import JSONResponse
from logging_config import logger
from security import apply_security_headers

TICKET_SUBJECT = "system.maintenance.create_ticket"
REPORT_TIMEOUT_SECONDS = 2.0


def _route_template(request: Request) -> str:
    """The matched route's template, or ``unmatched``; never the raw path."""
    route = request.scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) else "unmatched"


def error_report(request: Request, exc: Exception, error_id: str) -> dict:
    """The redacted report of one unhandled error."""
    report = {
        "error_id": error_id,
        "error_type": type(exc).__name__,
        "method": request.method,
        "route": _route_template(request),
    }
    source = (settings.ERROR_REPORT_SOURCE or "").strip()
    if source:
        report["source"] = source[:100]
    return report


async def _post_report(report: dict) -> None:
    """POST *report* to ``ERROR_REPORT_URL`` when the operator set one."""
    url = (settings.ERROR_REPORT_URL or "").strip()
    if not url:
        return
    if not url.startswith("https://"):
        logger.warning("ERROR_REPORT_URL is not https: the error report is not sent")
        return
    try:
        from services.integrations import webhook_client

        body = json.dumps(report, sort_keys=True).encode()
        await webhook_client.post_json(url, body, REPORT_TIMEOUT_SECONDS)
    except Exception as report_exc:  # noqa: BLE001 - Reporting must not replace the original error.
        logger.warning(
            "error report %s not sent: %s",
            report["error_id"],
            type(report_exc).__name__,
        )


async def _publish_ticket(request: Request, report: dict) -> None:
    nats = getattr(request.app.state, "nats_client", None)
    if not nats:
        return
    try:
        await nats.publish(
            TICKET_SUBJECT,
            json.dumps(
                {
                    "subject": f"[SYSTEM ERROR] {report['error_type']} on {report['route']}",
                    "priority": "critical",
                    **report,
                },
                sort_keys=True,
            ).encode(),
        )
    except Exception:  # noqa: BLE001, S110 - Ticket publishing is best-effort during error handling.
        pass


def register_exception_handlers(app):
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Log the traceback under an error id; answer 500 with the id only."""
        error_id = secrets.token_hex(8)
        logger.error(
            "unhandled %s on %s %s [error_id=%s]\n%s",
            type(exc).__name__,
            request.method,
            _route_template(request),
            error_id,
            "".join(traceback.format_exception(exc))[-4000:],
        )
        report = error_report(request, exc, error_id)
        await _publish_ticket(request, report)
        await _post_report(report)
        return apply_security_headers(
            JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "error_id": error_id},
            )
        )
