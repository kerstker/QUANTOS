from decimal import Decimal as D
import numpy as np
import pytest
from services.research.ai_factory import HOUR
from services.research.ai_factory_store import FactoryStore, digest
from tools.ai_quant_factory import execute
from tools import ai_factory_process_comparison as module


@pytest.mark.parametrize('provider', ['NOT_CONFIGURED', 'CONFIGURED_UNVERIFIED'])
def test_comparison_replays_equal_budgets_and_rejects_lost_terminal_receipt(tmp_path, monkeypatch, provider):
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    root = tmp_path / 'data/cache'; out = root / 'ai_factory'
    (root / 'perpetual_alpha/input').mkdir(parents=True)
    t = np.arange(600, dtype=np.int64) * HOUR
    x = np.random.default_rng(3).normal(size=(600, 5)); x[:, 3] = .005
    y = x[:, 0] * .001
    with FactoryStore(out / 'factory.db') as store:
        store.create({'attempt': 1, 'mode': 'baseline', 'objective': 'BTC', 'budget': 3})
        execute(store, 1, (t, x, y), lambda spec, t, p: {'score': float(spec['id'])},
                {'input_hash': digest({})}, out)
    times = np.arange(0, 611 * HOUR, 60000, dtype=np.int64)
    market = {'times': times, 'prices': np.full(len(times), 100.), 'marks': np.full(len(times), 100.),
              'start': 0, 'end': 610 * HOUR,
              'funding': [{'time': i * 8 * HOUR, 'rate': D('.0001'), 'mark': D(100)} for i in range(76)],
              'daily_volume': {i: D('10000000') for i in range(26)}}
    monkeypatch.setattr(module, 'load_verified_minutes', lambda d: (None, None))
    monkeypatch.setattr(module, 'hourly_panel', lambda a: (t, x, y))
    monkeypatch.setattr(module, 'load_data', lambda **k: (market, None))
    monkeypatch.setattr(module, 'provider_status', lambda: {'status': provider})
    monkeypatch.setattr(module, 'propose', lambda *args: pytest.fail('baseline mode must not contact a language model'))
    result = module.run_comparison({'attempt': 1})
    assert len(result['rows']) == 6
    assert all(r['status'] == 'COMPLETED' and len(r['trials']) == 3 for r in result['rows'])
    assert result['ai_group_status'] == 'NOT_CONFIGURED' and result['authoritative'] is False
    assert module.run_comparison({'attempt': 1}) == result
    (out / '1/process-comparison.json').unlink()
    lost = out / '1/comparison-cases' / (result['rows'][0]['trials'][0]['trial_id'] + '.json')
    lost.unlink()
    with pytest.raises(ValueError, match='terminal receipt missing'):
        module.run_comparison({'attempt': 1})
    assert not lost.exists()
