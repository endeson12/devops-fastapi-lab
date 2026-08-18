.PHONY: install run lint format typecheck test check docker-build compose-up compose-down backup restore

install:
	uv sync --extra dev

run:
	uv run uvicorn app.main:app --reload

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy app

test:
	uv run pytest

check: lint typecheck test

docker-build:
	docker build -t devops-fastapi-lab:local .

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down

backup:
	uv run python scripts/backup.py

restore:
	@test -n "$(FILE)" || (echo "Uso: make restore FILE=backups/tasks-....db" && exit 2)
	uv run python scripts/restore.py "$(FILE)" --force
