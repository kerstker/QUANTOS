"""Streamed preparation preserves the original complete Gold experiment."""
import json
from copy import deepcopy

import pytest

from quantos.streaming_identity import json_parts
from tests.tools.test_gold_segmented_replay import segmented_arguments


def long_parts(tmp_path, monkeypatch, *, count=65):
    """Distinct causal quote windows; explicitly synthetic, not market evidence."""
    from tests.execution.test_perpetual_public_replay import START, sealed
    arguments, _, capture = segmented_arguments(tmp_path, monkeypatch)
    first = {k: v for k, v in arguments.items() if k not in ('input_schema', 'continuation_segments')}
    second = arguments['continuation_segments'][0]
    sample = capture['samples'][0]
    for index in range(count):
        part = deepcopy(first if index == 0 else second)
        if index < 30:
            part['journal_files'] = deepcopy(first['journal_files'])
        source = deepcopy(capture)
        source['schema_version'] = 'GOLD_PUBLIC_REPLAY_CAPTURE_V1'
        source['samples'] = []
        if index:
            for kind, lead in (('exchange', 10090), ('funding', 10060), ('candles', 10030)):
                received = START + index * 30000 - lead
                source['bootstrap'][kind].update(requested_at=received-1, received_at=received)
        for number in range(3):
            quote = deepcopy(sample)
            timestamp = START + (index * 3 + number) * 10000
            for kind, delay in (('mark', 1), ('depth', 2)):
                quote[kind].update(requested_at=timestamp, received_at=timestamp+delay)
            quote['mark']['payload']['time'] = timestamp
            quote['depth']['payload'].update(T=timestamp, E=timestamp, lastUpdateId=index*3+number+1)
            source['samples'].append(quote)
        source['exchange_after'].update(requested_at=timestamp+3, received_at=timestamp+4)
        part['capture'] = sealed(source)
        yield part


@pytest.mark.parametrize('count', [65, 500])
def test_more_than_legacy_segment_limit_executes_original_accounts(tmp_path, monkeypatch, count):
    from tools import gold_chunked_source as source
    from tools.gold_stream_history import GoldResearchHistory
    parts = iter(long_parts(tmp_path, monkeypatch, count=count))
    first = next(parts)
    from itertools import chain
    monkeypatch.setattr(source, 'source_identity', lambda: first['release_identity'])
    root = tmp_path/'originals'
    legacy_transport_bytes = 0
    def measured_parts():
        nonlocal legacy_transport_bytes
        from tools.gold_bar_experiment import _bytes, _encode
        for part in chain((first,), parts):
            legacy_transport_bytes += len(_bytes(_encode(part) if 'quantity' in part else part))
            yield part
    source.freeze(measured_parts(), root, maximum_bytes=32_000_000)
    if count == 500:
        from quantos.contracts.registered_research_run import MAX_INPUT_BYTES
        assert legacy_transport_bytes > MAX_INPUT_BYTES
    with pytest.raises(ValueError, match='NATIVE_EXECUTION_BOUND'):
        source.materialize(root)
    preparation_budget = 64_000_000 if count == 65 else 256_000_000
    with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
        with source.compare_source(root, tmp_path/'prepared', maximum_preparation_bytes=preparation_budget,
                                   history=history) as result:
            value = json.loads(b''.join(json_parts(result)))
            assert value['dataset_identity']['schema'] == 'GOLD_CHUNKED_BAR_QUOTE_BINDING_V2'
            assert value['dataset_identity']['input_segment_count'] == count
            assert len(value['dataset_identity']['segments']) == count
            assert len(history.accounts) == 4
            for engine in [value['cash_baseline'], *(t['simulation'] for t in value['trials'])]:
                assert len(engine['environment_segments']) == count
                assert engine['ending_state']['cashflow_identity']['residual'] == '0'
                assert engine['reconciliation']['initial_equity'] == '1000'
            assert value['economic_eligible'] is value['submission_enabled'] is False


@pytest.mark.parametrize('overlap', [False, True])
@pytest.mark.parametrize('portfolio', [False, True])
def test_disk_preparation_matches_entire_original_result(tmp_path, monkeypatch, overlap, portfolio):
    from tools import gold_segmented_replay as replay
    from tools.gold_bar_experiment import _encode, _bytes
    from quantos.contracts import gold_segmented_input as contract
    from quantos.contracts.registered_research_run import digest
    from tools.gold_input_history import GoldInputHistory
    from tools.gold_stream_history import GoldResearchHistory
    arguments, _, _ = segmented_arguments(tmp_path, monkeypatch, overlap=overlap)
    if portfolio:
        arguments['portfolio_plan'] = dict(quantities={n: '.1' for n in ('channel', 'trend', 'reversion')},
                                          max_notional='100')
    expected = json.loads(_bytes(replay.compare(arguments)))
    document = _encode(arguments)
    pairs = zip(contract.documents(document), contract.journals(document), strict=True)
    with GoldInputHistory(tmp_path/'prepared', maximum_bytes=64_000_000) as prepared:
        with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
            result = replay._compare_parts(arguments, pairs, input_transport_sha256=digest(document),
                                           preparation=prepared, history=history)
            assert json.loads(b''.join(json_parts(result))) == expected
            assert not isinstance(prepared.frames, (list, tuple))
            assert len(prepared.frames) == result['dataset_identity']['evaluated_frames']
            kept = result['dataset_identity']['segments']
    with pytest.raises(ValueError, match='CLOSED'):
        list(kept)


@pytest.mark.parametrize('damage', ['rewrite', 'extra', 'database'])
def test_full_source_and_prepared_cut_are_held_through_result_consumption(tmp_path, monkeypatch, damage):
    from tools import gold_chunked_source as source
    from tests.tools.test_gold_chunked_source import retained
    from tools.gold_stream_history import GoldResearchHistory
    root, _, _, _ = retained(tmp_path, monkeypatch)
    prepared = tmp_path/'prepared'
    with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
        with pytest.raises(ValueError):
            with source.compare_source(root, prepared, maximum_preparation_bytes=64_000_000,
                                       history=history) as result:
                json.loads(b''.join(json_parts(result)))
                if damage == 'rewrite':
                    path = next((root/'blobs').iterdir())
                    path.write_bytes(path.read_bytes())
                elif damage == 'extra':
                    (prepared/'extra').write_bytes(b'unbound')
                else:
                    path = prepared/'history.sqlite3'
                    path.write_bytes(path.read_bytes())
        with pytest.raises(ValueError, match='CLOSED'):
            list(result['dataset_identity']['segments'])


def test_preparation_budget_includes_working_original_journal_copies(tmp_path):
    from tools.gold_input_history import GoldInputHistory
    from services.execution.perpetual_simulation import SimulationFrame
    with GoldInputHistory(tmp_path/'prepared', maximum_bytes=1_000_000) as prepared:
        prepared.frames.append(SimulationFrame(100))
        prepared.flush()
        with pytest.raises(ValueError, match='GOLD_INPUT_BYTES'):
            prepared.reserve_journal(1_000_000)
        assert prepared.journal_bytes == 0
        prepared.seal()
        with pytest.raises(ValueError, match='SEALED'):
            prepared.frames.append(SimulationFrame(101))
        assert len(prepared.frames) == 1


def test_complete_report_seals_while_input_cut_is_live_and_cold_reads_after_close(tmp_path, monkeypatch):
    from tools import gold_chunked_source as source
    from tools.gold_chunked_result import write_report
    from tools.gold_stream_history import GoldResearchHistory
    from tests.tools.test_gold_chunked_source import retained
    from tests.tools.test_registered_gold_chunked_result import storage
    from services.research.gold_chunked_result_reader import open_gold_result
    from quantos.contracts.registered_research_run import digest
    from tools.perpetual_replay_research import source_identity
    root, _, first, _ = retained(tmp_path, monkeypatch, source=source_identity())
    with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
        with source.compare_source(root, tmp_path/'prepared', maximum_preparation_bytes=64_000_000,
                                   history=history) as result:
            expected = json.loads(b''.join(json_parts(result)))
            reference = write_report(result, tmp_path/'complete-report', storage=storage(),
                input_sha256=result['dataset_identity']['input_transport_sha256'],
                source_identity_sha256=digest(first['release_identity']))
    with open_gold_result(tmp_path/'complete-report', tmp_path/'index',
                          expected_manifest_sha256=reference['manifest_sha256'],
                          maximum_source_bytes=16_000_000, maximum_index_bytes=256_000_000) as cold:
        assert json.loads(b''.join(json_parts(cold))) == expected
        assert cold['result_hash'] == reference['result_hash']
