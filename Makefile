PYTHON ?= python
CONSOLE_DIR := apps/research-console

.PHONY: help install install-frontend hygiene clean clean-apply governance validate test frontend api console-status hooks

help:
	@echo "QUANTOS targets"
	@echo "  make install          Install pinned Python development dependencies"
	@echo "  make install-frontend Install pinned console dependencies"
	@echo "  make hygiene          Check repository layout and curated documentation"
	@echo "  make clean            Preview disposable local artifacts"
	@echo "  make clean-apply      Remove disposable local artifacts (never key/data/node_modules)"
	@echo "  make governance       Run the paper-to-live governance scanner"
	@echo "  make validate         Run the canonical fail-closed gate"
	@echo "  make frontend         Typecheck, lint, test, and build the console"
	@echo "  make api              Start the read-only API on 127.0.0.1:8400"
	@echo "  make console-status   Inspect the managed local research stack"
	@echo "  make hooks            Install repository Git hooks"

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

install-frontend:
	cd $(CONSOLE_DIR) && npm ci

hygiene:
	$(PYTHON) scripts/check_repository_hygiene.py

clean:
	$(PYTHON) scripts/clean_workspace.py

clean-apply:
	$(PYTHON) scripts/clean_workspace.py --apply

governance:
	$(PYTHON) apps/research-console/scripts/run_paper_to_live_governance_check.py

validate:
	$(PYTHON) scripts/validate.py

test: validate

frontend:
	cd $(CONSOLE_DIR) && npm run typecheck && npm run eslint && npm test && npm run build

api:
	$(PYTHON) -m uvicorn services.console.main:app --host 127.0.0.1 --port 8400

console-status:
	$(PYTHON) -m tools.local_console_stack status

hooks:
	sh scripts/install-hooks.sh
