.PHONY: up down logs test lint rebuild shell migrate help

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## Start all services in detached mode
	docker compose up -d --build

down: ## Stop all services and remove volumes
	docker compose down -v

logs: ## Follow logs from all services
	docker compose logs -f

test: ## Run tests using the test compose file
	docker compose -f docker-compose.test.yml up --build --abort-on-container-exit
	docker compose -f docker-compose.test.yml down -v

lint: ## Run linters (ruff for backend)
	cd backend && ruff check .

rebuild: ## Rebuild all containers without cache
	docker compose build --no-cache

shell: ## Open a shell in the backend container
	docker compose exec backend bash

migrate: ## Run Alembic migrations
	docker compose exec backend alembic upgrade head
