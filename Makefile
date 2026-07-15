# QUANTOS 常用任务。Windows 用户在 Git Bash 中运行,或直接用 README/CONTRIBUTING 里的等价命令。
.PHONY: help gate governance test frontend api hooks

help:
	@echo "QUANTOS make targets:"
	@echo "  make gate        完整失败关闭门禁(治理扫描 + 全部 pytest)"
	@echo "  make governance  只跑治理扫描器"
	@echo "  make frontend    研究控制台 typecheck + build + test"
	@echo "  make api         起只读 console API(127.0.0.1:8400)"
	@echo "  make hooks       安装本地 git 钩子(pre-commit + pre-push)"

gate:
	python scripts/validate.py

governance:
	python apps/research-console/scripts/run_paper_to_live_governance_check.py

test: gate

frontend:
	cd apps/research-console && npm ci && npm run typecheck && npm run build && npm test

api:
	python -m uvicorn services.console.main:app --host 127.0.0.1 --port 8400

hooks:
	sh scripts/install-hooks.sh
