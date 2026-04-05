"""
Public re-export surface for all FastAPI routers.

Import these objects in ``main.py`` via ``from routers import ...`` instead of
importing each router module individually.  This keeps ``main.py`` clean and
makes it easy to rename or split router modules in the future.
"""

from routers.auth import router as auth_router
from routers.enterprise import router as enterprise_router
from routers.sprint import router as sprint_router
from routers.agents import router as agents_router
from routers.support import router as support_router

__all__ = [
    "auth_router",
    "enterprise_router",
    "sprint_router",
    "agents_router",
    "support_router",
]
