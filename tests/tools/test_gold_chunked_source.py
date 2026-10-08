"""Immutable out-of-line originals must retain the existing native semantics."""
import json

import pytest

from tests.tools.test_gold_prefix_continuation import pieces
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def test_declared_funding_window_is_retained_before_the_first_settlement(tmp_path, monkeypatch):
    from decimal import Decimal
    from tests.tools.test_gold_public_segment_producer import native_session
    from tools import gold_chunked_source as tool
    from tools.gold_chunked_source import NativeSource
    bars, identity, summary = native_session(tmp_path, monkeypatch, funding_after_ms=500_000)
    monkeypatch.setattr(tool, 'source_identity', lambda: identity)
    root = tmp_path/'declared-funding-source'
    tool.from_session(tmp_path/'session', bars, root,
        segment_count=summary['segment_count'], release_identity=identity,
        quantity=Decimal('0.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('0.001'),
        settlement_window_ms=1000, maximum_bytes=32_000_000)
    with NativeSource(root) as originals:
        assert originals.document.get('settlement_window_ms') == 1000
        assert originals.reader.manifest['schema'] == 'QUANTOS_GOLD_CHUNKED_SOURCE_V2'
        assert originals.document['input_schema'] == 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V3'
        from tools.gold_bar_experiment import _decode, _execute
        from tools.gold_input_history import GoldInputHistory
        from tools.gold_stream_history import GoldResearchHistory
        with GoldInputHistory(tmp_path/'prepared', maximum_bytes=64_000_000) as preparation:
            with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
                report = _execute(_decode(originals.document), history=history,
                    source_context=(originals, preparation))
                cash = report['trials'][0]['simulation']['ending_state']['cashflow_identity']
                assert cash['funding'] == '-0.01'
                assert cash['ending_equity'] == '999.95' and cash['residual'] == '0'
                assert report['economic_eligible'] is report['submission_enabled'] is False


def test_funding_policy_header_without_settlement_keeps_original_nonfunding_mode(tmp_path, monkeypatch):
    from tools import gold_chunked_source as tool
    from tools.gold_chunked_source import NativeSource
    from tools.gold_bar_experiment import _decode, _execute
    from tools.gold_input_history import GoldInputHistory
    from tools.gold_stream_history import GoldResearchHistory
    first, second = pieces(tmp_path, monkeypatch)
    assert 'observed_funding_records' not in first and 'observed_funding_records' not in second
    monkeypatch.setattr(tool, 'source_identity', lambda: first['release_identity'])
    root = tmp_path/'funding-declaration-only'
    tool.freeze(iter((first, second)), root, maximum_bytes=32_000_000, settlement_window_ms=1000)
    budget = dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1', maximum_environment_segments=4096,
        maximum_elapsed_ms=32*86_400_000)
    with NativeSource(root, replay_budget=budget) as originals:
        assert originals.document['input_schema'] == 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V4'
        assert originals.document['settlement_window_ms'] == 1000
        with GoldInputHistory(tmp_path/'prepared', maximum_bytes=64_000_000) as preparation:
            with GoldResearchHistory(tmp_path/'accounts', maximum_history_bytes=256_000_000) as history:
                report = _execute(_decode(originals.document), history=history,
                    source_context=(originals, preparation))
                assert all(t['simulation']['reconciliation']['residual'] == '0' for t in report['trials'])
                assert all(t['simulation']['ending_state']['cashflow_identity']['funding'] == '0'
                    for t in report['trials'])


def test_funding_window_change_in_later_part_rejects_before_sealing(tmp_path, monkeypatch):
    from decimal import Decimal
    from tests.tools.test_gold_public_segment_producer import native_session
    from tools import gold_chunked_source as tool, gold_native_prefix as bridge
    bars, identity, summary = native_session(tmp_path, monkeypatch, funding_after_ms=500_000)
    monkeypatch.setattr(tool, 'source_identity', lambda: identity)
    def changed_parts():
        for part in bridge.session_parts(tmp_path/'session', bars, tmp_path/'prefixes',
                segment_count=summary['segment_count'], release_identity=identity,
                quantity=Decimal('0.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('0.001'),
                settlement_window_ms=1000):
            if 'observed_funding_records' in part:
                part = dict(part, settlement_window_ms=500)
            yield part
    root = tmp_path/'changed-funding-policy'
    with pytest.raises(ValueError, match='FUNDING_POLICY_CHANGED'):
        tool.freeze(changed_parts(), root, maximum_bytes=32_000_000, settlement_window_ms=1000)
    assert not (root/'manifest.json').exists()
    assert json.loads((root/'failure.json').read_bytes())['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('damage', ['missing', 'legacy', 'budget'])
def test_declared_funding_input_cannot_drop_header_or_change_budget_version(tmp_path, monkeypatch, damage):
    from tools import gold_chunked_source as tool
    from tools.gold_chunked_source import NativeSource
    from quantos.contracts.gold_native_source import input_document
    first, second = pieces(tmp_path, monkeypatch)
    monkeypatch.setattr(tool, 'source_identity', lambda: first['release_identity'])
    root = tmp_path/'declared-policy'
    tool.freeze(iter((first, second)), root, maximum_bytes=32_000_000, settlement_window_ms=1000)
    with NativeSource(root) as originals:
        document = dict(originals.document)
    if damage == 'missing':
        document.pop('settlement_window_ms')
        with pytest.raises(ValueError, match='FUNDING_WINDOW_REQUIRED'):
            input_document(document)
    else:
        document['input_schema'] = 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V1' if damage == 'legacy' \
            else 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V4'
        with pytest.raises(ValueError):
            with NativeSource(root, document):
                pass


@pytest.mark.parametrize('reader', ['stream', 'native'])
@pytest.mark.parametrize('damage,code', [
    ('missing_first_window', 'FUNDING_WINDOW_REQUIRED'),
    ('changed_later_window', 'FUNDING_POLICY_CHANGED'),
    ('nonfunding_later_header', 'FUNDING_HEADER'),
])
def test_cold_readers_reject_invalid_funding_policy_even_after_rehashing(
        tmp_path, monkeypatch, reader, damage, code):
    import hashlib
    from decimal import Decimal
    from quantos.contracts import gold_chunked_source as shape
    from tests.tools.test_gold_public_segment_producer import native_session
    from tools import gold_chunked_source as tool
    root = tmp_path/'cold-funding-policy'
    if damage == 'changed_later_window':
        bars, identity, summary = native_session(tmp_path, monkeypatch, funding_after_ms=500_000)
        monkeypatch.setattr(tool, 'source_identity', lambda: identity)
        tool.from_session(tmp_path/'session', bars, root,
            segment_count=summary['segment_count'], release_identity=identity,
            quantity=Decimal('0.1'), initial_collateral=Decimal('1000'), fee_rate=Decimal('0.001'),
            settlement_window_ms=1000, maximum_bytes=32_000_000)
    else:
        first, second = pieces(tmp_path, monkeypatch)
        monkeypatch.setattr(tool, 'source_identity', lambda: first['release_identity'])
        tool.freeze(iter((first, second)), root, maximum_bytes=32_000_000, settlement_window_ms=1000)
    manifest = json.loads((root/'manifest.json').read_bytes())
    index = 0 if damage == 'missing_first_window' else 1
    if damage == 'changed_later_window':
        index = next(i for i, ref in enumerate(manifest['parts'])
            if i and 'observed_funding_records' in json.loads((root/'blobs'/ref['sha256']).read_bytes()))
    old = manifest['parts'][index]
    item = json.loads((root/'blobs'/old['sha256']).read_bytes())
    if damage == 'missing_first_window':
        item.pop('settlement_window_ms')
    else:
        item['settlement_window_ms'] = 500 if damage == 'changed_later_window' else 1000
    raw = tool._bytes(item)
    digest = hashlib.sha256(raw).hexdigest()
    (root/'blobs'/old['sha256']).unlink()
    (root/'blobs'/digest).write_bytes(raw)
    (root/'blobs'/digest).chmod(0o600)
    manifest['parts'][index] = dict(sha256=digest, bytes=len(raw))
    manifest['blob_bytes'] += len(raw)-old['bytes']
    manifest['source_id'] = shape.identity(manifest)
    (root/'manifest.json').write_bytes(tool._bytes(manifest))
    with pytest.raises(ValueError, match=code):
        if reader == 'native':
            with tool.NativeSource(root):
                pass
        else:
            with tool.open_source(root) as originals:
                list(originals.parts())


def retained(tmp_path,monkeypatch,source=None):
    from tools import gold_chunked_source as tool
    first,second=pieces(tmp_path,monkeypatch,source)
    monkeypatch.setattr(tool,'source_identity',lambda:first['release_identity'])
    root=tmp_path/'source-input'
    manifest=tool.freeze(iter((first,second)),root,maximum_bytes=32_000_000)
    return root,manifest,first,second


def test_chunked_originals_round_trip_to_the_same_original_input_and_account(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool,gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute,_encode
    root,manifest,first,second=retained(tmp_path,monkeypatch)
    original=bridge.assemble(first,[second])
    actual=tool.materialize(root)
    assert _encode(actual)==_encode(original)
    assert _execute(actual)==_execute(original)
    assert manifest['replay_ready'] is manifest['economic_eligible'] is manifest['submission_enabled'] is False
    assert manifest['part_count']==2
    assert all(p.stat().st_mode & 0o777==0o600 for p in (root/'blobs').iterdir())
    with pytest.raises(FileExistsError):
        tool.freeze(iter((first,second)),root,maximum_bytes=32_000_000)


def test_cold_reader_checks_every_original_and_rejects_a_replaced_inode(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool
    root,_,_,_=retained(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='(FILE|DIRECTORY)_CHANGED'):
        with tool.open_source(root) as reader:
            values=list(reader.parts())
            assert len(values)==2
            path=next((root/'blobs').iterdir())
            replacement=tmp_path/'replacement'
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(path)


def test_storage_keeps_more_than_64_parts_without_relaxing_native_admission(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool
    first,second=pieces(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'source_identity',lambda:first['release_identity'])
    # A synthetic storage load, not a claim of valid quote continuity.
    manifest=tool.freeze(iter([first]+[second]*64),tmp_path/'source-input',maximum_bytes=32_000_000)
    assert manifest['part_count']==65
    with tool.open_source(tmp_path/'source-input') as reader:
        assert sum(1 for _ in reader.parts())==65
    with pytest.raises(ValueError,match='NATIVE_EXECUTION_BOUND'):
        tool.materialize(tmp_path/'source-input')
    assert manifest['replay_ready'] is False


@pytest.mark.parametrize('damage',['missing','changed','extra','alias'])
def test_corrupted_or_unbound_content_cannot_be_consumed(tmp_path,monkeypatch,damage):
    from tools import gold_chunked_source as tool
    root,_,_,_=retained(tmp_path,monkeypatch)
    path=next((root/'blobs').iterdir())
    if damage=='missing':
        path.unlink()
    elif damage=='changed':
        path.write_bytes(path.read_bytes()+b' ')
    elif damage=='extra':
        p=root/'blobs/unbound'
        p.write_bytes(b'{}')
        p.chmod(0o600)
    else:
        old=path.read_bytes()
        path.unlink()
        p=tmp_path/'outside'
        p.write_bytes(old)
        p.chmod(0o600)
        path.symlink_to(p)
    with pytest.raises((ValueError,FileNotFoundError)):
        tool.materialize(root)


def test_interrupted_freeze_retains_chunks_and_has_no_complete_manifest(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool
    first,second=pieces(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'source_identity',lambda:first['release_identity'])
    def incoming():
        yield first
        yield second
        raise TimeoutError('source ended')
    with pytest.raises(TimeoutError):
        tool.freeze(incoming(),tmp_path/'source-input',maximum_bytes=32_000_000)
    failure=json.loads((tmp_path/'source-input/failure.json').read_bytes())
    assert failure['status']=='INCOMPLETE' and failure['part_count']==2
    assert list((tmp_path/'source-input/blobs').iterdir())
    assert not (tmp_path/'source-input/manifest.json').exists()


def test_consumers_cannot_silently_stop_before_all_parts(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool
    root,_,_,_=retained(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='NOT_ALL_PARTS_READ'):
        with tool.open_source(root) as reader:
            next(reader.parts())


def test_generator_cannot_change_the_first_source_identity_after_retention(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool
    first,second=pieces(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'source_identity',lambda:first['release_identity'])
    def incoming():
        yield first
        first['release_identity']['changed']=True
        yield second
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        tool.freeze(incoming(),tmp_path/'source-input',maximum_bytes=32_000_000)
    assert not (tmp_path/'source-input/manifest.json').exists()


def test_storage_byte_budget_fails_before_replacing_or_discarding_originals(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool
    from quantos.contracts import gold_chunked_source as shape
    first,second=pieces(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'source_identity',lambda:first['release_identity'])
    with pytest.raises(ValueError,match='TOTAL_BYTES'):
        tool.freeze(iter((first,second)),tmp_path/'source-input',maximum_bytes=shape.RESERVED_BYTES+1)
    assert (tmp_path/'source-input/failure.json').exists()
    assert not (tmp_path/'source-input/manifest.json').exists()


@pytest.mark.parametrize('portfolio', [False, True])
def test_original_collector_streams_parts_to_chunked_source_and_original_native_input(tmp_path,monkeypatch,portfolio):
    from decimal import Decimal
    from tests.tools.test_gold_public_segment_producer import native_session
    from tools import gold_chunked_source as tool,gold_native_prefix as bridge
    from tools.gold_bar_experiment import _encode,_execute
    bars,source,summary=native_session(tmp_path,monkeypatch,funding_after_ms=500_000)
    monkeypatch.setattr(tool,'source_identity',lambda:source)
    options=dict(segment_count=summary['segment_count'],release_identity=source,quantity=Decimal('.1'),
        initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'),settlement_window_ms=1000)
    portfolio_plan = dict(quantities={name:'0.1' for name in ('channel','trend','reversion')}, max_notional='100')
    tool.from_session(tmp_path/'session',bars,tmp_path/'source-input',maximum_bytes=32_000_000,
        **options, **(dict(portfolio_plan=portfolio_plan) if portfolio else {}))
    expected=bridge.from_session(tmp_path/'session',bars,tmp_path/'original-prefix',**options)
    if portfolio:
        expected['portfolio_plan'] = portfolio_plan
    actual=tool.materialize(tmp_path/'source-input')
    assert _encode(actual)==_encode(expected)
    result=_execute(actual)
    cash=result['trials'][0]['simulation']['ending_state']['cashflow_identity']
    assert cash['funding']=='-0.01' and cash['ending_equity']=='999.95' and cash['residual']=='0'
    if portfolio:
        assert len(result['portfolio']['simulation']['environment_segments']) == result['dataset_identity']['input_segment_count']


def test_writer_cannot_seal_a_blob_changed_by_the_source_iterator(tmp_path,monkeypatch):
    from tools import gold_chunked_source as tool
    first,second=pieces(tmp_path,monkeypatch)
    monkeypatch.setattr(tool,'source_identity',lambda:first['release_identity'])
    def incoming():
        yield first
        yield second
        path=next((tmp_path/'source-input/blobs').iterdir())
        path.write_bytes(path.read_bytes())
    with pytest.raises(ValueError,match='FILE_CHANGED'):
        tool.freeze(incoming(),tmp_path/'source-input',maximum_bytes=32_000_000)
    assert not (tmp_path/'source-input/manifest.json').exists()


def test_chunk_source_uses_original_request_queue_and_readonly_recovery(tmp_path,monkeypatch,technical_arguments):
    import time
    from tests.research.test_registered_research_job import queued
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tools import gold_chunked_source as tool,gold_bar_experiment,gold_bar_research
    from tools import registered_gold_collection as collected,registered_research_job as job
    from tools import registered_research_run as native
    root,_,first,second=retained(tmp_path,monkeypatch,technical_arguments['release_identity'])
    source=first['release_identity']
    for module in (gold_bar_experiment,gold_bar_research):
        monkeypatch.setattr(module,'source_identity',lambda:source)
    monkeypatch.setattr(time,'time_ns',lambda:
        (second['capture']['exchange_after']['received_at']+1000)*1_000_000)
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    tool.write_request(root,tmp_path/'data/cache/gold_bar_research/requests/1')
    original,calls=native._native_run,[]
    def observed(**kwargs):
        producer=kwargs['execute']
        def execute():
            calls.append(1)
            return producer()
        return original(**{**kwargs,'execute':execute})
    monkeypatch.setattr(native,'_native_run',observed)
    registry,queue=queued(tmp_path)
    uid=queue.submit('gold_bar_research',{'attempt':1},1,invocation_id='chunk-source')
    queue.join()
    assert registry.get(uid).status=='done',registry.get(uid).error
    project,database=job.workspace_paths()
    cut=ProjectResearchEvidenceReader(project,registry_database=database)()
    assert cut['attempt_count']==3 and {r['status'] for r in cut['attempts']}=={'PASSED'}
    report=project/'runs'/collected.invocation_id(1)/'archive/report.json'
    before=report.read_bytes(),database.read_bytes()
    uid=queue.submit('gold_bar_recover',{'attempt':1},2,invocation_id='chunk-source-recover')
    queue.join()
    assert registry.get(uid).status=='done',registry.get(uid).error
    assert calls==[1] and before==(report.read_bytes(),database.read_bytes())
