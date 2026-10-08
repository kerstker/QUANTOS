"""A failing validation-domain test must fail the actual canonical command."""
from pathlib import Path

import pytest

from scripts import validate


@pytest.mark.parametrize("selection", ["backend", "all"])
def test_original_gate_command_cannot_omit_a_failing_validation_test(tmp_path, capfd, selection):
    checks = validate._select_checks([selection], validate.CHECKS)
    stage, command, _ = next(row for row in checks if row[0].startswith("pytest: all suites"))
    # Provide the command's other real selectors in an isolated fixture. They
    # pass; only the previously omitted qualification/statistics domain fails.
    for argument in command[4:]:
        if argument.startswith("-"):
            continue
        target = tmp_path / argument
        if argument.endswith(".py"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def test_other_domain():\n    assert True\n")
        else:
            target.mkdir(parents=True, exist_ok=True)
    passing = tmp_path / "tests/contracts/test_other_domain.py"
    passing.write_text("def test_other_contract():\n    assert True\n")
    failing = tmp_path / "tests/validation/test_project_research_statistics.py"
    failing.parent.mkdir(parents=True, exist_ok=True)
    failing.write_text(
        "def test_research_qualification_failure():\n"
        "    raise AssertionError('VALIDATION_BUG_EXPOSED')\n"
    )
    result = validate._run_check(stage, command, str(tmp_path), 60)
    assert result["status"] == "FAIL", "validation failure was omitted from the canonical command"
    assert result["returncode"] == 1
    assert result["pytest_counts"]["failed"] == 1
    assert "VALIDATION_BUG_EXPOSED" in capfd.readouterr().out


def test_all_existing_validation_files_are_covered_by_the_original_backend_selector():
    # Resolve coverage for all present modules, so a new file does not depend
    # on a maintained list of names or the test module's import side effects.
    stage, command, directory = next(row for row in validate.CHECKS if row[0].startswith("pytest: all suites"))
    root = Path(directory)
    selectors = [root / arg for arg in command[4:] if not arg.startswith("-")]
    modules = sorted((root / "tests/validation").glob("test_*.py"))
    assert modules
    missing = [str(module.relative_to(root)) for module in modules
               if not any(module == selected or module.is_relative_to(selected) for selected in selectors)]
    assert not missing, f"{stage} omits validation modules: {missing}"
