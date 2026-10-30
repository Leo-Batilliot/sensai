VENV   ?= .venv
PYTHON ?= python3
PY     := $(VENV)/bin/python
TAG    ?= dev

.PHONY: help install test test-all lint format ci chat web web-mock web-build seed eval eval-adversarial clean

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  make %-10s %s\n", $$1, $$2}'

$(PY):
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip

install: $(PY) ## Create the venv and install dependencies (dev included)
	$(PY) -m pip install -r requirements-dev.txt

test: ## Run tests without Ollama or external tools (same as CI)
	$(PY) -m pytest -m "not ollama and not external"

test-all: ## Run all tests, including real Ollama and external MCP servers
	$(PY) -m pytest

lint: ## Check lint and formatting (same as CI)
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

ci: ## Run the CI locally: lint + tests on Python 3.10 and 3.12 in fresh venvs (needs uv)
	bash scripts/ci_local.sh

format: ## Fix lint issues and reformat
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

chat: ## Start the CLI chatbot
	$(PY) -m sensai chat

web: ## Start the web server (http://127.0.0.1:8000)
	$(PY) -m sensai web

web-mock: ## Start the web server on the MockEngine (replays tests/fixtures/events)
	$(PY) -m sensai web --mock

web-build: ## Rebuild the frontend after a change in web/src, then commit web/dist (needs Node 22)
	cd web && npm ci && npm run build

seed: ## Generate the demo database
	$(PY) -m sensai seed --seed 42

eval: ## Run the evaluation harness (make eval TAG=v0.5)
	$(PY) -m sensai eval --suite eval/golden.jsonl --tag $(TAG)

eval-adversarial: ## Replay the attacks of eval/adversarial.jsonl on the real model
	$(PY) -m sensai eval --suite eval/adversarial.jsonl

clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
