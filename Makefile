# MHEAT — developer Makefile. GNU make.
#
# Tested on Linux, macOS, and Git-Bash / MSYS2 on Windows.
# Run `make help` for the full list.

SHELL := bash
.DEFAULT_GOAL := help

BACKEND := backend
FRONTEND := frontend
DEMO_MODE ?= true
PY ?= python

.PHONY: help
help: ## List available targets.
	@awk 'BEGIN { FS = ":.*## "; print "\n  make <target>\n" } \
	      /^[a-zA-Z0-9_-]+:.*## / { printf "  \033[36m%-18s\033[0m  %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

# ---- backend --------------------------------------------------------------

.PHONY: install-backend
install-backend: ## pip install backend dev dependencies.
	cd $(BACKEND) && $(PY) -m pip install -r requirements-dev.txt

.PHONY: lint
lint: ## Lint backend (ruff) and frontend (eslint).
	cd $(BACKEND) && $(PY) -m ruff check app/ tests/
	cd $(FRONTEND) && npm run lint

.PHONY: lint-backend
lint-backend: ## Ruff lint on the whole backend package.
	cd $(BACKEND) && $(PY) -m ruff check app/ tests/

.PHONY: fmt
fmt: ## Ruff auto-format + auto-fix (run locally before committing).
	cd $(BACKEND) && $(PY) -m ruff check --fix app/ tests/

.PHONY: type
type: ## mypy type-check on app/ (pragmatic strictness).
	cd $(BACKEND) && $(PY) -m mypy app

.PHONY: test
test: ## Run backend pytest + frontend vitest (demo-mode fixture).
	cd $(BACKEND) && DEMO_MODE=$(DEMO_MODE) $(PY) -m pytest
	cd $(FRONTEND) && npm test

.PHONY: test-backend
test-backend: ## pytest with coverage gate (demo-mode fixture).
	cd $(BACKEND) && DEMO_MODE=$(DEMO_MODE) $(PY) -m pytest

.PHONY: audit
audit: ## pip-audit + npm audit — fail on HIGH/CRITICAL.
	grep -v "git+" $(BACKEND)/requirements.txt > /tmp/req-audit.txt
	$(PY) -m pip_audit --strict -r /tmp/req-audit.txt
	cd $(FRONTEND) && npm audit --omit=dev --audit-level=high

# ---- frontend -------------------------------------------------------------

.PHONY: install-frontend
install-frontend: ## npm install frontend deps.
	cd $(FRONTEND) && npm install --no-audit --no-fund

.PHONY: test-frontend
test-frontend: ## Vitest + coverage.
	cd $(FRONTEND) && npx vitest run --coverage

.PHONY: build-frontend
build-frontend: ## Type-check + production bundle.
	cd $(FRONTEND) && npm run build

.PHONY: lint-frontend
lint-frontend: ## ESLint.
	cd $(FRONTEND) && npm run lint

# ---- release / artefacts --------------------------------------------------

.PHONY: bench
bench: ## Run the in-process latency benchmark; writes docs/performance.md.
	DEMO_MODE=$(DEMO_MODE) $(PY) scripts/bench_inproc.py

.PHONY: reproduce
reproduce: ## Regenerate the reproducibility manifest under out/.
	DEMO_MODE=$(DEMO_MODE) $(PY) scripts/reproduce.py

.PHONY: arco
arco: ## Write the ARCO Zarr cube to out/mheat.zarr.
	DEMO_MODE=$(DEMO_MODE) $(PY) scripts/export_arco.py --out out/mheat.zarr

.PHONY: stac
stac: ## Build a pystac-validated STAC tree under out/stac/ (dry-run).
	$(PY) scripts/register_stac.py --out out/stac --years 2022 2023 2024

.PHONY: freeze-openapi
freeze-openapi: ## Accept the live OpenAPI shape as the new contract baseline.
	$(PY) scripts/freeze_openapi.py

# ---- convenience ----------------------------------------------------------

.PHONY: demo
demo: ## docker compose up -d --build then point reviewer at the demo URL.
	docker compose up -d --build
	@echo ""
	@echo "  Open http://localhost:8000"
	@echo ""

.PHONY: repro
repro: ## Reproducibility: regenerate the manifest under out/ (alias of reproduce).
	DEMO_MODE=$(DEMO_MODE) $(PY) scripts/reproduce.py

.PHONY: docker
docker: ## Build the full OCI image; tag mheat:local.
	docker build -t mheat:local .

.PHONY: compose
compose: ## docker compose up (attached, demo mode).
	docker compose up --build

.PHONY: clean
clean: ## Drop local test / build artefacts.
	rm -rf out/ $(BACKEND)/coverage.json $(FRONTEND)/coverage/ $(FRONTEND)/dist/ .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: all
all: lint type test test-frontend build-frontend audit ## Run every gate CI runs (local mirror).
	@echo ""
	@echo "  ✔ local mirror of CI pipeline passed"
	@echo ""
