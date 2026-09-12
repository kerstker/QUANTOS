"""Operator import reads existing journals; it has no order/credential action."""
import json
from pathlib import Path
import pytest

from tools.demo_reserved_receipts import main
from tests.live.test_demo_receipt_binding import fixture, close, NOW, paired_cash_capture
from services.research.demo_user_stream import normalize_message


@pytest.mark.parametrize('with_cash', [False,True])
def test_cli_import_and_restart_preserve_input_files_and_never_call_transport(tmp_path,monkeypatch,capsys,with_cash):
    from live.exchange import binance_demo_exact as transport
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        prepared=tmp_path/'prepared.json';prepared.write_text(json.dumps(batch))
        import_args=['--prepared',str(prepared),'--dispatch',str(args['dispatch'].path),'--stream',str(stream.path),
            '--start-ms',str(NOW+1),'--end-ms',str(NOW+70000),'--source-commit','a'*40,'--output-dir',str(tmp_path/'reports')]
        if with_cash:
            cash=tmp_path/'cash.json';cash.write_text(json.dumps(paired_cash_capture(batch['context']['account_ref_sha256'])))
            import_args += ['--cash-window',str(cash)]
        for name in ('_http_once','_http_get_once','_credentials'):
            monkeypatch.setattr(transport,name,lambda *a,**k:(_ for _ in ()).throw(AssertionError('transport forbidden')))
        before=args['dispatch'].evidence_snapshot(); raw=Path(batch['proposal_path']).read_bytes()
        assert main(import_args)==0
        first=json.loads((tmp_path/'reports/latest.json').read_text())
        assert first['replay']['reconciliation']['fee']=='0.054'
        if with_cash:
            assert first['canonical_cash']['status']=='MATCHED_REPORTED_INPUTS'
            assert len(list((tmp_path/'reports/cash-inputs').glob('*.json')))==1
        assert main(import_args)==0 and json.loads((tmp_path/'reports/latest.json').read_text())==first
        assert args['dispatch'].evidence_snapshot()==before and Path(batch['proposal_path']).read_bytes()==raw
        assert 'isolated-fixture-secret' not in capsys.readouterr().out
    finally:close(args,stream)


def test_cli_bad_prepared_input_does_not_disclose_it_or_replace_a_saved_result(tmp_path,capsys):
    path=tmp_path/'bad.json';path.write_text('{"secret":"DO_NOT_PRINT", "secret":"duplicate"}')
    out=tmp_path/'reports';out.mkdir();(out/'latest.json').write_text('previous result')
    assert main(['--prepared',str(path),'--dispatch',str(tmp_path/'absent.db'),'--stream',str(tmp_path/'none.db'),
                 '--start-ms','100','--end-ms','200','--source-commit','a'*40,'--output-dir',str(out)])==3
    assert 'DO_NOT_PRINT' not in capsys.readouterr().out
    assert (out/'latest.json').read_text()=='previous result'
    assert not (tmp_path/'absent.db').exists()
