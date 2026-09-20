.PHONY: up down build logs test lint format migrate revision

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

migrate:
	docker compose exec api alembic upgrade head

revision:
	docker compose exec api alembic revision --autogenerate -m "$(msg)"

test:
	pytest backend/tests -v

lint:
	ruff check backend/

format:
	ruff format backend/
