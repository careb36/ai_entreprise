# Agent roles

La Fabrique's agents are interchangeable producers; the product verifies their work.
The role names below are the canonical strings stored on `Agent.role` and used
by `ModelFactory.ROLE_MODEL_MAP` / `default_model_for_role()`.

The default team runs on DeepSeek alone (`docs/contexts/models.md`, The
default team): DeepSeek Pro (`deepseek-v4-pro`) where a mistake is expensive,
DeepSeek Flash (`deepseek-flash`) for the rest.

## Deciding and judging roles (DeepSeek Pro)

| Role | Alias | Default model |
|------|-------|---------------|
| `PO` | ProductOwner | deepseek-v4-pro |
| `Architect` | — | deepseek-v4-pro |
| `SecOps` | Security | deepseek-v4-pro |
| `Reviewer` | — | deepseek-v4-pro (reads QA's settings) |

## Every other role (DeepSeek Flash)

| Role | Aliases | Default model |
|------|---------|---------------|
| `ScrumMaster` | SM; the Manager agent runs as it | deepseek-flash |
| `Developer` | Dev, FullstackDev, MobileDev, MLEngineer | deepseek-flash |
| `BackendDeveloper`, `FrontendDeveloper`, `DevOps`, `DataEngineer` | BackendDev, FrontendDev, SRE, Data | deepseek-flash (as the Developer) |
| `Designer` | — | deepseek-flash |
| `QA` | — | deepseek-flash |
| `TechWriter` | — | deepseek-flash |
| `Support` | — | deepseek-flash (as TechWriter) |

## Internal

| Role | Default model |
|------|---------------|
| `EngramProcessor` | deepseek-flash |

## Frontend-only display roles

`UXResearcher` is a display role that resolves to `Developer` for model
routing. Any unknown role resolves to `Developer`.

## Autonomy

Roles are bound to a company (`Agent.company_id`) and a human role
(Owner > Manager > Viewer) gates who can change them. `NEMOTRON_ROLES`
env var opts roles into NVIDIA models served by Nebius.