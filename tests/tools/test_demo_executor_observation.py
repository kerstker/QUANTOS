"""The UI receives original executor facts, never a copied research journal."""
import json
import pytest

from tools import demo_executor_observation as tool
from services.research.demo_user_stream import UserStreamJournal
from services.research import demo_executor_observation as model
from tests.research.test_demo_user_stream import NOW, account_event


def populated(tmp_path, monkeypatch):
    from services.research.demo_user_stream import normalize_message
    monkeypatch.setattr(tool, 'require_custody', lambda: tmp_path)
    monkeypatch.setattr(tool, 'now_ms', lambda: NOW+10)
    monkeypatch.setattr(tool.time, 'time_ns', lambda: (NOW+10)*1_000_000)
    with UserStreamJournal(tmp_path/'user-stream.sqlite3', account_ref_sha256='a'*64) as journal:
        journal.start_session(source_sha256='b'*64, at_ms=NOW)
        journal.record('CONNECTED', at_ms=NOW+1)
        event=normalize_message(json.dumps(account_event()))
        journal.record('MESSAGE', at_ms=NOW+2, message=event)
        journal.record('MESSAGE', at_ms=NOW+3, message=event)
        journal.record('GAP', at_ms=NOW+4, reason='STREAM_DISCONNECTED')
    return tmp_path


def test_export_retains_duplicates_and_gaps_without_copying_private_payloads(tmp_path, monkeypatch):
    root=populated(tmp_path,monkeypatch)
    before=(root/'user-stream.sqlite3').read_bytes()
    report=tool.run(source_commit='c'*40)
    assert (root/'user-stream.sqlite3').read_bytes()==before
    saved=model.load_latest(root/'observation-views')
    assert saved==report
    stream=saved['components'][0]
    assert stream['phase']=='DISCONNECTED'
    assert stream['counts']=={'records':5,'messages':2,'unique_messages':1,'gaps':1}
    assert all(row['status']=='NO_SNAPSHOT' for row in saved['components'][1:])
    assert all(saved[key] is False for key in ('source_verified','submission_enabled','cash_reconciled','economic_authority'))
    assert '100.00000001' not in json.dumps(saved) and 'raw_utf8' not in json.dumps(saved)


@pytest.mark.parametrize('failure',['different_account','future','bad_hash','symlink'])
def test_invalid_origin_stays_visible_without_replacing_valid_stream(tmp_path,monkeypatch,failure):
    from tests.tools.test_binance_demo_execution_origin import collect
    from services.research import demo_execution_origin
    root=populated(tmp_path,monkeypatch)
    raw,_=collect(tmp_path)
    monkeypatch.setattr(tool,'now_ms',lambda:max(NOW,raw['completed_at_ms'])+10)
    if failure!='different_account':
        raw['account_ref_sha256']='a'*64
        for row in raw['reads']:
            if row['account_ref_sha256'] is not None:row['account_ref_sha256']='a'*64
    directory=root/'execution-origins'/'BTCUSDC'
    directory.parent.mkdir(mode=0o700);directory.mkdir(mode=0o700)
    path=demo_execution_origin.save(directory,raw)
    if failure=='future':monkeypatch.setattr(tool,'now_ms',lambda:raw['completed_at_ms']-1)
    if failure=='bad_hash':path.write_text('{"private":"DO_NOT_EXPORT"}')
    if failure=='symlink':
        target=root/'original-origin.json';path.rename(target);path.symlink_to(target)
    result=tool.run(source_commit='c'*40)
    assert result['components'][0]['status']=='AVAILABLE'
    assert result['components'][1]['status']=='CORRUPT_OR_UNREADABLE'
    assert 'DO_NOT_EXPORT' not in json.dumps(result)


def test_missing_protected_state_does_not_initialize_or_read_credentials(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(tool,'require_custody',lambda:(_ for _ in ()).throw(ValueError('PRIVATE_PATH')))
    assert tool.main(['--source-commit','c'*40])==3
    assert list(tmp_path.iterdir())==[] and 'PRIVATE_PATH' not in capsys.readouterr().out


def test_stream_full_chain_corruption_is_not_hidden_by_recent_valid_records(tmp_path,monkeypatch):
    root=populated(tmp_path,monkeypatch)
    with UserStreamJournal(root/'user-stream.sqlite3') as journal:
        for name, in journal.db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='stream_records'").fetchall():
            journal.db.execute('DROP TRIGGER "'+name+'"')
        journal.db.execute("UPDATE stream_records SET record_hash=? WHERE sequence=1",('f'*64,))
        journal.db.commit()
    with pytest.raises(ValueError):tool.run(source_commit='c'*40)
    assert not (root/'observation-views').exists()


def test_publisher_copies_only_small_display_and_rejects_rollback_or_invented_authority(tmp_path,monkeypatch,capsys):
    from tools.demo_oms_status_publish import main
    root=populated(tmp_path,monkeypatch);tool.run(source_commit='c'*40)
    source=root/'observation-views';dest=tmp_path/'research'
    args=['--source-dir',str(source),'--output-dir',str(dest),'--kind','executor-observation']
    assert main(args)==0 and model.load_latest(dest)==model.load_latest(source)
    report=model.load_latest(dest);before=(dest/'latest.json').read_bytes()
    with pytest.raises(ValueError):model.save(source,dict(report,source_verified=True))
    model.save(dest,dict(report,observed_at_ms=NOW+11));newer=(dest/'latest.json').read_bytes()
    assert newer!=before and main(args)==3 and (dest/'latest.json').read_bytes()==newer
    assert 'PUBLISHED_UNVERIFIED_DISPLAY' in capsys.readouterr().out


def test_disappeared_origin_record_is_corrupt_and_publication_is_serialized(tmp_path,monkeypatch):
    from tools.binance_demo_user_stream import writer_lock
    root=populated(tmp_path,monkeypatch)
    directory=root/'execution-origins'/'BTCUSDC'
    directory.parent.mkdir(mode=0o700);directory.mkdir(mode=0o700)
    pointer=directory/'latest.json';pointer.write_text(json.dumps({'sha256':'b'*64}));pointer.chmod(0o600)
    assert tool.run(source_commit='c'*40)['components'][1]['status']=='CORRUPT_OR_UNREADABLE'
    with writer_lock(root/'observation.lock'):
        with pytest.raises(ValueError,match='STREAM_WRITER_ALREADY_RUNNING'):tool.run(source_commit='c'*40)


def test_read_only_inventory_can_display_a_journal_at_its_write_budget(tmp_path,monkeypatch):
    from tools import demo_executor_stream
    root=populated(tmp_path,monkeypatch)
    monkeypatch.setattr(demo_executor_stream,'MAX_RECORDS',5)
    result=tool.run(source_commit='c'*40)
    assert result['components'][0]['counts']['records']==5
    with UserStreamJournal(root/'user-stream.sqlite3',read_only=True) as journal:
        with pytest.raises(ValueError,match='RECORD_BUDGET'):demo_executor_stream._budget(journal)


def test_observation_continues_after_first_thousand_files_without_removing_history(tmp_path,monkeypatch):
    root=populated(tmp_path,monkeypatch)
    report=tool.run(source_commit='c'*40)
    directory=root/'observation-views'
    for offset in range(1,999):
        model.save(directory,dict(report,observed_at_ms=report['observed_at_ms']+offset))
    previous={p.name:p.read_bytes() for p in directory.iterdir() if p.name!='latest.json'}
    assert len(list(directory.iterdir()))==1000
    monkeypatch.setattr(tool,'now_ms',lambda:report['observed_at_ms']+1000)
    current=tool.run(source_commit='c'*40)
    assert model.load_latest(directory)==current
    assert all((directory/name).read_bytes()==body for name,body in previous.items())


def test_observation_capacity_failure_is_bounded_and_leaves_the_last_view(tmp_path,monkeypatch):
    root=populated(tmp_path,monkeypatch)
    tool.run(source_commit='c'*40)
    directory=root/'observation-views';before=(directory/'latest.json').read_bytes()
    monkeypatch.setattr(tool,'MAX_OUTPUT_FILES',2)
    with pytest.raises(ValueError,match='EXECUTOR_OBSERVATION_OUTPUT_BUDGET'):
        tool.run(source_commit='c'*40)
    assert (directory/'latest.json').read_bytes()==before
