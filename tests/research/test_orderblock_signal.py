import numpy as np
from services.research.orderblock import signals


def test_constant_market_never_generates_orderblocks():
    rows=np.tile([100.,101.,99.,100.],(140,1))
    result=signals(np.arange(140)*14400000,rows)
    assert not result['blocks'] and not result['signals']


def test_future_bars_cannot_rewrite_confirmations_or_signal_prefix():
    rng=np.random.default_rng(18)
    closes=100*np.exp(np.cumsum(rng.normal(0,.015,500)))
    opens=np.r_[100.,closes[:-1]]
    rows=np.column_stack((opens,np.maximum(opens,closes)+.1,np.minimum(opens,closes)-.1,closes))
    # Deterministic gaps exercise FVG creation, rather than a vacuous prefix.
    rows[::7,:]*=1.02
    times=np.arange(500)*14400000
    full=signals(times,rows);prefix=signals(times[:300],rows[:300])
    assert full['blocks']
    assert [r for r in full['blocks'] if r['confirmed_index']<300]==prefix['blocks']
    assert [r for r in full['signals'] if r['bar_index']<300]==prefix['signals']
    blocks={r['id']:r for r in full['blocks']}
    for r in full['signals']:
        assert all(blocks[k]['confirmed_index']<r['bar_index'] for k in r['block_ids'])
        assert r['available_time']>int(times[r['bar_index']])+14400000
    touched=[k for r in full['touches'] for k in r['block_ids']]
    assert len(touched)==len(set(touched))
