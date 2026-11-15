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
from routers.board_moves import router as board_moves_router
from routers.board_notes import router as board_notes_router
from routers.chat_approvals import router as chat_approvals_router
from routers.chat_interactions import router as chat_interactions_router
from routers.console_producers import router as console_producers_router
from routers.costs import router as costs_router
from routers.documentation import router as documentation_router
from routers.enterprise import router as enterprise_router
from routers.enterprise_agents import router as enterprise_agents_router
from routers.enterprise_analytics import router as enterprise_analytics_router
from routers.events import router as events_router
from routers.github import router as github_router
from routers.inbox import router as inbox_router
from routers.integration import router as integration_router
from routers.manager import router as manager_router
from routers.mcp import router as mcp_router
from routers.messages import router as messages_router
from routers.model_settings import router as model_settings_router
from routers.procedures import router as procedures_router
from routers.providers import router as providers_router
from routers.reviews import router as reviews_router
from routers.run_secrets import router as run_secrets_router
from routers.security_tests import router as security_tests_router
from routers.specifications import router as specifications_router
from routers.sprint import router as sprint_router
from routers.sprint_control import router as sprint_control_router
from routers.sprint_crud import router as sprint_crud_router
from routers.sso import router as sso_router
from routers.sso_admin import router as sso_admin_router
from routers.support import router as support_router
from routers.webhooks import router as webhooks_router
from routers.wiki import router as wiki_router
from routers.workspace import router as workspace_router

__all__ = [
    "agent_runs_router",
    "agents_router",
    "api_tokens_router",
    "architectures_router",
    "auth_router",
    "board_moves_router",
    "board_notes_router",
    "chat_approvals_router",
    "chat_interactions_router",
    "console_producers_router",
    "costs_router",
    "documentation_router",
    "enterprise_agents_router",
    "enterprise_analytics_router",
    "enterprise_router",
    "events_router",
    "github_router",
    "inbox_router",
    "integration_router",
    "manager_router",
    "mcp_router",
    "messages_router",
    "model_settings_router",
    "procedures_router",
    "providers_router",
    "reviews_router",
    "run_secrets_router",
    "security_tests_router",
    "specifications_router",
    "sprint_control_router",
    "sprint_crud_router",
    "sprint_router",
    "sso_admin_router",
    "sso_router",
    "support_router",
    "webhooks_router",
    "wiki_router",
    "workspace_router",
]
