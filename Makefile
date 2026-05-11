.PHONY: dev refresh upload-db test test-slow report docker-build docker-bench help

PYTHON := .venv/bin/python
VENV   := .venv/bin

help:
	@echo "Targets:"
	@echo "  dev          - run gunicorn locally against data/bikemap.db"
	@echo "  refresh      - run prep pipeline (~5 min, rebuilds bikemap.db)"
	@echo "  upload-db    - push data/bikemap.db to RENDER_BASE_URL"
	@echo "  test         - ruff + mypy + pytest (fast suite)"
	@echo "  test-slow    - pytest -m slow (real DB tests)"
	@echo "  report       - print prep_report.md"
	@echo "  docker-build - build production Docker image"
	@echo "  docker-bench - run smoke test inside production image"

dev:
	APP_BOOTSTRAP=1 BIKEMAP_DB_PATH=data/bikemap.db CACHE_DB_PATH=/tmp/cache.db \
	NOMINATIM_USER_AGENT=dev/1.0 \
	$(VENV)/gunicorn -w 1 --threads 4 -b 0.0.0.0:8000 app.main:app

refresh:
	$(PYTHON) -m prep.main

upload-db:
	@: $${RENDER_BASE_URL?Set RENDER_BASE_URL (e.g. https://chicago-bike-advocacy-map.onrender.com)}
	@: $${UPLOAD_TOKEN?Set UPLOAD_TOKEN (must match Render env var)}
	$(PYTHON) -m prep.upload_db

test:
	$(VENV)/ruff check app/ prep/ tests/
	$(VENV)/mypy app/ prep/
	$(VENV)/pytest

test-slow:
	$(VENV)/pytest -m slow

report:
	@if [ -f prep_report.md ]; then cat prep_report.md; else echo "no prep_report.md (run 'make refresh' first)"; fi

docker-build:
	docker build --platform=linux/amd64 -t chicago-bike-prod:latest .

docker-bench:
	@# Each test runs in its own docker invocation so it gets a fresh pytest
	@# process. Running both in the same process would create two Flask apps
	@# back-to-back (each holding a 350 MB graph snapshot), which production
	@# never does — gunicorn loads the app exactly once. APP_BOOTSTRAP=0
	@# prevents pytest's `from app.main import create_app` from triggering
	@# the gunicorn-only auto-load at module import time.
	docker run --rm --platform=linux/amd64 \
	  -v $$(pwd)/data:/app/data:ro \
	  -e BIKEMAP_DB_PATH=/app/data/bikemap.db \
	  -e APP_BOOTSTRAP=0 \
	  --memory=512m \
	  chicago-bike-prod:latest \
	  pytest -m slow tests/app/test_smoke_real_db.py::test_routes_and_memory_against_real_db -v -s
	docker run --rm --platform=linux/amd64 \
	  -v $$(pwd)/data:/app/data:ro \
	  -e BIKEMAP_DB_PATH=/app/data/bikemap.db \
	  -e APP_BOOTSTRAP=0 \
	  --memory=512m \
	  chicago-bike-prod:latest \
	  pytest -m slow tests/app/test_smoke_real_db.py::test_memory_under_sustained_load -v -s
