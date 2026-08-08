from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pre_push_clears_repository_local_environment_before_validation() -> None:
    hook = (ROOT / "scripts" / "git-hooks" / "pre-push").read_text(
        encoding="utf-8"
    )
    cleanup = "unset $(git rev-parse --local-env-vars)"
    validation = 'python "$ROOT/scripts/validate.py"'

    assert cleanup in hook
    assert hook.index(cleanup) < hook.index(validation)
