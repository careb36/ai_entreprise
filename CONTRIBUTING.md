# Contributing to La Fabrique

Thank you for your interest in contributing to La Fabrique. This document outlines the standards and workflows for contributing to this project.

---

## Table of Contents

- [Branch Strategy](#branch-strategy)
- [Commit Conventions](#commit-conventions)
- [Development Setup](#development-setup)
- [Code Standards](#code-standards)
- [Testing Requirements](#testing-requirements)
- [Pull Request Process](#pull-request-process)
- [AI Assistant Workflow](#ai-assistant-workflow)

---

## Branch Strategy

We follow the **GitFlow** model for software lifecycle management.

### Main Branches
- `main`: Stable, production-ready code
- `develop`: Integration branch for ongoing development

### Working Branches
| Type | Prefix | Source | Target | Example |
|:-----|:-------|:-------|:-------|:--------|
| Feature | `feature/` | `develop` | `develop` | `feature/auth-sqlmodel` |
| Bugfix | `bugfix/` | `develop` | `develop` | `bugfix/nats-retry-logic` |
| Release | `release/` | `develop` | `main` & `develop` | `release/v2.1.0` |
| Hotfix | `hotfix/` | `main` | `main` & `develop` | `hotfix/critical-db-fix` |

---

## Commit Conventions

All commits must follow the [Conventional Commits](https://www.conventionalcommits.org/) format: `<type>(<scope>): <description>`

### Allowed Types
- `feat`: New functionality
- `fix`: Bug fix
- `docs`: Documentation-only changes
- `style`: Formatting changes (spaces, commas) that do not affect logic
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `perf`: Performance improvement
- `test`: Add or fix tests
- `chore`: Changes to build process or auxiliary tools
- `ci`: Changes to CI configuration files and scripts

### Examples
- `feat(auth): implement user registration with SQLModel`
- `fix(nats): correct exponential reconnection in client`
- `refactor(backend): modularize sprint endpoints into independent routers`

---

## Development Setup

### Prerequisites
- Docker 24.0+ with Docker Compose v2
- Python 3.12+
- Node.js 20+

### Initial Setup

```bash
# Clone the repository
git clone https://github.com/careb36/le-fabrique.git
cd le-fabrique

# Configure environment
cp .env.example .env
# Edit .env with your API keys and passwords

# Start the full stack
./reset_infra.sh
```

### Running Tests

```bash
# Backend tests
cd backend && PYTHONPATH=.:app python3 -m pytest tests -q

# Frontend tests
cd frontend && npm run test:run
```

### API Integration

The [API and integration guide](docs/API.md) documents endpoint examples,
authentication, HTTP errors, NATS/SSE events, and onboarding an external
producer. To regenerate the FastAPI schema after changing a route or request
model, run `cd backend && PYTHONPATH=.:app poetry run python scripts/generate_openapi.py`.
The generated `openapi.yaml` is checked for drift by backend CI.

---

## Code Standards

### Backend (Python)
- Use `typing` for all function signatures
- Follow PEP 8 style guide
- Use Pydantic v2 for request/response validation
- No coupling between database logic and FastAPI controllers
- Inject dependencies whenever possible

### Frontend (TypeScript)
- Use strict TypeScript types
- All user-facing strings must be added to both `frontend/src/locales/en.json` and `frontend/src/locales/es.json`
- Use the `useTranslation` hook from i18next; never hardcode English strings in JSX
- Use Zustand for state management
- Use TanStack Query for API operations

---

## Testing Requirements

- All new features must include corresponding tests
- Backend tests use pytest with in-memory SQLite (no external services required)
- Frontend tests use Vitest with Testing Library and jsdom
- CI runs automatically on every pull request

---

## Pull Request Process

1. **Create a branch** from `develop` following the naming convention
2. **Implement** your changes following code standards
3. **Test** locally — ensure all tests pass
4. **Commit** using conventional commit format
5. **Push** your branch to the repository
6. **Open a Pull Request** with a clear description of changes
7. **Address review feedback** promptly
8. **Merge** once approved and CI passes

---

## AI Assistant Workflow

When working with AI assistants (Copilot, Claude, etc.):

1. **Investigate:** Read `REFACTORING_PLAN.md` to understand the current objective
2. **Branch:** Create a `feature/` or `bugfix/` branch from `develop`
3. **Implement:** Write code following design patterns (Screaming Architecture)
4. **Validate:** Ensure code compiles and passes linters
5. **Commit:** Make atomic, descriptive commits following the convention
6. **PR:** Describe changes made in the Pull Request

---

## Questions?

If you have questions about contributing, please open a [GitHub Discussion](https://github.com/careb36/le-fabrique/discussions) or contact the maintainer.
