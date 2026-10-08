"""Original indicators, full causal identities and the real registered producer."""
from dataclasses import replace
from decimal import Decimal as D, localcontext
import json

import pytest

from services.research.gold_bar_candidates import Bar, CANDIDATES, INTERVAL_MS, evaluate
from tests.research.test_gold_bar_candidates import bars
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def varied(count=901):
    return [replace(bar, open=D('100.00'), high=D('103.00'), low=D('97.00'),
        close=D('100.00') + D((index * 13) % 37 - 18) / 100)
        for index, bar in enumerate(bars(count))]


@pytest.mark.parametrize('gap', [False, True])
def test_every_original_field_and_digest_match_across_receipts_future_stale_and_gap(gap):
    from services.research.gold_bar_candidates import CausalGoldBarEvaluator
    values = varied()
    if gap:
        del values[450]
    engine = CausalGoldBarEvaluator(values[:400])
    assert engine.evaluate(candidate='channel', as_of_ms=0) == evaluate(
        values, candidate='channel', as_of_ms=0)
    # Hold later original bars as unavailable inputs, then consume each once.
    engine.append_bars(values[400:])
    checkpoints = {399, 400, 449, 450, 451, 479, 850, len(values)-1}
    for index, bar in enumerate(values):
        for offset in (0, 7):
            now = bar.available_ms + offset
            for candidate in CANDIDATES:
                actual = engine.evaluate(candidate=candidate, as_of_ms=now)
                if index in checkpoints:
                    assert actual == evaluate(values, candidate=candidate, as_of_ms=now)
    now = values[-1].available_ms + INTERVAL_MS + 1
    for candidate in CANDIDATES:
        assert engine.evaluate(candidate=candidate, as_of_ms=now) == evaluate(
            values, candidate=candidate, as_of_ms=now)
    assert len(engine._window) <= 41 and not engine._pending


def test_full_recursive_ema_cannot_be_replaced_by_recent_warmup():
    from services.research.gold_bar_candidates import CausalGoldBarEvaluator
    values = [replace(bar, open=D('110'), high=D('111'), low=D('109'), close=D('110'))
        if index < 400 else bar for index, bar in enumerate(bars(801))]
    values[-1] = replace(values[-1], close=D('100.001'))
    now = values[-1].available_ms
    original = evaluate(values, candidate='trend', as_of_ms=now)
    truncated = evaluate(values[-401:], candidate='trend', as_of_ms=now)
    assert original['entry_side'] is None and truncated['entry_side'] == 'BUY'
    assert CausalGoldBarEvaluator(values).evaluate(candidate='trend', as_of_ms=now) == original


def test_original_decimal_context_and_spellings_survive_incremental_processing():
    from services.research.gold_bar_candidates import CausalGoldBarEvaluator
    values = varied(501)
    now = values[-1].available_ms
    expected = {name:evaluate(values, candidate=name, as_of_ms=now) for name in CANDIDATES}
    with localcontext() as context:
        context.prec = 6
        engine = CausalGoldBarEvaluator(values)
        assert {name:engine.evaluate(candidate=name, as_of_ms=now) for name in CANDIDATES} == expected


def test_repeated_quotes_do_not_revalidate_or_rehash_consumed_bars(monkeypatch):
    from services.research.gold_bar_candidates import CausalGoldBarEvaluator
    original = Bar.validate
    validated = []
    def observed(bar):
        validated.append(bar.open_ms)
        return original(bar)
    values = varied(901)
    monkeypatch.setattr(Bar, 'validate', observed)
    engine = CausalGoldBarEvaluator(values[:400])
    engine.evaluate(candidate='trend', as_of_ms=values[399].available_ms)
    engine.append_bars(values[400:])
    first = engine.evaluate(candidate='trend', as_of_ms=values[-1].available_ms)
    for offset in range(1, 1001):
        result = engine.evaluate(candidate='trend', as_of_ms=values[-1].available_ms + offset)
        assert result['input_sha256'] == first['input_sha256']
    assert validated == [b.open_ms for b in values]
    assert len(engine._window) == 41 and engine._count == 901 and not engine._pending


def test_rejected_append_and_clock_leave_previous_causal_result_intact():
    from services.research.gold_bar_candidates import CausalGoldBarEvaluator
    values = varied(403)
    engine = CausalGoldBarEvaluator(values[:401])
    now = values[400].available_ms
    expected = engine.evaluate(candidate='trend', as_of_ms=now)
    for incoming in (values[400:], [values[401], values[401]],
            [values[401], replace(values[402], high=D('1'))],
            [replace(values[401], available_ms=values[402].available_ms+1), values[402]]):
        with pytest.raises(ValueError):
            engine.append_bars(incoming)
        assert engine.evaluate(candidate='trend', as_of_ms=now) == expected
    with pytest.raises(ValueError):
        engine.evaluate(candidate='trend', as_of_ms=now-1)
    with pytest.raises(ValueError):
        engine.evaluate(candidate='unknown', as_of_ms=now+1)
    assert engine.evaluate(candidate='trend', as_of_ms=now) == expected
    engine.append_bars(values[401:])
    assert engine.evaluate(candidate='trend', as_of_ms=values[-1].available_ms) == evaluate(
        values, candidate='trend', as_of_ms=values[-1].available_ms)


def test_original_queue_uses_causal_signals_and_keeps_original_four_trials_and_recovery(
        tmp_path, monkeypatch, technical_arguments):
    from services.research import gold_bar_v2_strategy as strategy, gold_net_portfolio as portfolio
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tests.research.test_gold_segmented_registration import request
    from tests.research.test_registered_research_job import queued
    from tools import registered_gold_collection as bridge, registered_research_job as job
    from tools import registered_research_run as native
    root = request(tmp_path, monkeypatch, technical_arguments, portfolio=True, declared=True, funding=True)
    scanned = []
    original = evaluate
    def observed(value, **kwargs):
        if isinstance(value, (list, tuple)):
            scanned.append(len(value))
        return original(value, **kwargs)
    monkeypatch.setattr(strategy, 'evaluate', observed)
    monkeypatch.setattr(portfolio, 'evaluate', observed)
    producers = []
    original_native = native._native_run
    def native_run(**kwargs):
        original_execute = kwargs['execute']
        def execute():
            producers.append(1)
            return original_execute()
        return original_native(**{**kwargs, 'execute':execute})
    monkeypatch.setattr(native, '_native_run', native_run)
    registry, queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research', {'attempt':1}, 1, invocation_id='causal-gold')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    # Three constructors in the producer and three in its original diagnostic
    # review replay. The original native invocation itself runs only once.
    assert len(scanned) <= 6, scanned
    assert producers == [1]
    project, database = job.workspace_paths()
    cut = ProjectResearchEvidenceReader(project, registry_database=database)()
    assert cut['attempt_count'] == 4 and {r['status'] for r in cut['attempts']} == {'PASSED'}
    directory = project/'runs'/bridge.invocation_id(1)
    report = (directory/'archive/report.json').read_bytes()
    saved = json.loads(report)
    assert saved['trials'][0]['simulation']['reconciliation']['funding'] == '-0.3'
    assert saved['trials'][0]['simulation']['reconciliation']['residual'] == '0'
    assert saved['economic_eligible'] is saved['submission_enabled'] is False
    assert (root.parent.parent/'results/1/report.json').read_bytes() == report
    before = database.read_bytes()
    uid = queue.submit('gold_bar_recover', {'attempt':1}, 2, invocation_id='causal-gold-recover')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert (directory/'archive/report.json').read_bytes() == report and database.read_bytes() == before
    assert producers == [1]


def test_nonmonotone_original_receipts_preserve_full_history_fallback():
    from services.research.gold_bar_v2_strategy import GoldBarV2Strategy
    from services.research.gold_net_portfolio import GoldNetTargetPortfolio
    values = varied(403)
    values[-2] = replace(values[-2], available_ms=values[-1].available_ms+1)
    strategy = GoldBarV2Strategy(values[:401], candidate='trend', quantity=D('.1'),
                               entry_timeout_ms=5000, max_quote_age_ms=30000)
    portfolio = GoldNetTargetPortfolio(values[:401], quantities={n:D('.1') for n in CANDIDATES},
                                      max_notional=D('100'))
    for owner in (strategy, portfolio):
        assert owner._signal_engine is not None
        owner.append_bars(values[401:])
        assert owner._signal_engine is None and owner.bars == tuple(values)


def test_original_full_comparison_including_portfolio_is_byte_identical_to_reference(
        tmp_path, monkeypatch):
    from services.research import gold_bar_v2_strategy as strategy, gold_net_portfolio as portfolio
    from tools.gold_bar_experiment import _bytes, _execute
    from tests.tools.test_gold_segmented_replay import funding_arguments
    arguments = funding_arguments(tmp_path, monkeypatch)
    arguments['portfolio_plan'] = dict(quantities={n:'0.1' for n in CANDIDATES}, max_notional='100')
    actual = _execute(arguments)
    monkeypatch.setattr(strategy, 'causal_evaluator', lambda values: None)
    monkeypatch.setattr(portfolio, 'causal_evaluator', lambda values: None)
    reference = _execute(arguments)
    assert _bytes(actual) == _bytes(reference)
