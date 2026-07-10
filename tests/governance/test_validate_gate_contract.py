from pathlib import Path


def test_canonical_validate_gate_includes_frontend_typecheck_tests_and_build():
    source = Path("scripts/validate.py").read_text(encoding="utf-8")
    assert '"run", "typecheck"' in source
    assert '"test"' in source
    assert '"run", "build"' in source
    assert "apps/research-console" in source or "APPS" in source

