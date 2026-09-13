import json
import pytest
from services.research.demo_oms_status import build_status,save,load_latest
from tests.research.test_demo_oms_status import inputs,result,NOW


def test_publish_copies_validated_display_only_and_preserves_source(tmp_path,capsys):
    from tools.demo_oms_status_publish import main
    prepared,plan=inputs();source=tmp_path/'private';dest=tmp_path/'display'
    raw=build_status(prepared,plan,observed_at_ms=NOW,result=result(plan));save(source,raw)
    before={p.name:p.read_bytes() for p in source.iterdir()}
    args=['--source-dir',str(source),'--output-dir',str(dest)]
    assert main(args)==0 and load_latest(dest)==raw and main(args)==0
    assert before=={p.name:p.read_bytes() for p in source.iterdir()}
    out=json.loads(capsys.readouterr().out.splitlines()[-1])
    assert out['source_verified'] is False and out['submission_enabled'] is False


def test_cancellation_publish_uses_its_own_schema_and_rejects_oms_mixup(tmp_path,monkeypatch):
    from tools.demo_oms_status_publish import main
    from services.research import demo_cancel_status
    from live.execution.demo_cancel_store import DemoCancelStore
    from tests.live.test_demo_cancel_store import configured,NOW
    _,commands,context,envelope=configured(monkeypatch)
    with DemoCancelStore(tmp_path/'cancel.db',create=True) as store:
        store.reserve(envelope,context=context,commands=commands,now_ms=NOW)
        raw=demo_cancel_status.build_status(store.snapshot(),observed_at_ms=NOW)
    source=tmp_path/'private';dest=tmp_path/'public';demo_cancel_status.save(source,raw)
    args=['--source-dir',str(source),'--output-dir',str(dest)]
    assert main(args)==3 and not dest.exists()
    assert main([*args,'--kind','cancellation'])==0 and demo_cancel_status.load_latest(dest)==raw


def test_market_publishing_preserves_exact_bytes_quote_and_failed_attempts(tmp_path,capsys):
    from tools.demo_oms_status_publish import main
    from services.research import demo_market_inputs as model
    from tests.tools.test_binance_demo_market_inputs import capture
    source=tmp_path/'private';output=tmp_path/'display'/'BTCUSDC'
    raw,_=capture();path=model.save(source,raw);original=path.read_bytes()
    args=['--kind','market','--symbol','BTCUSDC','--source-dir',str(source),'--output-dir',str(output)]
    assert main(args)==0 and model.load_latest(output)==raw
    assert path.read_bytes()==original and (output/path.name).read_bytes()==original
    failed,_=capture(lambda d:d['klines'].pop());model.save(source,failed)
    assert main(args)==0 and model.load_latest(output)==failed
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['phase']=='FAILED'


@pytest.mark.parametrize('bad',['wrong_symbol','older_completion','symbol_without_market'])
def test_market_publish_cannot_replace_the_other_quote_or_a_newer_view(tmp_path,capsys,bad):
    from tools.demo_oms_status_publish import main
    from services.research import demo_market_inputs as model
    from tests.tools.test_binance_demo_market_inputs import capture
    source=tmp_path/'private';output=tmp_path/'display'/'BTCUSDC';raw,_=capture()
    model.save(output,raw)
    if bad=='older_completion':
        # A failure is allowed a longer actual elapsed time; it still cannot
        # overwrite a later completed observation with an earlier completion.
        raw=dict(raw,status='FAILED',completed_at_ms=raw['completed_at_ms']-1,reads=[],failure={'kind':'worker','reason':'REQUEST_FAILED'})
    model.save(source,raw);before=(output/'latest.json').read_bytes()
    args=['--kind','market','--symbol','BTCUSDT' if bad=='wrong_symbol' else 'BTCUSDC',
        '--source-dir',str(source),'--output-dir',str(output)]
    if bad=='symbol_without_market':args[1]='oms'
    assert main(args)==3 and (output/'latest.json').read_bytes()==before


@pytest.mark.parametrize('failure',['corrupt','backwards','same_directory','nested_source','nested_output'])
def test_publish_rejects_invalid_or_overlapping_input_without_replacing_good_view(tmp_path,capsys,failure):
    from tools.demo_oms_status_publish import main
    prepared,plan=inputs();source=tmp_path/'private';dest=tmp_path/'display'
    raw=build_status(prepared,plan,observed_at_ms=NOW,result=result(plan));path=save(source,raw)
    save(dest,{**raw,'observed_at_ms':NOW+1} if failure=='backwards' else raw)
    before=(dest/'latest.json').read_bytes()
    source_arg=source;dest_arg=dest
    if failure=='corrupt':path.write_text('{"secret":"NEVER_PRINT"}')
    if failure=='same_directory':dest_arg=source
    if failure=='nested_source':dest_arg=tmp_path
    if failure=='nested_output':dest_arg=source/'public'
    assert main(['--source-dir',str(source_arg),'--output-dir',str(dest_arg)])==3
    assert (dest/'latest.json').read_bytes()==before
    assert 'NEVER_PRINT' not in capsys.readouterr().out
