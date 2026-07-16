UV ?= uv
PNPM ?= pnpm
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(UV) run python)
PYTEST ?= $(if $(wildcard .venv/bin/pytest),.venv/bin/pytest,$(UV) run pytest)
RUFF ?= $(if $(wildcard .venv/bin/ruff),.venv/bin/ruff,$(UV) run ruff)
MYPY ?= $(if $(wildcard .venv/bin/mypy),.venv/bin/mypy,$(UV) run mypy)
UVICORN ?= $(if $(wildcard .venv/bin/uvicorn),.venv/bin/uvicorn,$(UV) run uvicorn)
ALEMBIC ?= $(if $(wildcard .venv/bin/alembic),.venv/bin/alembic,$(UV) run alembic)

ifneq (,$(wildcard .env))
include .env
export
endif

API_HOST ?= 127.0.0.1
API_PORT ?= 8000
WEB_PORT ?= 3000
API_INTERNAL_URL ?= http://127.0.0.1:$(API_PORT)

.PHONY: bootstrap dev migrate api web test test-python test-web test-integration lint typecheck build openapi compose-up compose-down

bootstrap:
	$(UV) sync --all-packages --all-groups
	$(PNPM) install

dev:
	docker compose up --build

migrate:
	@mkdir -p data
	$(ALEMBIC) -c apps/api/alembic.ini upgrade head

api: migrate
	$(UVICORN) quant_api.main:app --app-dir apps/api/src --reload --host $(API_HOST) --port $(API_PORT)

web:
	$(PNPM) --filter web dev --hostname 127.0.0.1 --port $(WEB_PORT)

test: test-python test-web

test-python:
	$(PYTEST)

test-web:
	$(PNPM) --filter web test

test-integration:
	@set -eu; \
	compose_test() { docker compose -p quant-trend-lab-test -f compose.test.yaml "$$@"; }; \
	cleanup() { compose_test down -v --remove-orphans >/dev/null 2>&1 || true; }; \
	trap cleanup EXIT INT TERM; \
	compose_test down -v --remove-orphans >/dev/null 2>&1 || true; \
	compose_test up -d --wait postgres-test valkey-test; \
	postgres_address="$$(compose_test port postgres-test 5432)"; \
	postgres_port="$${postgres_address##*:}"; \
	valkey_address="$$(compose_test port valkey-test 6379)"; \
	valkey_port="$${valkey_address##*:}"; \
	valkey_guard="$$(compose_test ps -q valkey-test)"; \
	test -n "$$valkey_guard"; \
	compose_test exec -T valkey-test valkey-cli SET quant-trend-lab:test-guard "$$valkey_guard" >/dev/null; \
	test_database_url="postgresql+asyncpg://quant_test:quant_test_password@127.0.0.1:$${postgres_port}/quant_test"; \
	test_valkey_url="redis://127.0.0.1:$${valkey_port}/0"; \
	DATABASE_URL="$$test_database_url" APP_ENV=test AUTO_CREATE_SCHEMA=false \
		$(ALEMBIC) -c apps/api/alembic.ini upgrade head; \
	DATABASE_URL="$$test_database_url" APP_ENV=test AUTO_CREATE_SCHEMA=false \
		$(ALEMBIC) -c apps/api/alembic.ini check; \
	TEST_DATABASE_URL="$$test_database_url" TEST_VALKEY_URL="$$test_valkey_url" \
		TEST_VALKEY_GUARD="$$valkey_guard" \
		$(PYTEST) -m integration apps/api/integration_tests

lint:
	$(RUFF) check .
	$(PNPM) --filter web lint

typecheck:
	$(MYPY) packages/quant-core/src apps/api/src
	$(PNPM) --filter web typecheck

build:
	$(PNPM) --filter web build

openapi:
	$(PYTHON) scripts/export_openapi.py
	$(PNPM) --filter web generate:api

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down
