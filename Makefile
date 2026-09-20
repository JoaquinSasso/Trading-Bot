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

backtest:
	python -m tbot.backtest.runner --strategy $(or $(STRATEGY),s1) --start $(or $(START),2025-01-01) --end $(or $(END),2025-12-31)
