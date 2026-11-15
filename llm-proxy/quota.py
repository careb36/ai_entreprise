"""
Per-business-unit token quotas, kept in the platform's Postgres.

A request that names a business unit (``X-Business-Unit-ID``) is refused with
429 once the unit has used its limit, answers carry ``X-Quota-Warning`` from
80 % of it, and every served or failed call is charged afterwards. A database
that cannot be reached never blocks a request: the failure is logged and the
request goes on.
"""

import logging
import os

from flask import jsonify, request
from sqlalchemy import create_engine, text

logger = logging.getLogger("llm-proxy")

_QUOTA_QUERY = "SELECT token_quota_limit, token_quota_used FROM business_units WHERE id = :id"


def _build_database_url() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "factory")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db = os.getenv("POSTGRES_DB", "factory")
    # 2.1 defaults to psycopg3; this image ships psycopg2.
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


engine = create_engine(_build_database_url(), pool_pre_ping=True)


def enforce_business_unit_quota():
    """``before_request``: refuse a business unit that has used its quota."""
    if request.path == "/health":
        return None
    bu_id = request.headers.get("X-Business-Unit-ID")
    if not bu_id:
        return None
    try:
        with engine.connect() as conn:
            res = conn.execute(text(_QUOTA_QUERY), {"id": bu_id}).fetchone()
            if not res:
                return jsonify({"error": "Business Unit not found"}), 404
            limit, used = res
            if limit > 0 and used >= limit:
                return jsonify({"error": "Quota exceeded", "used": used, "limit": limit}), 429
    except Exception as e:
        logger.error(f"Quota check failed: {e}")
    return None


def add_quota_warning_header(response):
    """``after_request``: add X-Quota-Warning when a business unit has consumed ≥ 80 % of its limit."""
    bu_id = request.headers.get("X-Business-Unit-ID")
    if not bu_id:
        return response
    try:
        with engine.connect() as conn:
            res = conn.execute(text(_QUOTA_QUERY), {"id": bu_id}).fetchone()
            if res:
                limit, used = res
                if limit > 0 and used >= limit * 0.8:
                    response.headers["X-Quota-Warning"] = "true"
                    response.headers["X-Quota-Used"] = str(used)
                    response.headers["X-Quota-Limit"] = str(limit)
    except Exception as e:
        logger.error(f"Quota warning check failed: {e}")
    return response


def track_usage_in_db(bu_id, tokens):
    """Track token usage in the database for a business unit."""
    if not bu_id or not tokens:
        return
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE business_units SET token_quota_used = token_quota_used + :tokens WHERE id = :id"),
                {"tokens": tokens, "id": bu_id},
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to track usage in DB: {e}")
