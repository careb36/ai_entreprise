.PHONY: setup install dev-backend dev-frontend dev-all clean

# Variables
PYTHON_VERSION = 3.11
NODE_VERSION = 24

# Console colours
GREEN  := $(shell tput -Txterm setaf 2)
RESET  := $(shell tput -Txterm sgr0)

setup:
	@echo "$(GREEN)🚀 Setting up La Fabrique...$(RESET)"
	@make install

install:
	@echo "$(GREEN)📦 Installing backend dependencies (Poetry)...$(RESET)"
	cd backend && poetry install
	@echo "$(GREEN)📦 Installing frontend dependencies (npm)...$(RESET)"
	cd frontend && npm install
	@echo "$(GREEN)📦 Installing LLM proxy dependencies (Poetry)...$(RESET)"
	cd llm-proxy && poetry install

dev-backend:
	@echo "$(GREEN)🔥 Starting the backend (FastAPI)...$(RESET)"
	cd backend && PYTHONPATH=.:app poetry run uvicorn app.main:app --reload --port 8000

dev-llm-proxy:
	@echo "$(GREEN)🤖 Starting the LLM proxy (Flask)...$(RESET)"
	cd llm-proxy && poetry run python app.py

dev-frontend:
	@echo "$(GREEN)🎨 Starting the frontend (React/Vite)...$(RESET)"
	cd frontend && npm run dev

dev-all:
	@echo "$(GREEN)🚀 Starting everything (needs tmux or similar to see the logs)...$(RESET)"
	@make -j 3 dev-backend dev-llm-proxy dev-frontend

clean:
	@echo "$(GREEN)🧹 Cleaning caches and environments...$(RESET)"
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name "node_modules" -exec rm -rf {} +
	@echo "$(GREEN)✨ Clean.$(RESET)"

ci:
	@echo "$(GREEN)🧪 CI local (backend + frontend + proxy)...$(RESET)"
	@make ci-backend
	@make ci-frontend
	@make ci-proxy

ci-backend:
	@echo "$(GREEN)🐍 Backend: ruff + pytest...$(RESET)"
	cd backend && PYTHONPATH=.:app poetry run ruff check app tests
	cd backend && PYTHONPATH=.:app poetry run ruff format --check app tests
	cd backend && PYTHONPATH=.:app poetry run pytest tests -q

ci-frontend:
	@echo "$(GREEN)🎨 Frontend: tsc + lint + tests + build...$(RESET)"
	cd frontend && npx tsc --noEmit
	cd frontend && npm run lint
	cd frontend && npm run test:run
	cd frontend && npm run build

ci-proxy:
	@echo "$(GREEN)🤖 LLM proxy: pytest...$(RESET)"
	cd llm-proxy && poetry run python -m pytest tests -q
