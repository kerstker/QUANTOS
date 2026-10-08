"""Triple-barrier labels + meta-labeling (tests-first).

Triple-barrier (Lopez de Prado): from each entry, the first of three barriers decides the
label — upper (profit target) -> +1, lower (stop) -> -1, vertical (max holding) -> 0. The
barriers are scaled by the entry's trailing realized volatility, which is knowable AT the
entry (no lookahead); only the OUTCOME walks forward (that is the label, by definition).

Meta-labeling: the primary signal gives DIRECTION (+1/-1); the meta layer decides whether
acting on it was right (1) or not (0) vs the realized barrier label. meta_label_stats'
precision = "how often acting on the primary was correct" — the should-I-bet signal.
"""
from services.research.labeling import triple_barrier_labels, meta_labels, meta_label_stats

# ~1% oscillation for the vol warm-up, then a clear directional move.
WARM = [100.0, 101.0, 100.0, 101.0, 100.0, 101.0]


def _entry(labels, idx):
    return next((l for l in labels if l["entry"] == idx), None)


def test_upper_barrier_gives_plus_one():
    closes = WARM + [103.0, 106.0, 110.0]                 # strong rise after the warm-up
    labels = triple_barrier_labels(closes, pt_mult=1.0, sl_mult=1.0, max_hold=4, vol_window=5)
    e = _entry(labels, 5)                                 # entry at last warm bar (=101)
    assert e is not None and e["label"] == 1 and e["hit"] == "upper"


def test_lower_barrier_gives_minus_one():
    closes = WARM + [97.0, 94.0, 90.0]                    # strong fall
    labels = triple_barrier_labels(closes, pt_mult=1.0, sl_mult=1.0, max_hold=4, vol_window=5)
    e = _entry(labels, 5)
    assert e is not None and e["label"] == -1 and e["hit"] == "lower"


def test_vertical_barrier_timeout_gives_zero():
    closes = WARM + [100.5, 100.4, 100.6, 100.5]          # stays inside the barriers
    labels = triple_barrier_labels(closes, pt_mult=3.0, sl_mult=3.0, max_hold=3, vol_window=5)
    e = _entry(labels, 5)
    assert e is not None and e["label"] == 0 and e["hit"] == "vertical"


def test_fail_closed_on_zero_vol_and_thin_history():
    flat = [100.0] * 12                                   # zero vol -> no barriers -> skipped
    assert triple_barrier_labels(flat, vol_window=5) == []
    assert triple_barrier_labels([100.0, 101.0], vol_window=5) == []   # too short -> empty


def test_deterministic():
    closes = WARM + [103.0, 106.0, 110.0]
    a = triple_barrier_labels(closes, pt_mult=1.0, sl_mult=1.0, max_hold=4, vol_window=5)
    b = triple_barrier_labels(closes, pt_mult=1.0, sl_mult=1.0, max_hold=4, vol_window=5)
    assert a == b


def test_meta_labels_and_precision():
    tb = [1, 1, -1, 0, 1]                                 # realized barrier labels
    dirs = [1, -1, -1, 1, 1]                              # primary directions (bets)
    assert meta_labels(dirs, tb) == [1, 0, 1, 0, 1]       # 1 only when direction matched a win
    stats = meta_label_stats(dirs, tb)
    assert stats["n_acted"] == 5 and stats["precision"] == 0.6   # 3 of 5 bets correct
    assert stats["coverage"] == 1.0


def test_meta_no_bet_when_direction_zero():
    tb = [1, -1, 1]
    dirs = [0, 0, 1]                                      # first two = no bet
    assert meta_labels(dirs, tb) == [0, 0, 1]
    stats = meta_label_stats(dirs, tb)
    assert stats["n_acted"] == 1 and stats["precision"] == 1.0 and stats["coverage"] == round(1 / 3, 4)
