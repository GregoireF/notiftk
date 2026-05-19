.PHONY: install install-dev dev test test-all coverage lint fmt format type-check security openapi clean hooks check help

install: ## Install runtime dependencies only
	pip install -r requirements.txt

install-dev: ## Install all dependencies including dev tools
	pip install -r requirements-dev.txt

dev: ## Run the dev server with hot-reload on port 8000
	uvicorn api.main:app --reload --port 8000

test: ## Run unit tests (no network required)
	pytest tests/ -m "not integration" -v

test-all: ## Run all tests including real TikTok API call
	pytest tests/ -v

coverage: ## Run unit tests with coverage report
	pytest tests/ -m "not integration" --cov=api --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

lint: ## Check code style with ruff
	ruff check .

fmt: ## Auto-fix style issues with ruff
	ruff format .
	ruff check --fix .

format: fmt ## Alias for fmt

type-check: ## Run static type checking with mypy
	mypy api/ --ignore-missing-imports

security: ## Audit dependencies for known vulnerabilities
	pip-audit -r requirements.txt

openapi: ## Export OpenAPI schema to openapi.json
	python -c "from api.main import app; import json; print(json.dumps(app.openapi(), indent=2))" > openapi.json
	@echo "Schema exported to openapi.json"

clean: ## Remove build artifacts and cache directories
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name "coverage.xml" -delete 2>/dev/null || true
	find . -name "openapi.json" -delete 2>/dev/null || true
	@echo "Cleaned."

hooks: ## Install pre-commit hooks (run once after cloning)
	pre-commit install

check: lint type-check test ## Lint + type-check + unit tests — run before every push

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
