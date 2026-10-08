"""Pure trust readers must not require model dependencies to be installed."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_pure_json_contract_can_import_without_site_packages():
    command = """
import sys
from quantos.contracts.json_integrity import ascii_json_string_size
from services.governance.cross_host_container_reproducibility import build_container_build_claim
assert ascii_json_string_size('a\\nb', maximum_bytes=128) == 6
assert 'pydantic' not in sys.modules
print('PURE_IMPORT_OK')
"""
    result = subprocess.run([sys.executable, '-B', '-S', '-c', command], cwd=ROOT,
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'PURE_IMPORT_OK'


def test_lazy_domain_access_preserves_canonical_safety_classes_and_exports():
    command = """
import sys
import quantos.contracts as package
assert 'pydantic' not in sys.modules
from quantos.contracts import SafetyConfig, SafetyViolation, PaperOrderSide
from quantos.contracts.config import SafetyConfig as direct_config, SafetyViolation as direct_violation
from quantos.contracts.paper import OrderSide as direct_side
assert SafetyConfig is direct_config and SafetyViolation is direct_violation
assert PaperOrderSide is direct_side
assert SafetyConfig().live_trading_armed is False
assert set(package.__all__) <= set(dir(package))
try:
    getattr(package, 'unknown_contract')
except AttributeError:
    pass
else:
    raise AssertionError('unknown contract export was accepted')
print('CANONICAL_EXPORTS_OK')
"""
    result = subprocess.run([sys.executable, '-B', '-c', command], cwd=ROOT,
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'CANONICAL_EXPORTS_OK'
