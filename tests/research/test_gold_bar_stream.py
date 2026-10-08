import json
from copy import deepcopy

import pytest

from services.research.gold_bar_stream import GoldBarStream
from tests.research.test_gold_bar_candidates import bars, INTERVAL_MS


def packet(closed=True):
    start = 401 * INTERVAL_MS
    return {'stream': 'xauusdt@kline_15m', 'data': {'e': 'kline', 'E': start + INTERVAL_MS + 10,
        's': 'XAUUSDT', 'k': {'t': start, 'T': start + INTERVAL_MS - 1, 's': 'XAUUSDT',
        'i': '15m', 'x': closed, 'o': '100', 'h': '110', 'l': '99', 'c': '109'}}}


def test_only_closed_bar_produces_causal_three_candidate_decisions():
    stream = GoldBarStream(bars())
    p = packet(False)
    assert stream.ingest(json.dumps(p), received_at_ms=p['data']['E'] + 5)['status'] == 'OPEN_BAR'
    p['data']['k']['x'] = True
    result = stream.ingest(json.dumps(p), received_at_ms=p['data']['E'] + 10)
    assert result['status'] == 'CLOSED_BAR'
    assert len(result['decisions']) == 3
    assert result['decisions'][0]['entry_side'] == 'BUY'
    assert stream.bars[-1].available_ms == p['data']['E'] + 10
    assert stream.ingest(json.dumps(p), received_at_ms=p['data']['E'] + 11)['status'] == 'DUPLICATE_CLOSED_BAR'
    assert len(stream.bars) == 402


def test_conflicting_closed_bar_blocks_stream():
    stream = GoldBarStream(bars())
    p = packet()
    stream.ingest(json.dumps(p), received_at_ms=p['data']['E'])
    p['data']['k']['c'] = '108'
    with pytest.raises(ValueError):
        stream.ingest(json.dumps(p), received_at_ms=p['data']['E'] + 1)
    with pytest.raises(ValueError, match='BLOCKED'):
        stream.ingest(json.dumps(packet()), received_at_ms=p['data']['E'] + 2)


@pytest.mark.parametrize('kind', ['future', 'stale', 'wrong_symbol', 'not_boolean'])
def test_bad_clock_identity_or_finality_rejected(kind):
    stream = GoldBarStream(bars())
    p = deepcopy(packet())
    now = p['data']['E']
    if kind == 'future':
        now -= 1
    elif kind == 'stale':
        now += 30001
    elif kind == 'wrong_symbol':
        p['data']['k']['s'] = 'BTCUSDT'
    else:
        p['data']['k']['x'] = 'true'
    with pytest.raises(ValueError):
        stream.ingest(json.dumps(p), received_at_ms=now)
