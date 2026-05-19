.PHONY: install dev test test-all lint fmt hooks check help

install: ## Install all dependencies
	pip install -r requirements.txt

dev: ## Run the dev server with hot-reload on port 8000
	uvicorn api.main:app --reload --port 8000

test: ## Run unit tests (no network required)
	pytest tests/ -m "not integration" -v

test-all: ## Run all tests including real TikTok API call
	pytest tests/ -v

lint: ## Check code style with ruff
	ruff check .

fmt: ## Auto-fix style issues with ruff
	ruff check --fix .

hooks: ## Install pre-commit hooks (run once after cloning)
	pre-commit install

check: lint test ## Lint + unit tests — run before every push

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
