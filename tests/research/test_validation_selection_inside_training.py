"""Selection belongs to the TRAIN folds; the OOS stream is reported once, honestly.

Two confirmed defects in ``run_research_validation`` are locked here:

(A) ``best`` was chosen with the very same out-of-sample per-fold returns that the
    call then reported as the headline OOS Sharpe, so the reported out-of-sample
    number was selection-biased.  Selection must use the walk-forward TRAIN halves.
(B) the batch PBO series was built by zipping the FULL ``bars`` list against the
    shorter concatenated OOS returns, so every PBO event time was taken from the
    train-heavy head of the sample instead of each OOS window's own bar.

``ScriptedPort`` below scripts a per-window equity path, so a candidate can be
made the TRAIN-fold winner while another wins on the OOS folds.
"""

from __future__ import annotations

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.data import Bar
from services.alpha.catalog import AlphaCatalog
from services.research.backtest_engine.costs import CostModel
from services.research.ports.backtest import BacktestOutcome
from services.research.validation.pipeline import run_research_validation
from services.validation.gate import ValidationGate
from services.validation.performance import returns_from_equity, sharpe
from services.validation.walk_forward import walk_forward_splits


N_BARS, TRAIN_SIZE, TEST_SIZE = 18, 6, 3
TARGET_BY_CANDIDATE = {"train_winner": 100, "oos_only": 50}
# per-bar rate seeded per (leg, candidate); halved on odd event times so the
# stream has dispersion instead of a zero-variance constant
BASE_RATE = {
    ("train", "train_winner"): 0.02,
    ("train", "oos_only"): -0.005,
    ("oos", "train_winner"): -0.02,
    ("oos", "oos_only"): 0.02,
}


def _bars(n=N_BARS):
    return [
        Bar(
            instrument_id="X",
            event_time=index + 1,
            knowledge_time=index + 1,
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
            volume=1000,
        )
        for index in range(n)
    ]


def _rate(leg, candidate, event_time):
    base = BASE_RATE[(leg, candidate)]
    return base if event_time % 2 else base / 2.0


def _outcome(initial_capital, rates):
    curve, equity = [], float(initial_capital)
    for rate in rates:
        equity *= 1.0 + rate
        curve.append(equity)
    return BacktestOutcome(initial_capital=initial_capital, equity_curve=tuple(curve))


class ScriptedPort:
    """Window-scripted backtest port: window length picks the leg, target the candidate."""

    def __init__(self):
        self.calls = []

    def run(self, bars, target_shares, *, initial_capital=1_000_000.0, cost=CostModel()):
        candidate = next(
            name for name, target in TARGET_BY_CANDIDATE.items()
            if target == int(target_shares[0])
        )
        leg = "train" if len(bars) == TRAIN_SIZE else "oos"
        rates = [_rate(leg, candidate, bar.event_time) for bar in bars]
        outcome = _outcome(initial_capital, rates)
        self.calls.append(
            {"leg": leg, "candidate": candidate, "outcome": outcome}
        )
        return outcome


class RecordingGate(ValidationGate):
    """ValidationGate that records the single OOS metric it was asked to arbitrate."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def evaluate(self, subject_id, **kwargs):
        self.seen.append((subject_id, kwargs["oos_sharpe"]))
        return super().evaluate(subject_id, **kwargs)


def _catalog(candidate_ids):
    catalog = AlphaCatalog()
    for candidate_id in candidate_ids:
        catalog.register(
            AlphaDefinition(
                alpha_id=candidate_id,
                name=candidate_id,
                factor_ids=(f"factor-{candidate_id}",),
                universe="test",
            )
        )
    return catalog


def _targets(bars, candidate_ids):
    return [
        [TARGET_BY_CANDIDATE[candidate_id]] * len(bars)
        for candidate_id in candidate_ids
    ]


def _run(subject_id, candidate_ids, port, gate=None, bars=None):
    bars = bars or _bars()
    return run_research_validation(
        subject_id,
        bars,
        _targets(bars, candidate_ids),
        candidate_ids=candidate_ids,
        catalog=_catalog(candidate_ids),
        backtest=port,
        train_size=TRAIN_SIZE,
        test_size=TEST_SIZE,
        cost=CostModel(0, 0),
        leakage_ok=True,
        gate=gate,
    )


def test_selection_uses_the_train_folds_not_the_reported_oos_stream():
    port, gate = ScriptedPort(), RecordingGate()

    result = _run("train_winner", ("train_winner", "oos_only"), port, gate)

    assert result.subject_id == "train_winner"
    # the single OOS report belongs to the train-selected candidate, not to the
    # candidate that happened to look best on the OOS folds themselves
    oos_stream = [
        value
        for call in port.calls
        if call["leg"] == "oos" and call["candidate"] == "train_winner"
        for value in returns_from_equity(call["outcome"].equity_curve)
    ]
    exclusive_oos = [
        value
        for call in port.calls
        if call["leg"] == "oos" and call["candidate"] == "oos_only"
        for value in returns_from_equity(call["outcome"].equity_curve)
    ]
    assert sum(exclusive_oos) > sum(oos_stream)          # oos_only is the OOS winner
    assert len(gate.seen) == 1                           # the OOS stream is reported once
    assert gate.seen[0][0] == "train_winner"
    assert gate.seen[0][1] == pytest.approx(sharpe(oos_stream))
    assert gate.seen[0][1] < 0.0


def test_subject_id_cannot_name_the_oos_fold_winner():
    port = ScriptedPort()

    with pytest.raises(ValueError, match="selected best"):
        _run("oos_only", ("train_winner", "oos_only"), port)


def test_every_candidate_is_scored_on_train_and_oos_windows_only():
    port = ScriptedPort()

    _run("train_winner", ("train_winner", "oos_only"), port)

    splits = walk_forward_splits(N_BARS, TRAIN_SIZE, TEST_SIZE)
    per_candidate = sorted(
        (leg, len(window))
        for leg, windows in (
            ("train", [train for train, _test in splits]),
            ("oos", [test for _train, test in splits]),
        )
        for window in windows
    )
    observed = sorted(
        (call["leg"], len(call["outcome"].equity_curve)) for call in port.calls
    )
    assert observed == sorted(per_candidate * len(TARGET_BY_CANDIDATE))


def test_batch_pbo_evidence_is_stamped_with_the_oos_windows_own_bars():
    bars = _bars()
    splits = walk_forward_splits(len(bars), TRAIN_SIZE, TEST_SIZE)
    # every OOS fold realises one return per bar after its first equity observation
    oos_event_times = tuple(
        bars[index].event_time for _train, test in splits for index in test[1:]
    )
    train_only_times = {
        bars[index].event_time for index in range(TRAIN_SIZE)
    }

    result = _run("train_winner", ("train_winner", "oos_only"), ScriptedPort(), bars=bars)

    assert result.pbo_evidence is not None
    assert result.pbo_evidence.aligned_event_times == oos_event_times
    # the head of the sample is train-only and must never label an OOS return
    assert train_only_times.isdisjoint(result.pbo_evidence.aligned_event_times)
    assert result.pbo_evidence.aligned_period_count == len(oos_event_times)
