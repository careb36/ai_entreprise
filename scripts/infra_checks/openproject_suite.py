"""OpenProject: authenticate, create a work package, then close it again."""

from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional
from uuid import uuid4

from .report import Suite, hint, timed


def _basic_auth(api_key: str) -> str:
    return "Basic " + base64.b64encode(f"apikey:{api_key}".encode()).decode()


def _op_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": _basic_auth(token),
        "Content-Type": "application/json",
        "Accept":        "application/hal+json",
    }


def check_openproject(suite: Suite) -> None:
    try:
        import httpx
    except ImportError:
        suite.add("httpx import", False,
                  "httpx package not installed.\n"
                  "Run: pip install httpx")
        return

    base  = os.getenv("OPENPROJECT_URL",      "http://localhost:8080").rstrip("/")
    token = os.getenv("OPENPROJECT_TOKEN_PO", "")

    if not token:
        suite.add("Credentials present", False,
                  "OPENPROJECT_TOKEN_PO is empty.")
        hint("OPENPROJECT_TOKEN_PO",
             "OpenProject → My Account → Access Tokens → API key")
        hint("OPENPROJECT_URL",
             "Base URL of your OpenProject instance (no trailing slash)")
        return

    suite.add("Credentials present", True,
              f"URL={base}  TOKEN={'*' * 6}{token[-4:]}")

    # ── 3a. GET /api/v3/projects — auth + connectivity ─────────────────────
    project_id: Optional[int] = None
    try:
        with httpx.Client(timeout=10) as client:
            resp, ms = timed(lambda: client.get(
                f"{base}/api/v3/projects",
                headers=_op_headers(token),
            ))

        if resp.status_code == 200:
            projects = resp.json().get("_embedded", {}).get("elements", [])
            if projects:
                project_id = projects[0]["id"]
                proj_names = ", ".join(f"#{p['id']} {p.get('name','?')}" for p in projects[:3])
                suite.add("GET /api/v3/projects", True,
                          f"Found {len(projects)} project(s): {proj_names}", ms)
            else:
                suite.add("GET /api/v3/projects", True,
                          "Auth OK — but no projects exist yet. "
                          "Create a project in OpenProject first.", ms)
                hint("—", "Create at least one project in OpenProject so WP tests can run")
        elif resp.status_code == 401:
            suite.add("GET /api/v3/projects", False,
                      "401 Unauthorized — token rejected.")
            hint("OPENPROJECT_TOKEN_PO",
                 "Make sure you copied the full API key, not an OAuth client secret")
            return
        elif resp.status_code == 403:
            suite.add("GET /api/v3/projects", False,
                      "403 Forbidden — token valid but lacks 'View projects' permission.")
            hint("OPENPROJECT_TOKEN_PO",
                 "Grant the API key user the 'Member' role in at least one project")
            return
        else:
            suite.add("GET /api/v3/projects", False,
                      f"HTTP {resp.status_code}: {resp.text[:300]}", ms)
            return
    except httpx.ConnectError as exc:
        suite.add("GET /api/v3/projects", False,
                  f"Cannot reach OpenProject at {base}:\n{exc}")
        hint("OPENPROJECT_URL",
             "Ensure OpenProject is running and the URL is reachable from this host")
        return
    except Exception as exc:
        suite.add("GET /api/v3/projects", False, str(exc))
        return

    if project_id is None:
        # No projects to test WP creation against — skip remaining checks.
        suite.add("POST work package (skipped)", False,
                  "Cannot create a Work Package without at least one project.")
        return

    # ── 3b. POST dummy Work Package ──────────────────────────────────────────
    wp_id: Optional[int] = None
    wp_subject = f"[VERIFY_INFRA] Automated test — {uuid4().hex[:8]}"

    payload: Dict[str, Any] = {
        "subject": wp_subject,
        "description": {"raw": "Created by `scripts/verify_infra.py`. Safe to delete."},
        "_links": {
            "project": {"href": f"/api/v3/projects/{project_id}"},
            "type":    {"href": "/api/v3/types/1"},    # 1 = Task (default)
            "status":  {"href": "/api/v3/statuses/1"}, # 1 = New (default)
        },
    }

    try:
        with httpx.Client(timeout=15) as client:
            resp, ms = timed(lambda: client.post(
                f"{base}/api/v3/work_packages",
                headers=_op_headers(token),
                json=payload,
            ))

        if resp.status_code in (200, 201):
            wp_data  = resp.json()
            wp_id    = wp_data.get("id")
            wp_url   = f"{base}/work_packages/{wp_id}"
            suite.add(
                "POST /api/v3/work_packages",
                True,
                f"Created WP #{wp_id}: {wp_subject!r}\n"
                f"URL: {wp_url}",
                ms,
            )
        elif resp.status_code == 403:
            suite.add("POST /api/v3/work_packages", False,
                      f"403 Forbidden — token lacks 'Create work packages' permission "
                      f"in project #{project_id}.\n{resp.text[:300]}")
            hint("OPENPROJECT_TOKEN_PO",
                 "Assign the API key user a role with 'Create work packages' permission")
            return
        elif resp.status_code == 422:
            suite.add("POST /api/v3/work_packages", False,
                      f"422 Unprocessable — OpenProject rejected the payload.\n"
                      f"{resp.text[:500]}\n"
                      "The default type/status IDs may differ in your installation.")
            return
        else:
            suite.add("POST /api/v3/work_packages", False,
                      f"HTTP {resp.status_code}: {resp.text[:400]}", ms)
            return
    except Exception as exc:
        suite.add("POST /api/v3/work_packages", False, str(exc))
        return

    if wp_id is None:
        return

    # ── 3c. PATCH — close / reject the dummy WP ──────────────────────────────
    # Fetch lock_version first (required for optimistic concurrency in OP v3).
    lock_version = 0
    try:
        with httpx.Client(timeout=10) as client:
            get_resp = client.get(
                f"{base}/api/v3/work_packages/{wp_id}",
                headers=_op_headers(token),
            )
        if get_resp.status_code == 200:
            lock_version = get_resp.json().get("lockVersion", 0)
    except Exception:
        pass  # proceed with lockVersion=0; worst case OP returns 409

    # Try status IDs in order: Closed (5) → Rejected (6) → Resolved (3)
    # Different installations may have different sets.
    closed = False
    for status_id, label in [(5, "Closed"), (6, "Rejected"), (3, "Resolved")]:
        patch_payload: Dict[str, Any] = {
            "lockVersion": lock_version,
            "_links": {
                "status": {"href": f"/api/v3/statuses/{status_id}"},
            },
        }
        try:
            with httpx.Client(timeout=10) as client:
                patch_resp, ms = timed(lambda: client.patch(
                    f"{base}/api/v3/work_packages/{wp_id}",
                    headers=_op_headers(token),
                    json=patch_payload,
                ))
            if patch_resp.status_code in (200, 201):
                new_status = (
                    patch_resp.json()
                    .get("_links", {})
                    .get("status", {})
                    .get("title", label)
                )
                suite.add(
                    f"PATCH WP #{wp_id} → {label}",
                    True,
                    f"WP #{wp_id} status is now '{new_status}' (lockVersion bumped)",
                    ms,
                )
                closed = True
                break
            elif patch_resp.status_code == 422:
                # Status transition not allowed — try next
                continue
            else:
                suite.add(
                    f"PATCH WP #{wp_id} → {label}",
                    False,
                    f"HTTP {patch_resp.status_code}: {patch_resp.text[:300]}",
                )
                break
        except Exception as exc:
            suite.add(f"PATCH WP #{wp_id} → {label}", False, str(exc))
            break

    if not closed:
        suite.add(
            "Cleanup (close WP)",
            False,
            f"Could not transition WP #{wp_id} to a closed state.\n"
            f"No standard closed-status (Closed/Rejected/Resolved) accepted the transition.\n"
            f"Please close WP #{wp_id} manually in OpenProject.",
        )
