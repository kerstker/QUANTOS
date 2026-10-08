#!/bin/sh
# 让 git 使用仓库内受版本控制的钩子(scripts/git-hooks/)。
set -e
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" config core.hooksPath scripts/git-hooks
chmod +x "$ROOT"/scripts/git-hooks/* 2>/dev/null || true
echo "✓ git 钩子已安装(core.hooksPath = scripts/git-hooks)"
echo "  pre-commit → 治理扫描 + 密钥防呆"
echo "  pre-push   → python scripts/validate.py"
