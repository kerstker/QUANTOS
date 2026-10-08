"""Historical component checks reuse production rules without fixed-root I/O."""
from copy import deepcopy
import json

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_bar_opening_origin import opening_case as opening_case
from tests.tools.test_gold_bar_rotation import setup, outcome


def test_account_opening_content_validation_preserves_native_output(opening_case):
    from tools import gold_bar_cash as cash
    f = opening_case
    expected = cash.open_account_origin(f.root, **f.args)
    area = f.root / 'strategy-cash-work' / _digest(f.bundle)
    binding = json.loads((area / 'binding.json').read_bytes())
    intent, record = cash._metadata(area / 'account-opening', f.root.stat().st_uid)
    raw = cash._origin_path(f.root, record['origin_file_sha256']).read_bytes()
    args = dict(binding=binding, intent=intent, record=record, origin_bytes=raw,
        bundle=f.bundle, account=f.account, source_commit='c' * 40, original_pilot=None, market_partition=None)
    assert cash.validate_account_opening_records(**args) == expected
    for key, value in [('market_partition', {'unrelated': True}), ('account', 'f' * 64),
                       ('origin_bytes', raw + b'\n')]:
        with pytest.raises(ValueError):
            cash.validate_account_opening_records(**dict(args, **{key: value}))
    changed = deepcopy(intent); changed['submission_enabled'] = 0
    with pytest.raises(ValueError):
        cash.validate_account_opening_records(**dict(args, intent=changed))


def test_rotation_content_replay_preserves_original_chain_without_root_io(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    base = args['base']; first = rotation.advance(**args, current=base, result=outcome(base))
    final = rotation.advance(**args, current=first, result=outcome(first, 'STRATEGY_EPISODE_SETTLED'))
    directory = tmp_path / 'strategy-bar-rotation' / _digest(base)
    rows = [json.loads(path.read_bytes()) for path in directory.iterdir()]
    active, ordered = rotation.verify_rotation_records(rows, base=base, account=args['account'], capacity=None)
    assert active == final and [row['ordinal'] for row in ordered] == [1, 2]
    for changed in [rows[1:], rows + rows[:1]]:
        with pytest.raises(ValueError):
            rotation.verify_rotation_records(changed, base=base, account=args['account'], capacity=None)
    changed = deepcopy(rows); changed[0]['ordinal'] = True
    with pytest.raises(ValueError):
        rotation.verify_rotation_records(changed, base=base, account=args['account'], capacity=None)
