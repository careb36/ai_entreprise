# Agent roles

La Fabrique's agents are interchangeable producers; the product verifies their work.
The role names below are the canonical strings stored on `Agent.role` and used
by `ModelFactory.ROLE_MODEL_MAP` / `default_model_for_role()`.

## Decision roles (reasoning-heavy models)

| Role | Alias | Default model |
|------|-------|---------------|
| `PO` | ProductOwner | qwen3.7-plus |
| `Architect` | — | qwen3.7-plus |
| `ScrumMaster` | SM | qwen3.7-plus |
| `SecOps` | Security | qwen3.7-plus |

## Implementation roles (code models)

| Role | Aliases | Default model |
|------|---------|---------------|
| `Developer` | Dev, BackendDev, FrontendDev, FullstackDev, MobileDev, DataEngineer, MLEngineer, DevOps | deepseek-v4-pro |
| `QA` | — | deepseek-v4-pro |
| `Reviewer` | — | deepseek-v4-pro (as QA) |

## Documentation / support roles (fast models)

| Role | Alias | Default model |
|------|-------|---------------|
| `TechWriter` | — | qwen3.6-flash |
| `Support` | — | qwen3.6-flash (as TechWriter) |

## Internal

| Role | Default model |
|------|---------------|
| `EngramProcessor` | deepseek-v4-pro |

## Frontend-only display roles

`Designer`, `UXResearcher` are display roles that resolve to `Developer` for
model routing. Any unknown role resolves to `Developer`.

## Autonomy

Roles are bound to a company (`Agent.company_id`) and a human role
(Owner > Manager > Viewer) gates who can change them. `NEMOTRON_ROLES`
env var opts roles into NVIDIA models served by Nebius.