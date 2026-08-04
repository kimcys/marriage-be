PYTHON ?= python3

.PHONY: install format lint typecheck test migrate run docker-up docker-down verify

install:
	$(PYTHON) -m pip install -e '.[dev]'

format:
	ruff format --check .

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest -q

migrate:
	alembic upgrade head

run:
	uvicorn marriage_ocr_api.main:app --host 0.0.0.0 --port 8000 --workers 1

docker-up:
	docker compose up -d postgres

docker-down:
	docker compose down -v

verify:
	ruff format --check . && ruff check . && mypy src && pytest -q

