"""Exact persisted identities must be readable without the execution layer."""
from decimal import Decimal
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest


def test_identity_contract_import_has_no_execution_or_live_surface():
    root = Path(__file__).resolve().parents[2]
    code = "\n".join((
        "import sys",
        "sys.path.insert(0, " + json.dumps(str(root)) + ")",
        "from quantos.contracts.backtest_identity import RunManifestV2, exact_identity_hash",
        "for name in sys.modules:",
        "    assert not any(name == p or name.startswith(p + '.') for p in "
        "('services.execution', 'services.risk', 'live', 'trading')), name",
        "assert exact_identity_hash(False) == "
        "'38d36ce273c7be1c4d771030a94ce9d9760ca762732855090017581928dacb85'",
    ))
    result = subprocess.run([sys.executable, '-I', '-c', code], cwd=root,
        text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(('value', 'expected'), [
    (False, '38d36ce273c7be1c4d771030a94ce9d9760ca762732855090017581928dacb85'),
    (0, 'd8117918f245aedda9bf1236d1eb30e8958c763caca42418e6e969d561124f07'),
    (0.0, '632e91f3920f9c12d9e86412bfb88fabeb660a67802a0bba2d7136a1470a7907'),
    ('0', 'd1c298f193c3b1e911866233c3287cf4ec47b98922d54a19b7c2deea5c947d8e'),
    (Decimal('0.00'), 'de48d8ef2e7247399e3bc9f3fffd284102a58081aa0a41db7429c19febf52280'),
    (b'\x00\xff', '97275ab6811b6e0610910689624e8d37b7cf63a2b6914c6fb7f58579dd88f95d'),
    ({'alpha': Decimal('1.2300'), 3: [-0.0, math.nextafter(0.1, 1.0), True, None],
      'set': frozenset(('b', 'a'))},
     '15bbf8198966bfdb6978154a7bdca4e4cfb51b5b1a3d1e9b3ca719831f1c8eaf'),
])
def test_exact_identity_matches_pre_extraction_persisted_vectors(value, expected):
    from quantos.contracts.backtest_identity import exact_identity_hash
    assert exact_identity_hash(value) == expected


def test_old_producer_and_neutral_reader_share_exact_manifest_implementation():
    from quantos.contracts import backtest_identity as neutral
    from services.execution import backtest_authority as original
    from tests.execution.test_backtest_authority import _manifest

    assert original.RunManifestV2 is neutral.RunManifestV2
    assert original.build_run_manifest_v2 is neutral.build_run_manifest_v2
    assert original.exact_identity_hash is neutral.exact_identity_hash
    manifest = _manifest()
    restored = neutral.RunManifestV2.from_dict(manifest.as_dict())
    assert restored == manifest
    assert restored.run_id == 'e8c76422ebbd15203faac9c1bb13aac486dd0360aee569899c371715ab96ec51'
    assert original.is_authoritative_backtest_record({'run_id': restored.run_id}) is False
