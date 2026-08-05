DOCKER_COMPOSE ?= $(shell command -v docker-compose 2>/dev/null || echo "docker compose")

.PHONY: dev up down logs migrate test lint

# ── Development ─────────────────────────────────────────────
dev: up  ## Start all services
	@echo "✅ DocIntel is running at http://localhost:3000"
	@echo "   API docs: http://localhost:8000/docs"

up:  ## Start all containers
	$(DOCKER_COMPOSE) up -d --build

down:  ## Stop all containers
	$(DOCKER_COMPOSE) down

logs:  ## Tail all container logs
	$(DOCKER_COMPOSE) logs -f

logs-backend:  ## Tail backend logs
	$(DOCKER_COMPOSE) logs -f backend worker

# ── Database ────────────────────────────────────────────────
migrate:  ## Run database migrations
	$(DOCKER_COMPOSE) exec backend alembic upgrade head

migrate-create:  ## Create a new migration (usage: make migrate-create msg="add foo table")
	$(DOCKER_COMPOSE) exec backend alembic revision --autogenerate -m "$(msg)"

# ── Testing ─────────────────────────────────────────────────
test:  ## Run backend tests
	$(DOCKER_COMPOSE) exec backend pytest -v --cov=app tests/

test-local:  ## Run tests locally (without Docker)
	cd backend && pytest -v --cov=app tests/

# ── Linting ─────────────────────────────────────────────────
lint:  ## Run linter
	cd backend && ruff check . && ruff format --check .

format:  ## Auto-format code
	cd backend && ruff check --fix . && ruff format .

# ── Cleanup ─────────────────────────────────────────────────
clean:  ## Remove all containers, volumes, and build artifacts
	$(DOCKER_COMPOSE) down -v --rmi local
	rm -rf backend/uploads/*
