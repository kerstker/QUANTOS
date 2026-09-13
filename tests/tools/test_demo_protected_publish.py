"""Fixed private snapshots cross into research only after permanent UID drop."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from tools import demo_protected_publish as tool
from services.research import demo_market_inputs
from tests.tools.test_binance_demo_market_inputs import capture


def configuration(tmp_path):
    return {'schema':'DEMO_DISPLAY_PUBLICATION_V1','source_commit':'c'*40,'executor_uid':2001,
        'issuer_uid':2002,'research_uid':2003,'research_gid':2003,'research_state_root':str(tmp_path/'research')}


@pytest.mark.parametrize('failure',[None,'corrupt','symlink','hardlink','writable','pointer'])
def test_fixed_snapshot_reads_exact_checked_bytes_without_following_links(tmp_path,monkeypatch,failure):
    source=tmp_path/'private';raw,_=capture();path=demo_market_inputs.save(source,raw)
    before=path.read_bytes();target=tmp_path/'outside';target.write_bytes(before)
    if failure=='corrupt':path.write_bytes(b'{"secret":"never echo"}')
    elif failure=='symlink':path.unlink();path.symlink_to(target)
    elif failure=='hardlink':path.unlink();os.link(target,path)
    elif failure=='writable':path.chmod(0o666)
    elif failure=='pointer':(source/'latest.json').write_text('{"sha256":"../../outside"}')
    monkeypatch.setattr(tool,'_require_protected',lambda *a,**kw:None)
    if failure:
        with pytest.raises(ValueError):tool.read_snapshot(source,root=source,uid=os.getuid(),model=demo_market_inputs)
    else:assert tool.read_snapshot(source,root=source,uid=os.getuid(),model=demo_market_inputs)==raw
    assert target.read_bytes()==before


def ready(tmp_path,monkeypatch):
    cfg=configuration(tmp_path);raw,_=capture();events=[]
    monkeypatch.setattr(tool,'verify_source',lambda _:None)
    monkeypatch.setattr(tool.os,'geteuid',lambda:0)
    monkeypatch.setattr(tool,'_read_root_owned_json',lambda _:tool.canonical(cfg))
    def read(*a,**kw):events.append('read_private');return raw
    monkeypatch.setattr(tool,'read_snapshot',read)
    def drop(uid,gid):assert (uid,gid)==(2003,2003);events.append('permanent_drop')
    monkeypatch.setattr(tool,'drop_identity',drop)
    return cfg,raw,events


def test_market_destination_is_opened_only_after_permanent_identity_drop(tmp_path,monkeypatch):
    cfg,raw,events=ready(tmp_path,monkeypatch);original=demo_market_inputs.save
    def save(path,value):
        assert events==['read_private','permanent_drop'];events.append('write_display')
        return original(path,value)
    monkeypatch.setattr(demo_market_inputs,'save',save)
    result=tool.run(source_commit='c'*40,kind='market',symbol='BTCUSDC')
    destination=Path(cfg['research_state_root'])/'data/cache/binance_demo_market_inputs/BTCUSDC'
    assert demo_market_inputs.load_latest(destination)==raw
    assert events==['read_private','permanent_drop','write_display'] and not result['source_verified']


@pytest.mark.parametrize('change',['source','research_uid','research_gid','state_path','extra'])
def test_invalid_publication_configuration_stops_before_private_read(tmp_path,monkeypatch,change):
    cfg,_,events=ready(tmp_path,monkeypatch)
    if change=='source':cfg['source_commit']='d'*40
    elif change=='research_uid':cfg['research_uid']=cfg['executor_uid']
    elif change=='research_gid':cfg['research_gid']=0
    elif change=='state_path':cfg['research_state_root']='relative/state'
    else:cfg['broker_key_path']='/not/allowed'
    with pytest.raises(ValueError):tool.run(source_commit='c'*40,kind='market',symbol='BTCUSDC')
    assert events==[]


@pytest.mark.parametrize('failure',['quote','drop','backwards'])
def test_failed_publication_preserves_previous_view(tmp_path,monkeypatch,failure,capsys):
    cfg,raw,events=ready(tmp_path,monkeypatch)
    destination=Path(cfg['research_state_root'])/'data/cache/binance_demo_market_inputs/BTCUSDC'
    previous=deepcopy(raw)
    if failure=='backwards':
        previous.update(status='FAILED',reads=[],completed_at_ms=raw['completed_at_ms']+100,
            failure={'kind':'worker','reason':'REQUEST_FAILED'})
    demo_market_inputs.save(destination,previous);before=(destination/'latest.json').read_bytes()
    if failure=='quote':raw['symbol']='BTCUSDT'
    if failure=='drop':monkeypatch.setattr(tool,'drop_identity',lambda *a:(_ for _ in ()).throw(ValueError('private diagnostic')))
    assert tool.main(['--source-commit','c'*40,'--kind','market','--symbol','BTCUSDC'])==3
    assert (destination/'latest.json').read_bytes()==before
    assert 'private diagnostic' not in capsys.readouterr().out


def test_permanent_drop_clears_groups_and_all_saved_identifiers(monkeypatch):
    calls=[]
    monkeypatch.setattr(tool.os,'setgroups',lambda x:calls.append(('groups',x)))
    monkeypatch.setattr(tool.os,'setresgid',lambda *x:calls.append(('gid',x)))
    monkeypatch.setattr(tool.os,'setresuid',lambda *x:calls.append(('uid',x)))
    monkeypatch.setattr(tool.os,'getresgid',lambda:(2003,2003,2003))
    monkeypatch.setattr(tool.os,'getresuid',lambda:(2003,2003,2003))
    monkeypatch.setattr(tool.os,'getgroups',lambda:[])
    tool.drop_identity(2003,2003)
    assert calls==[('groups',[]),('gid',(2003,)*3),('uid',(2003,)*3)]
    monkeypatch.setattr(tool.os,'getresuid',lambda:(2003,2003,0))
    with pytest.raises(ValueError):tool.drop_identity(2003,2003)


@pytest.mark.parametrize('kind,symbol',[('market',None),('issuer','BTCUSDC'),('credentials',None)])
def test_no_arbitrary_private_paths_or_ambiguous_quote_can_be_selected(kind,symbol):
    with pytest.raises(ValueError):tool.selection(kind,symbol)


def test_backup_view_reads_root_summary_and_drops_identity_before_research_write(tmp_path,monkeypatch):
    from services.research import demo_backup_status
    from tests.research.test_demo_backup_status import report
    cfg,_,events=ready(tmp_path,monkeypatch);raw=report()
    def read(directory,*,root,uid,model):
        assert root==Path('/var/lib/quantos-demo-checkpoints') and uid==0 and model is demo_backup_status
        events.append('read_private');return raw
    monkeypatch.setattr(tool,'read_snapshot',read)
    original=demo_backup_status.save
    def save(path,value):
        assert events==['read_private','permanent_drop'];events.append('write_display')
        return original(path,value)
    monkeypatch.setattr(demo_backup_status,'save',save)
    result=tool.run(source_commit='c'*40,kind='backup')
    assert result['status']=='PUBLISHED_UNVERIFIED_DISPLAY' and result['phase']=='PARTIAL_RECOVERY_POINTS'
    assert demo_backup_status.load_latest(Path(cfg['research_state_root'])/'data/cache/binance_demo_backup_status')==raw
