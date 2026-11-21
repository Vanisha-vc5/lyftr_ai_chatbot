# Vani: run `make help` for targets.
SHELL := /bin/sh
COMPOSE ?= docker compose
BASE_URL ?= http://localhost:8000
PYTHON ?= python3

.PHONY: help up down logs ps restart test lint smoke clean

help:
	@echo "make up | down | logs | ps | restart | test | lint | smoke [BASE_URL=...] | clean"

up:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo ""; \
		echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"; \
		echo "!!  .env was missing: copied .env.example -> .env               !!"; \
		echo "!!  Put your real LLM_API_KEYS in .env, then run make up again.  !!"; \
		echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"; \
		echo ""; \
	fi
	$(COMPOSE) up -d --build
	@echo ""
	@echo "Backend:  http://localhost:8000/health"
	@echo "Frontend: http://localhost:3000"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

restart:
	$(COMPOSE) restart

test:
	docker run --rm -v "$(CURDIR)/backend:/src" -w /src python:3.12-slim \
		sh -c "pip install -q --root-user-action=ignore -r requirements-dev.txt && python -m pytest -q"

lint:
	docker run --rm -v "$(CURDIR)/backend:/src" -w /src python:3.12-slim \
		sh -c "pip install -q --root-user-action=ignore ruff==0.11.10 && ruff check . && ruff format --check ."

smoke:
	$(PYTHON) scripts/smoke_test.py $(BASE_URL)

clean:
	@printf "This deletes the vani_data volume (all chat history and cache). Continue? [y/N] "; \
	read ans; [ "$$ans" = "y" ] || [ "$$ans" = "Y" ] || { echo "aborted"; exit 1; }; \
	$(COMPOSE) down -v
