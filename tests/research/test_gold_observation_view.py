"""A saved fill export never proves a connected account or current position."""
import json

import pytest

from services.console.api.gold_observation import build_snapshot
from tests.research.test_gold_trade_observation import document


def save(root, value):
    (root / 'external-fills.json').write_text(json.dumps(value))


def test_missing_source_does_not_invent_zero_trades(tmp_path):
    result = build_snapshot(tmp_path, now_ms=4000)
    assert result['snapshot_status'] == 'NO_SNAPSHOT'
    assert result['fills'] is None
    assert result['account_connected'] is False
    assert result['position'] is None and result['net_pnl'] is None
    assert result['submission_enabled'] is False


def test_export_projects_only_redacted_historical_facts(tmp_path):
    save(tmp_path, document())
    result = build_snapshot(tmp_path, now_ms=4000)
    assert result['snapshot_status'] == 'REAL'
    assert result['fills']['unique_fills'] == 1
    assert result['fills']['end_ms'] == 2000
    assert result['coverage_age_ms'] == 2000
    assert result['account_connected'] is False
    assert result['history_complete'] is False
    assert result['source_authenticated'] is False
    assert 'account_ref' not in result['fills']
    assert 'trade_id' not in json.dumps(result)
    assert result['market_status'] == result['signal_status'] == 'NOT_CONNECTED'


def test_recent_reexport_does_not_refresh_old_coverage(tmp_path):
    d = document()
    d['exported_at_ms'] = 999999
    save(tmp_path, d)
    result = build_snapshot(tmp_path, now_ms=1000000)
    assert result['snapshot_status'] == 'STALE'
    assert result['coverage_age_ms'] == 998000


@pytest.mark.parametrize('case', ['future', 'schema', 'duplicate', 'symlink'])
def test_untrusted_inputs_fail_without_private_details(tmp_path, case):
    d = document()
    if case == 'future':
        d['exported_at_ms'] = 5000
    if case == 'schema':
        d['secret'] = 'must-never-appear'
    save(tmp_path, d)
    if case == 'duplicate':
        (tmp_path / 'external-fills.json').write_text('{"secret":1,"secret":2}')
    if case == 'symlink':
        (tmp_path / 'external-fills.json').rename(tmp_path / 'private.json')
        (tmp_path / 'external-fills.json').symlink_to(tmp_path / 'private.json')
    result = build_snapshot(tmp_path, now_ms=4000)
    assert result['snapshot_status'] == 'DATA_BLOCKED'
    assert result['fills'] is None
    assert 'secret' not in json.dumps(result)


def test_empty_export_is_not_a_complete_account(tmp_path):
    d = document()
    d['fills'] = []
    save(tmp_path, d)
    result = build_snapshot(tmp_path, now_ms=4000)
    assert result['fills']['status'] == 'NO_FILL_FACTS'
    assert result['fills']['unique_fills'] == 0
    assert result['net_pnl'] is None


def test_public_capture_displays_without_implying_an_account_connection(tmp_path):
    from tests.data.test_gold_capture_snapshot import captured
    (tmp_path/'public-capture.json').write_text(json.dumps(captured()))
    result=build_snapshot(tmp_path,now_ms=1200)
    assert result['market_status']=='OBSERVED_RECENTLY'
    assert result['market']['last_trade_price']=='4000'
    assert result['fills'] is None and result['account_connected'] is False
    assert result['snapshot_status']=='NO_SNAPSHOT'  # Account export still absent.


def test_bad_public_capture_does_not_hide_valid_private_fill_facts(tmp_path):
    save(tmp_path,document())
    (tmp_path/'public-capture.json').write_text('{}')
    result=build_snapshot(tmp_path,now_ms=4000)
    assert result['market_status']=='DATA_BLOCKED' and result['market'] is None
    assert result['fills']['unique_fills']==1


def cash_save(root, change=None):
    from tests.research.test_gold_cash_observation import document as cash_document
    d=cash_document()
    if change:d.update(change)
    (root/'external-cash.json').write_text(json.dumps(d))
    return d


def test_cash_without_fills_remains_visible_but_unreconciled(tmp_path):
    cash_save(tmp_path)
    r=build_snapshot(tmp_path,now_ms=4000)
    assert r['cash_status']=='OBSERVED'
    assert r['cash']['unique_events']==3
    assert r['cash_reconciliation_status']=='WAITING_FOR_FILLS'
    assert r['snapshot_status']=='NO_SNAPSHOT'
    assert r['net_pnl'] is None
    assert 'account_ref' not in json.dumps(r) and 'record_id' not in json.dumps(r)


def test_cash_missing_category_is_not_complete_reconciliation(tmp_path):
    save(tmp_path,document());cash_save(tmp_path)
    r=build_snapshot(tmp_path,now_ms=4000)
    assert r['cash_reconciliation_status']=='INCOMPLETE_CATEGORIES'
    assert r['cash']['funding_by_asset']=={'USDT':'-0.1'}


@pytest.mark.parametrize('case', ['bad','future','symlink','scope','mismatch','stale'])
def test_cash_issues_preserve_good_fills(tmp_path,case):
    save(tmp_path,document());d=cash_save(tmp_path)
    path=tmp_path/'external-cash.json'
    if case=='bad':path.write_text('{}')
    if case=='future':d['exported_at_ms']=5000;path.write_text(json.dumps(d))
    if case=='symlink':path.rename(tmp_path/'other.json');path.symlink_to(tmp_path/'other.json')
    if case=='scope':d['account_ref']='b'*64;path.write_text(json.dumps(d))
    if case=='mismatch':d['events'][0]['amount']='-1';path.write_text(json.dumps(d))
    r=build_snapshot(tmp_path,now_ms=200000 if case=='stale' else 4000)
    assert r['fills']['unique_fills']==1
    if case in ('bad','future','symlink'):
        assert r['cash_status']=='DATA_BLOCKED' and r['cash'] is None
    if case=='scope':assert r['cash_reconciliation_status']=='SCOPE_MISMATCH'
    if case=='mismatch':assert r['cash_reconciliation_status']=='TOTALS_DIFFER'
    if case=='stale':assert r['cash_status']=='STALE'


def test_matching_declared_totals_still_do_not_assert_account_reconciliation(tmp_path):
    save(tmp_path,document());d=cash_save(tmp_path)
    d['events'].append(dict(record_id='4',time_ms=1800,kind='REALIZED_PNL',asset='USDT',amount='0',symbol='XAUUSDT'))
    (tmp_path/'external-cash.json').write_text(json.dumps(d))
    r=build_snapshot(tmp_path,now_ms=4000)
    assert r['cash_reconciliation_status']=='DECLARED_TOTALS_MATCH'
    assert r['history_complete'] is False and r['net_pnl'] is None


def signal_save(root, change=None):
    from tests.tools.test_gold_signal_replay import archive
    d=archive()
    if change:d.update(change)
    (root/'signal-input.json').write_text(json.dumps(d))
    return d


def test_historical_signal_is_independent_of_account_snapshot(tmp_path):
    signal_save(tmp_path)
    r=build_snapshot(tmp_path,now_ms=4000)
    assert r['signal_status']=='HISTORICAL_REPLAY'
    assert r['signal']['decision_count']==2 and r['signal']['entry_signal_count']==1
    assert r['signal']['last_decision_ms']==3010
    assert r['signal']['preregistered'] is False
    assert r['signal']['authoritative'] is False
    assert r['snapshot_status']=='NO_SNAPSHOT' and r['submission_enabled'] is False


def test_incomplete_replay_keeps_observed_prefix(tmp_path):
    d=signal_save(tmp_path)
    d['packets']=d['packets'][:4];signal_save(tmp_path,d)
    r=build_snapshot(tmp_path,now_ms=4000)
    assert r['signal_status']=='INPUT_INCOMPLETE'
    assert r['signal']['decision_count']==1
    assert r['signal']['reason']=='MISSING_WINDOW_COVERAGE'


@pytest.mark.parametrize('case',['future','large','invalid_decimal','symlink'])
def test_signal_problem_cannot_hide_account_facts(tmp_path,case):
    save(tmp_path,document());d=signal_save(tmp_path)
    if case=='future':d['decision_times_ms']=[2010,5010]
    if case=='large':d['decision_times_ms']=list(range(1000,1061))
    if case=='invalid_decimal':d['rule']['sell_threshold']='invalid'
    signal_save(tmp_path,d)
    if case=='symlink':
        p=tmp_path/'signal-input.json';p.rename(tmp_path/'other-signal.json');p.symlink_to(tmp_path/'other-signal.json')
    r=build_snapshot(tmp_path,now_ms=4000)
    assert r['signal_status']=='DATA_BLOCKED' and r['signal'] is None
    assert r['fills']['unique_fills']==1


def linked_save(root):
    import hashlib
    from tests.research.test_gold_trade_links import inputs
    from services.research.gold_trade_links import report_digest
    f,s,a=inputs()
    save(root,f);signal_save(root)
    s['input_sha256']=hashlib.sha256((root/'signal-input.json').read_bytes()).hexdigest()
    a['replay_sha256']=report_digest(s)
    (root/'trade-links.json').write_text(json.dumps(a))
    return a


def test_links_recomputed_from_exact_signal_input_and_fills_are_redacted(tmp_path):
    linked_save(tmp_path)
    r=build_snapshot(tmp_path,now_ms=8000)
    assert r['links_status']=='DECLARED_ASSOCIATIONS_ONLY'
    assert r['links']['declared_entry_fills']==1
    assert r['links']['fills_by_status']=={'DECLARED_LINK':1}
    assert 'trade_id' not in json.dumps(r) and 'account_ref' not in json.dumps(r)
    assert r['net_pnl'] is None


@pytest.mark.parametrize('case',['binding','signal_bytes','future_annotation','missing_signal','bad_links'])
def test_invalid_or_missing_link_evidence_preserves_other_panels(tmp_path,case):
    a=linked_save(tmp_path)
    if case=='binding':a['fills_facts_sha256']='0'*64
    if case=='future_annotation':a['links'][0]['annotated_at_ms']=9000
    (tmp_path/'trade-links.json').write_text(json.dumps(a))
    if case=='signal_bytes':
        p=tmp_path/'signal-input.json';p.write_text(p.read_text()+'\n')
    if case=='missing_signal':(tmp_path/'signal-input.json').unlink()
    if case=='bad_links':(tmp_path/'trade-links.json').write_text('{}')
    r=build_snapshot(tmp_path,now_ms=8000)
    assert r['links_status']==('WAITING_FOR_EVIDENCE' if case=='missing_signal' else 'DATA_BLOCKED')
    assert r['links'] is None and r['fills']['unique_fills']==1


def test_monitor_view_is_independent_and_stale_connection_is_not_live(tmp_path):
    from services.research.gold_account_journal import AccountJournal
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64);j.lifecycle('CONNECTED',at_ms=1001)
        value={'schema':'GOLD_MONITOR_VIEW_V1','as_of_ms':1001,'summary':j.summary(as_of_ms=1001),'recovery':None}
    (tmp_path/'account-monitor.json').write_text(json.dumps(value))
    r=build_snapshot(tmp_path,now_ms=1002)
    assert r['monitor_status']=='CONNECTED' and r['monitor']['unique_trades']==0
    assert r['account_connected'] is False  # Saved observation is not independent live attestation.
    r=build_snapshot(tmp_path,now_ms=200000)
    assert r['monitor_status']=='STALE'
    value['summary']['history_complete']=True
    (tmp_path/'account-monitor.json').write_text(json.dumps(value))
    assert build_snapshot(tmp_path,now_ms=200000)['monitor_status']=='DATA_BLOCKED'
