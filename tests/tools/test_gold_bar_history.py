"""Original Gold callbacks retain complete Bar records on their session store."""
import json

import pytest

from quantos.streaming_identity import json_parts
from tests.tools.test_gold_stream_history import original_arguments as original_arguments
from tests.tools.test_registered_research_run import registered_case as registered_case


def test_all_original_bar_histories_spill_and_complete_result_stays_identical(
        original_arguments, tmp_path, monkeypatch):
    from tools import gold_bar_cost_replay as comparison
    from services.research import gold_net_portfolio as portfolio
    from tools.gold_bar_experiment import _execute, _bytes
    from tools.gold_stream_history import GoldResearchHistory
    instances = []
    candidate, shared = comparison.GoldBarV2Strategy, portfolio.GoldNetTargetPortfolio
    def make_candidate(*args, **kwargs):
        strategy = candidate(*args, **kwargs)
        instances.append(strategy)
        return strategy
    def make_shared(*args, **kwargs):
        strategy = shared(*args, **kwargs)
        instances.append(strategy)
        return strategy
    monkeypatch.setattr(comparison, 'GoldBarV2Strategy', make_candidate)
    monkeypatch.setattr(portfolio, 'GoldNetTargetPortfolio', make_shared)
    expected = _execute(original_arguments)
    memory_bars = [tuple(s.bars) for s in instances]
    instances.clear()
    with GoldResearchHistory(tmp_path/'histories', maximum_history_bytes=256_000_000) as histories:
        actual = _execute(original_arguments, history=histories)
        assert json.loads(b''.join(json_parts(actual))) == json.loads(_bytes(expected))
        assert actual['result_hash'] == expected['result_hash']
        assert len(instances) == 4
        for strategy, original in zip(instances, memory_bars, strict=True):
            assert not isinstance(strategy.bars, (list, tuple))
            assert tuple(strategy.bars) == original
            store = strategy.bars.store
            assert store.db.execute("SELECT count(*) FROM arrays WHERE namespace='strategy_bars'").fetchone()[0] == len(original)
        strategy = instances[0]
        engine = strategy._signal_engine
        before = (engine._last_open, engine._last_available, len(engine._pending))
        from dataclasses import replace
        from services.research.gold_bar_candidates import INTERVAL_MS
        last = strategy.bars[-1]
        incoming = replace(last, open_ms=last.open_ms+INTERVAL_MS,
            close_ms=last.close_ms+INTERVAL_MS, available_ms=last.available_ms+INTERVAL_MS)
        with pytest.raises(ValueError, match='SEALED'):
            strategy.append_bars((incoming,))
        assert (engine._last_open, engine._last_available, len(engine._pending)) == before
        kept = instances[0].bars
    with pytest.raises(ValueError, match='CLOSED'):
        list(kept)


def test_growing_causal_bar_history_keeps_original_full_indicator_digest_and_small_heap(tmp_path):
    from dataclasses import replace
    from decimal import Decimal as D
    import tracemalloc
    from services.execution.perpetual_simulation import SimulationContext
    from services.research.gold_bar_candidates import INTERVAL_MS, evaluate
    from services.research.gold_bar_v2_strategy import GoldBarV2Strategy
    from tests.research.test_gold_causal_indicators import varied
    from tests.execution.test_perpetual_simulation_session import configuration
    from tools.gold_stream_history import GoldResearchHistory
    values = varied(2800)
    expected = evaluate(values, candidate='channel', as_of_ms=values[-1].available_ms)
    seed = tuple(values[:400])
    del values
    strategy = GoldBarV2Strategy(seed, candidate='channel', quantity=D('.1'),
        entry_timeout_ms=5000, max_quote_age_ms=30000)
    with GoldResearchHistory(tmp_path/'histories', maximum_history_bytes=64_000_000) as histories:
        session = histories.new_session(configuration(strategy), strategy=strategy)
        tracemalloc.start()
        try:
            def tick(now):
                return strategy(SimulationContext(now, D('1000'), {}, {}, {}, (), {}, D('1000')))
            assert tick(seed[-1].available_ms) == ()
            for index in range(400, 2800):
                bar = replace(seed[0], open_ms=index*INTERVAL_MS,
                    close_ms=(index+1)*INTERVAL_MS, available_ms=(index+1)*INTERVAL_MS+10,
                    close=D('100.00')+D((index*13)%37-18)/100)
                strategy.append_bars((bar,))
                assert tick(bar.available_ms) == ()
                if index % 400 == 0:
                    session._history_store.flush()
            actual = strategy._signal_engine.evaluate(candidate='channel', as_of_ms=bar.available_ms)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        session._history_store.flush()
        assert actual == expected
        assert len(strategy.bars) == 2800
        assert len(strategy.observations) == 2401
        assert len(strategy._signal_engine._pending) == 0
        assert len(strategy._signal_engine._window) <= 41
        assert peak < 4_000_000


def test_rejected_bar_append_preserves_sql_prefix_and_original_signal_state(tmp_path):
    from decimal import Decimal as D
    from services.execution.perpetual_simulation import SimulationContext
    from services.research.gold_bar_v2_strategy import GoldBarV2Strategy
    from tests.research.test_gold_causal_indicators import varied
    from tests.execution.test_perpetual_simulation_session import configuration
    from tools.gold_stream_history import GoldResearchHistory
    seed = varied(401)
    strategy = GoldBarV2Strategy(seed, candidate='channel', quantity=D('.1'),
        entry_timeout_ms=5000, max_quote_age_ms=30000)
    with GoldResearchHistory(tmp_path/'histories', maximum_history_bytes=64_000_000) as histories:
        session = histories.new_session(configuration(strategy), strategy=strategy)
        strategy(SimulationContext(seed[-1].available_ms, D('1000'), {}, {}, {}, (), {}, D('1000')))
        before = tuple(strategy.bars)
        expected = strategy._signal_engine.evaluate(candidate='channel', as_of_ms=seed[-1].available_ms)
        with pytest.raises(ValueError, match='BAR_RECEIPT_ALREADY_PROCESSED'):
            strategy.append_bars((seed[-1],))
        assert tuple(strategy.bars) == before
        assert strategy._signal_engine.evaluate(candidate='channel', as_of_ms=seed[-1].available_ms) == expected
        session._history_store.flush()
