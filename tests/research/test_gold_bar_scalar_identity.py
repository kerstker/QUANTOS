"""Original complete bar identity without copying immutable scalar fields."""
import copy
import hashlib
import json
from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from services.research.gold_bar_candidates import CANDIDATES, CausalGoldBarEvaluator, evaluate
from tests.research.test_gold_bar_candidates import bars


@pytest.mark.parametrize('incremental', [False, True])
@pytest.mark.parametrize('gap', [False, True])
@pytest.mark.parametrize('candidate', CANDIDATES)
def test_complete_original_scalar_identity_needs_no_deepcopy(monkeypatch, incremental, gap, candidate):
    values = [replace(bar, open=Decimal('100.00'), high=Decimal('101.000'),
                      low=Decimal('99.0'), close=Decimal('1.00E+2')) for bar in bars(405)]
    if gap:
        del values[1]
    now = values[-2].available_ms
    # The final receipt is unavailable; a gap also discards the first bar.
    known = values[1:-1] if gap else values[:-1]
    original_wire = json.dumps([
        {key: str(value) if isinstance(value, Decimal) else value
         for key, value in asdict(bar).items()} for bar in known
    ], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    expected_digest = hashlib.sha256(original_wire).hexdigest()
    copied = []
    original_copy = copy.deepcopy

    def observed(value, *args, **kwargs):
        copied.append(type(value).__name__)
        return original_copy(value, *args, **kwargs)

    monkeypatch.setattr(copy, 'deepcopy', observed)
    inputs = CausalGoldBarEvaluator(values) if incremental else values
    result = evaluate(inputs, candidate=candidate, as_of_ms=now)
    assert result['bars_available'] == len(known)
    assert result['input_sha256'] == expected_digest
    assert not copied, f'Immutable scalar identity performs {len(copied)} deep copies'
