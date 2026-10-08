"""Verifier statistics reuse the ledger's exact arithmetic without execution imports."""
from decimal import Inexact, ROUND_CEILING, getcontext, localcontext
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def test_statistics_import_has_no_execution_or_live_surface():
    root = Path(__file__).resolve().parents[2]
    code = "\n".join((
        "import sys",
        "sys.path.insert(0, " + json.dumps(str(root)) + ")",
        "from quantos.contracts.research_statistics import fixed_frequency_equity",
        "from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context",
        "for name in sys.modules:",
        "    assert not any(name == p or name.startswith(p + '.') for p in "
        "('services.execution', 'services.risk', 'live', 'trading')), name",
    ))
    result = subprocess.run([sys.executable, '-I', '-c', code], cwd=root,
        text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_statistics_preserve_original_bytes_under_hostile_caller_decimal_context():
    from quantos.contracts.research_statistics import DAY_MS, fixed_frequency_equity

    history = [{'timestamp': n * DAY_MS, 'sequence': 0,
                'equity': str(10000 + n * 37 + (n % 3) * 11)} for n in range(31)]
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_CEILING
        ambient.traps[Inexact] = True
        before = repr(getcontext())
        result = fixed_frequency_equity(history, start_ms=0,
            end_ms=30 * DAY_MS + 123, frequency_ms=DAY_MS)
        assert repr(getcontext()) == before
    raw = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    assert hashlib.sha256(raw).hexdigest() == 'f75804abbd93f236c35823824ea7046f801f43450d20ee560cc482ed02172167'


def test_producer_and_verifier_share_original_arithmetic_objects():
    from quantos.contracts import decimal_arithmetic, research_statistics
    from services.execution import perpetual_ledger_v2, perpetual_results

    assert perpetual_results.fixed_frequency_equity is research_statistics.fixed_frequency_equity
    assert perpetual_ledger_v2.ledger_decimal_context is decimal_arithmetic.ledger_decimal_context
    assert perpetual_ledger_v2.canonical_decimal is decimal_arithmetic.canonical_decimal
    assert perpetual_ledger_v2.DECIMAL_CONTEXT_VERSION == decimal_arithmetic.DECIMAL_CONTEXT_VERSION
