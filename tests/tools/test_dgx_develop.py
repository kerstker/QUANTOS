"""Development preflight must reject the wrong host/version before running tests."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "dgx_develop", Path(__file__).resolve().parents[2] / "scripts/dgx_develop.py")


def module():
    result = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(result)
    return result


def test_preflight_rejects_wrong_architecture():
    with pytest.raises(ValueError, match="aarch64"):
        module().check_runtime("x86_64", "3.12", "3.12")


def test_preflight_rejects_wrong_python():
    with pytest.raises(ValueError, match="Python"):
        module().check_runtime("aarch64", "3.13", "3.12")


def test_dependency_check_reports_missing_and_mismatched_packages():
    versions = {"numpy": "1.0"}
    def installed(name):
        if name not in versions:
            raise __import__("importlib.metadata", fromlist=["PackageNotFoundError"]).PackageNotFoundError(name)
        return versions[name]
    with pytest.raises(ValueError, match="numpy.*pytest"):
        module().check_dependencies("# package: numpy==2.4.4\n# package: pytest==9.1.1\n", installed)


def test_empty_dependency_manifest_is_not_a_valid_baseline():
    with pytest.raises(ValueError, match="empty"):
        module().check_dependencies("", lambda name: "1")


def test_dependency_check_records_verified_versions():
    assert module().check_dependencies("# package: pytest==9.1.1\n", lambda name: "9.1.1") == {"pytest": "9.1.1"}
