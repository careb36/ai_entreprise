"""Core service health endpoints."""

from datetime import datetime

from fastapi import APIRouter

router = APIRouter(tags=["Support"])


@router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}
