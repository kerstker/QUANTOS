"""Execute the canonical Python lint/type stages against real bad inputs."""
from pathlib import Path
import subprocess
import shutil

from scripts.validate import CHECKS


def _command(name):
    matches = [command for stage, command, _ in CHECKS if stage == name]
    assert len(matches) == 1, f'missing canonical stage: {name}'
    return matches[0]


def test_python_lint_stage_rejects_an_undefined_name(tmp_path):
    shutil.copyfile(Path(__file__).resolve().parents[2] / 'pyproject.toml', tmp_path / 'pyproject.toml')
    command = _command('python-quality:ruff')
    path = tmp_path / 'broken.py'
    path.write_text('def run():\n    return missing_result\n')
    failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert failed.returncode != 0 and 'F821' in failed.stdout
    path.write_text('def run():\n    return 1\n')
    assert subprocess.run(command, cwd=tmp_path, capture_output=True).returncode == 0


def test_python_type_stage_rejects_a_contract_type_error(tmp_path):
    shutil.copyfile(Path(__file__).resolve().parents[2] / 'pyproject.toml', tmp_path / 'pyproject.toml')
    command = _command('python-quality:mypy')
    paths = [Path(arg) for arg in command if arg.endswith('.py')]
    assert paths
    for path in paths:
        assert not path.is_absolute()
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('value: int = 1\n')
    target = tmp_path / paths[0]
    target.write_text("value: int = 'wrong'\n")
    failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert failed.returncode != 0 and '[assignment]' in failed.stdout
    target.write_text('value: int = 1\n')
    assert subprocess.run(command, cwd=tmp_path, capture_output=True).returncode == 0


def test_python_type_stage_rejects_untyped_analytics_function(tmp_path):
    shutil.copyfile(Path(__file__).resolve().parents[2] / 'pyproject.toml', tmp_path / 'pyproject.toml')
    command = _command('python-quality:mypy')
    for arg in command:
        if arg.endswith('.py'):
            target = tmp_path / arg
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('value: int = 1\n')
    package = tmp_path / 'quantos' / 'analytics'
    package.mkdir(parents=True, exist_ok=True)
    (package.parent / '__init__.py').touch()
    (package / '__init__.py').touch()
    target = package / 'orthogonalize.py'
    target.write_text('def projection(value):\n    return value\n')
    failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert failed.returncode != 0 and '[no-untyped-def]' in failed.stdout
    assert 'Per-module sections' not in failed.stderr
    target.write_text('def projection(value: float) -> float:\n    return value\n')
    assert subprocess.run(command, cwd=tmp_path, capture_output=True).returncode == 0


def test_python_type_stage_rejects_untyped_risk_contract_function(tmp_path):
    shutil.copyfile(Path(__file__).resolve().parents[2] / 'pyproject.toml', tmp_path / 'pyproject.toml')
    command = _command('python-quality:mypy')
    for arg in command:
        if arg.endswith('.py'):
            target = tmp_path / arg
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('value: int = 1\n')
    package = tmp_path / 'quantos' / 'contracts'
    package.mkdir(parents=True, exist_ok=True)
    (package.parent / '__init__.py').touch()
    (package / '__init__.py').touch()
    target = package / 'risk.py'
    target.write_text('def deserialize(value):\n    return value\n')
    failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert failed.returncode != 0 and '[no-untyped-def]' in failed.stdout
    target.write_text('def deserialize(value: str) -> str:\n    return value\n')
    assert subprocess.run(command, cwd=tmp_path, capture_output=True).returncode == 0
