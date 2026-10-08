"""Budgeted original Gold source execution; no natural-time or authority claim."""
import json

import pytest

from quantos.streaming_identity import json_parts
from tests.tools.test_gold_stream_input import long_parts
from tests.tools.test_gold_bar_observed_replay import continuous_inputs
from tests.execution.test_perpetual_long_replay import budget


def test_first_part_with_two_closed_bars_does_not_invent_second_input_segment(tmp_path, monkeypatch):
    from tools.gold_bar_experiment import JOURNAL_FILES
    from tools.gold_segmented_replay import _compare_parts
    from decimal import Decimal as D
    journal, capture, _ = continuous_inputs(tmp_path, monkeypatch)
    arguments = dict(capture=capture,
        quantity=D('.1'), initial_collateral=D('1000'), fee_rate=D('.001'), release_identity={'fixture':True})
    result = _compare_parts(arguments, [(arguments, {name:(journal/name).read_bytes() for name in JOURNAL_FILES})],
                            input_transport_sha256='f' * 64)
    assert len(result['dataset_identity']['closed_decision_times']) == 2
    assert len(result['dataset_identity']['segments']) == 1
    assert result['dataset_identity']['input_segment_count'] == 1


def test_explicit_source_budget_reaches_all_original_accounts_and_cold_report(tmp_path, monkeypatch):
    from tools import gold_chunked_source as source
    from tools.gold_stream_history import GoldResearchHistory
    from tools.gold_chunked_result import write_report
    from services.research.gold_chunked_result_reader import open_gold_result
    from tests.tools.test_registered_gold_chunked_result import storage
    from quantos.contracts.registered_research_run import digest
    from tools.perpetual_replay_research import source_identity
    from itertools import chain
    parts = iter(long_parts(tmp_path, monkeypatch))
    first = next(parts)
    first['release_identity'] = source_identity()
    first['portfolio_plan'] = dict(quantities={name:'.1' for name in ('channel','trend','reversion')},
                                    max_notional='100')
    root = tmp_path/'originals'
    source.freeze(chain((first,), parts), root, maximum_bytes=32_000_000)
    frozen_budget = budget(epochs=65)
    with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
        with source.compare_source(root, tmp_path/'prepared', maximum_preparation_bytes=64_000_000,
                                   history=history, replay_budget=frozen_budget) as result:
            expected = json.loads(b''.join(json_parts(result)))
            assert len(history.accounts) == 5
            assert expected['dataset_identity']['schema'] == 'GOLD_CHUNKED_BAR_QUOTE_BINDING_V3'
            assert expected['dataset_identity']['replay_budget'] == frozen_budget
            assert expected['dataset_identity']['input_transport_sha256'] == digest(dict(
                schema='QUANTOS_GOLD_BUDGETED_SOURCE_EXECUTION_INPUT_V1',
                manifest_sha256=expected['dataset_identity']['source_binding']['manifest_sha256'],
                replay_budget=frozen_budget))
            for engine in [expected['cash_baseline'], expected['portfolio']['simulation'],
                           *(t['simulation'] for t in expected['trials'])]:
                assert engine['schema_version'] == 'LINEAR_PERPETUAL_FORWARD_SIMULATION_V4'
                assert engine['replay_budget'] == frozen_budget
                assert len(engine['environment_segments']) == 65
                assert engine['reconciliation']['initial_equity'] == '1000'
                assert engine['reconciliation']['residual'] == '0'
            reference = write_report(result, tmp_path/'report', storage=storage(),
                input_sha256=expected['dataset_identity']['input_transport_sha256'],
                source_identity_sha256=digest(first['release_identity']))
    with open_gold_result(tmp_path/'report', tmp_path/'index',
                          expected_manifest_sha256=reference['manifest_sha256'],
                          maximum_source_bytes=16_000_000, maximum_index_bytes=256_000_000) as cold:
        assert json.loads(b''.join(json_parts(cold))) == expected


def test_source_epoch_budget_rejected_before_original_account_creation(tmp_path, monkeypatch):
    from tools import gold_chunked_source as source
    from tools.gold_stream_history import GoldResearchHistory
    from itertools import chain
    parts = iter(long_parts(tmp_path, monkeypatch))
    first = next(parts)
    monkeypatch.setattr(source, 'source_identity', lambda:first['release_identity'])
    root = tmp_path/'originals'
    source.freeze(chain((first,), parts), root, maximum_bytes=32_000_000)
    with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
        with pytest.raises(ValueError, match='REPLAY.*BUDGET'):
            with source.compare_source(root, tmp_path/'prepared', maximum_preparation_bytes=64_000_000,
                                       history=history, replay_budget=budget(epochs=64)):
                pass
        assert history.accounts == []
