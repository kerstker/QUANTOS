"""断点1 (方案A) — the leakage_ok evidence SOURCE: a trailing-window SIGNAL_OP is detected as PIT-safe (True),
a peeking compute is caught (False), and an unassessable window is honestly None (evaluator stays fail-closed)."""
from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue
from services.research.leakage_evidence import (
    leakage_ok_over_times, signal_compute_fn, signal_leakage_ok, volume_leakage_ok,
)


def _bars(closes, inst="BTC"):
    out = []
    for i, c in enumerate(closes):
        c = float(c)
        out.append(Bar(instrument_id=inst, event_time=i, knowledge_time=i,
                       open=c, high=c + 1.0, low=max(0.1, c - 1.0), close=c, volume=1.0))
    return out


def test_trailing_signal_op_is_leakage_ok():
    bars = _bars([10, 11, 12, 11, 13, 14, 13, 15, 16, 15])
    # momentum is a pure trailing-window op -> every sampled decision time must be PIT-safe.
    assert signal_leakage_ok("momentum", 3, bars, target_times=[5, 6, 7]) is True
    assert signal_leakage_ok("zscore", 3, bars, target_times=[5, 6, 7]) is True


def test_peeking_compute_is_caught_as_leakage():
    bars = _bars([10, 11, 12, 13, 14, 15, 16, 17])

    def leaky(rows):
        rows = sorted(rows, key=lambda b: b.event_time)
        out = []
        for i, b in enumerate(rows):
            nxt = rows[i + 1].close if i + 1 < len(rows) else b.close   # PEEK at the future bar
            out.append(FeatureValue(feature_id="leaky", instrument_id=b.instrument_id,
                                    event_time=b.event_time, knowledge_time=b.knowledge_time, value=float(nxt)))
        return out

    assert leakage_ok_over_times(leaky, bars, target_times=[3, 4], feature_id="leaky") is False


def test_no_assessable_time_is_none_fail_closed():
    bars = _bars([10, 11, 12, 13, 14])
    cf = signal_compute_fn("momentum", 3)
    assert leakage_ok_over_times(cf, bars, target_times=[]) is None    # nothing assessed -> honest None


def test_trailing_volume_op_is_leakage_ok():
    closes = [10, 11, 12, 11, 13, 14, 13, 15, 16, 15]
    vols = [5, 6, 4, 7, 5, 8, 6, 9, 7, 10]
    bars = [Bar(instrument_id="BTC", event_time=i, knowledge_time=i,
                open=float(c), high=float(c) + 1.0, low=max(0.1, float(c) - 1.0), close=float(c), volume=float(v))
            for i, (c, v) in enumerate(zip(closes, vols))]
    # a VOLUME_OP reads only trailing (closes, volumes) -> PIT-safe at every sampled decision time.
    assert volume_leakage_ok("vol_momentum", 3, bars, target_times=[5, 6, 7]) is True
