"""
Public re-export surface for all FastAPI routers.

Import these objects in ``main.py`` via ``from routers import ...`` instead of
importing each router module individually.  This keeps ``main.py`` clean and
makes it easy to rename or split router modules in the future.
"""

from routers.agent_runs import router as agent_runs_router
from routers.agents import router as agents_router
from routers.api_tokens import router as api_tokens_router
from routers.architectures import router as architectures_router
from routers.auth import router as auth_router
from routers.enterprise import router as enterprise_router
from routers.events import router as events_router
from routers.github import router as github_router
from routers.inbox import router as inbox_router
from routers.mcp import router as mcp_router
from routers.messages import router as messages_router
from routers.model_settings import router as model_settings_router
from routers.procedures import router as procedures_router
from routers.providers import router as providers_router
from routers.reviews import router as reviews_router
from routers.specifications import router as specifications_router
from routers.sprint import router as sprint_router
from routers.sprint_control import router as sprint_control_router
from routers.support import router as support_router
from routers.workspace import router as workspace_router

__all__ = [
    "agent_runs_router",
    "agents_router",
    "api_tokens_router",
    "architectures_router",
    "auth_router",
    "enterprise_router",
    "events_router",
    "github_router",
    "inbox_router",
    "mcp_router",
    "messages_router",
    "model_settings_router",
    "procedures_router",
    "providers_router",
    "reviews_router",
    "specifications_router",
    "sprint_control_router",
    "sprint_router",
    "support_router",
    "workspace_router",
]
