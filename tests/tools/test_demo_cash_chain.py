from pathlib import Path
import json

from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_cash_chain_status import load_latest
from tools.demo_cash_chain import main
from tests.research.test_demo_cash_chain import fixture


def test_offline_cli_retains_full_result_and_publishes_only_unverified_display(tmp_path,capsys):
    batches,cash=fixture(tmp_path);paths=[]
    for i,(m,messages) in enumerate(batches):
        p=tmp_path/f'cli-{i}.db';paths.append(p)
        with DemoExecutionJournal(p,manifest=m) as j:j.append(messages)
    cashpath=tmp_path/'cash.json';cashpath.write_text(json.dumps(cash))
    before={p:p.read_bytes() for p in [*paths,cashpath]}
    args=[v for p in paths for v in ('--journal',str(p))]+['--cash-window',str(cashpath),
        '--output-dir',str(tmp_path/'out'),'--publish-dir',str(tmp_path/'display')]
    assert main(args)==0
    assert json.loads(capsys.readouterr().out)['status']=='MATCHED_REPORTED_INPUTS'
    view=load_latest(tmp_path/'display');assert view['quantity']=='0.6'
    pointer=json.loads((tmp_path/'out/latest.json').read_text())
    assert pointer['report_sha256']==view['report_sha256']
    assert (tmp_path/'out/reports'/(pointer['report_file_sha256']+'.json')).is_file()
    assert len(list((tmp_path/'out/reports').glob('*.json')))==1
    assert main(args)==0 and len(list((tmp_path/'out/reports').glob('*.json')))==1
    assert {p:p.read_bytes() for p in before}==before
    bad=args.copy();bad[bad.index('--output-dir')+1]=str(tmp_path)
    assert main(bad)==3 and {p:p.read_bytes() for p in before}==before
