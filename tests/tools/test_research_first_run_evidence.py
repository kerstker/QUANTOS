"""Synthetic-only regressions for the read-only RF2 saved-receipt reader.

Every fixture is generated from a minimal canonical replay plus the real
``TrialLedger``, so the tests never touch a saved real run, raw market data or
any network/service boundary.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal

import numpy as np
import pytest

from services.research.profitability_attribution import attribute_replay
from services.research.perpetual_alpha import replay as canonical_replay
from services.research.trial_ledger import TrialLedger, TrialRecord
from tools import rf2_bounded_strategy_experiment as driver
from tools import research_first_run_evidence as reader

PLAN_TEXT = 'synthetic RF2 plan body\n'
PLAN_SHA = hashlib.sha256(PLAN_TEXT.encode('utf-8')).hexdigest()


def _ms(year, month, day):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def _identity():
    start, end = _ms(2025, 1, 1)+300000, _ms(2025, 1, 3)+300000
    dataset = {'start_ms': start, 'end_ms': end,
               'start_iso': datetime.fromtimestamp(start // 1000, timezone.utc).isoformat(),
               'end_iso': datetime.fromtimestamp(end // 1000, timezone.utc).isoformat(),
               'aligned_minutes': 2881, 'provenance_sha256': 'd' * 64}
    return {'schema': 'RF2_BATCH_IDENTITY_V1',
            'plan': 'docs/engineering/plans/2026-09-20-rf2-bounded-strategy-experiment.md',
            'plan_sha256': PLAN_SHA,
            'source': {'commit': 'a' * 40, 'python_source_sha256': 'b' * 64,
                       'dependency_lock_sha256': 'c' * 64, 'python_version': '3.12.3',
                       'architecture': 'aarch64'},
            'dataset': dataset, 'risk_cost_profile': driver.RISK_COST_PROFILE}


def _replay(candidate, slip):
    data = _identity()['dataset']
    start, end = data['start_ms'], data['end_ms']
    times = np.arange(start, end+60000, 60000, dtype=np.int64)
    prices = np.linspace(100., 130., len(times))
    return canonical_replay(times=times, prices=prices, marks=prices.copy(), funding=[],
                            start=start, end=end,
                            daily_volume={int(at//86400000): Decimal('10000000') for at in times},
                            decision=lambda *_: 0. if candidate == 'cash' else .25,
                            slip_bps=slip, stop=True)


def _artifact(plan, identity, status=driver.CASE_STATUS_OK):
    if status == driver.CASE_STATUS_FAILED:
        return driver._failed_case(plan['candidate'], plan['slip_bps'], plan['trial_id'], identity,
                                   'SyntheticError: computation failed', 0.5)
    replay = _replay(plan['candidate'], plan['slip_bps'])
    attribution = attribute_replay(replay)
    metrics = {'net_pnl': attribution['net_pnl'], 'gross_reference_pnl': attribution['gross_reference_pnl'],
               'fees': attribution['fees'], 'funding': attribution['funding'],
               'embedded_slippage': attribution['embedded_slippage'],
               'turnover_notional': attribution['turnover_notional'],
               'turnover_initial_equity': attribution['turnover_initial_equity'],
               'diagnosis': attribution['diagnosis'], 'fills': len(replay['fills']),
               'orders': len(replay['orders']), 'rejected_orders': 0,
               'decision_count': len(replay['decisions']),
               'annual_pnl': driver._annual_pnl(replay['daily']),
               'max_drawdown': float(replay['minute_max_drawdown']),
               'average_exposure': float(replay['average_exposure']),
               'halted_at': replay['halted_at'], 'elapsed_seconds': 1.5}
    documented = [{'time': receipt['time'], 'actual_quantity': receipt['actual_quantity'],
                   'replay_target': repr(float(receipt['target']))} for receipt in replay['decisions']]
    return {'schema': driver.CASE_SCHEMA, 'status': driver.CASE_STATUS_OK,
            'evidence_class': driver.EVIDENCE_CLASS, 'history_class': driver.HISTORY_CLASS,
            'identity': identity, 'candidate': plan['candidate'],
            'signal': driver.SIGNAL_IDS[plan['candidate']], 'slip_bps': plan['slip_bps'],
            'trial_id': plan['trial_id'],
            'cost_profile': {'slippage_bps': plan['slip_bps'],
                             'taker_fee_bps': driver.RISK_COST_PROFILE['taker_fee_bps']},
            'risk_profile': driver.RISK_COST_PROFILE, 'metrics': metrics, 'attribution': attribution,
            'decisions': documented, 'replay': replay,
            'authority_admitted': False, 'authoritative': False, 'live_eligible': False,
            'paper_admitted': False,
            'computation': 'COMPLETED_NOT_ECONOMIC_SUCCESS_OR_AUTHORITY', 'limitations': driver.LIMITATIONS}


def _build(tmp_path, failed_keys=()):
    base = tmp_path / 'run'
    base.mkdir()
    identity = _identity()
    plans = driver.planned_trials(identity)
    ledger = TrialLedger(str(base / 'trials.sqlite'))
    try:
        for plan in plans:
            ledger.append(TrialRecord(**plan['record'], trial_id=plan['trial_id'], status='PLANNED',
                                      event_index=0, at=1))
        cases = {}
        for plan in plans:
            key = driver.case_key(plan['candidate'], plan['slip_bps'])
            failed = key in failed_keys
            artifact = _artifact(plan, identity,
                                 driver.CASE_STATUS_FAILED if failed else driver.CASE_STATUS_OK)
            ledger.append(TrialRecord(**plan['record'], trial_id=plan['trial_id'], status='RUNNING',
                                      event_index=1, at=2))
            ledger.append(TrialRecord(**plan['record'], trial_id=plan['trial_id'],
                                      status='FAILED' if failed else 'PASSED', event_index=2, at=3))
            (base / driver.case_filename(plan['candidate'], plan['slip_bps'])).write_text(json.dumps(artifact))
            cases[key] = driver._summary_entry(plan, artifact)
    finally:
        ledger.close()
    (base / 'identity.json').write_text(json.dumps(identity))
    (base / 'registration.json').write_text(json.dumps({'plan_text': PLAN_TEXT, 'plan_sha256': PLAN_SHA}))
    completed = sum(1 for entry in cases.values() if entry['status'] == driver.CASE_STATUS_OK)
    summary = {'schema': driver.SUMMARY_SCHEMA, 'status': driver.EVIDENCE_CLASS,
               'evidence_class': driver.EVIDENCE_CLASS, 'history_class': driver.HISTORY_CLASS,
               'authoritative': False, 'authority_admitted': False, 'live_eligible': False,
               'paper_admitted': False, 'identity': identity, 'planned_trials': len(plans),
               'planned_trial_ids': [plan['trial_id'] for plan in plans], 'cases': cases,
               'completed_cases': completed, 'failed_cases': len(plans) - completed,
               'trial_ledger_verified': True,
               'replay_engine': 'services.research.perpetual_alpha.replay',
               'attribution_engine': 'services.research.profitability_attribution.attribute_replay',
               'optimization': 'NONE_NO_RETRY_NO_AUTOTUNING_SINGLE_FROZEN_SPECIFICATION',
               'trial_success_meaning': 'COMPUTATION_ONLY_NOT_PROFITABILITY_OR_AUTHORITY',
               'screening': driver._screen(cases), 'limitations': driver.LIMITATIONS}
    (base / 'summary.json').write_text(json.dumps(summary))
    return base, summary


def _edit(base, name, mutate):
    path = base / name
    document = json.loads(path.read_text())
    mutate(document)
    path.write_text(json.dumps(document))


def test_receipts_with_consistently_removed_fees_are_not_the_four_bp_scenario(tmp_path):
    base, summary = _build(tmp_path)
    path = base / 'case-passive25-5.json'
    artifact = json.loads(path.read_text())
    replay = artifact['replay']
    r = replay['reconciliation']
    for fill in replay['fills']:
        fill['fee'] = '0'
    r['fee'] = '0'
    r['ending_equity'] = str(Decimal(r['initial_equity']) + Decimal(r['price_pnl']) + Decimal(r['funding']))
    r['attributed_change'] = str(Decimal(r['ending_equity']) - Decimal(r['initial_equity']))
    replay['daily'][-1]['equity'] = r['ending_equity']
    attribution = attribute_replay(replay)
    assert attribution['status'] == 'RECONCILED_TECHNICAL_ONLY'
    artifact['attribution'] = attribution
    for key in ('net_pnl', 'gross_reference_pnl', 'fees', 'funding', 'embedded_slippage',
                'turnover_notional', 'turnover_initial_equity', 'diagnosis'):
        artifact['metrics'][key] = attribution[key]
    artifact['metrics']['annual_pnl'] = driver._annual_pnl(replay['daily'])
    path.write_text(json.dumps(artifact))
    plan = next(p for p in driver.planned_trials(_identity())
                if p['candidate'] == 'passive25' and p['slip_bps'] == 5)
    summary['cases']['passive25/5'] = driver._summary_entry(plan, artifact)
    summary['screening'] = driver._screen(summary['cases'])
    (base / 'summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


@pytest.mark.parametrize('fault', ['numeric_risk_flag', 'inner_authority'])
def test_nested_contracts_cannot_weaken_risk_or_claim_authority(tmp_path, fault):
    base, _ = _build(tmp_path)
    if fault == 'numeric_risk_flag':
        _edit(base, 'case-cash-2.json', lambda d: d['risk_profile'].__setitem__('stop_enabled', 1))
    else:
        _edit(base, 'case-cash-2.json', lambda d: d['replay'].__setitem__('authoritative', True))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_reader_result_connects_to_assessment_without_authority(tmp_path):
    from tools.research_first_assessment import assess_paths, with_rf2_evidence
    from tools.research_first_assessment import DEFAULT_REGISTRY, DEFAULT_ASSESSMENT, DEFAULT_PROFILE
    base, _ = _build(tmp_path)
    report = with_rf2_evidence(assess_paths(DEFAULT_REGISTRY, DEFAULT_ASSESSMENT, DEFAULT_PROFILE),
                              reader.inspect_rf2(base))
    assert report['run_evidence']['authority'] is False
    assert report['overall']['verdict'] == 'RESEARCH_EVIDENCE_INCOMPLETE'


def test_optional_run_cli_is_read_only_and_preserves_failed_candidate(tmp_path):
    base, summary = _build(tmp_path)
    def snapshot():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in base.iterdir()}
    before = snapshot()
    script = Path(__file__).resolve().parents[2] / 'tools' / 'research_first_assessment.py'
    result = subprocess.run([sys.executable, str(script), '--rf2-run', str(base)],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report['run_evidence']['screening'] == summary['screening']
    assert report['overall']['verdict'] == 'RESEARCH_EVIDENCE_INCOMPLETE'
    assert report['counts']['research_pass'] == 0
    assert snapshot() == before


def test_inspect_verifies_a_consistent_synthetic_run(tmp_path):
    base, summary = _build(tmp_path)
    report = reader.inspect_rf2(base)
    assert report['schema'] == reader.EVIDENCE_SCHEMA
    assert report['status'] == reader.EVIDENCE_STATUS
    assert report['evidence_class'] == driver.EVIDENCE_CLASS
    assert report['history_class'] == driver.HISTORY_CLASS
    assert report['authority_admitted'] is False and report['authoritative'] is False
    assert report['live_eligible'] is False and report['paper_admitted'] is False
    assert report['economic_eligible'] is False
    assert report['cases'] == summary['cases'] and len(report['cases']) == 12
    assert report['completed_cases'] == 12 and report['failed_cases'] == 0
    assert report['screening'] == summary['screening']
    assert report['trial_ledger']['verified'] is True
    assert report['trial_ledger']['trial_count'] == 12 and report['trial_ledger']['event_count'] == 36
    assert report['trial_ledger']['registration_committed_first'] is True
    assert report['elapsed_case_sum_seconds'] == pytest.approx(18.0)
    files = {entry['file'] for entry in report['evidence_files']}
    assert len(files) == 16 and 'trials.sqlite' in files and 'summary.json' in files
    assert all(Path(entry['path']).is_absolute() for entry in report['evidence_files'])
    assert all(len(entry['sha256']) == 64 for entry in report['evidence_files'])
    assert report['limitations'] == reader.LIMITATIONS


def test_report_does_not_re_derive_current_repository_identity(tmp_path):
    base, _ = _build(tmp_path)
    report = reader.inspect_rf2(base)
    assert report['identity']['source'] == _identity()['source']


def test_failed_computation_case_is_retained(tmp_path):
    base, _ = _build(tmp_path, failed_keys={'log_slope90/5'})
    report = reader.inspect_rf2(base)
    assert len(report['cases']) == 12
    assert report['cases']['log_slope90/5']['status'] == driver.CASE_STATUS_FAILED
    assert report['cases']['log_slope90/5']['error']
    assert report['failed_cases'] == 1 and report['completed_cases'] == 11
    assert report['screening']['passed'] is False
    assert report['screening']['reason'] == 'FAILED_COMPUTATION'


def test_tampered_net_pnl_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    _edit(base, 'case-log_slope90-5.json', lambda d: d['metrics'].__setitem__('net_pnl', '999'))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_declared_screening_is_recomputed(tmp_path):
    base, _ = _build(tmp_path)
    _edit(base, 'summary.json', lambda d: d['screening'].__setitem__('passed', True))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_missing_case_artifact_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    (base / 'case-cash-2.json').unlink()
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


@pytest.mark.parametrize('flag', reader.AUTHORITY_FLAGS)
def test_non_boolean_summary_authority_flag_is_rejected(tmp_path, flag):
    base, _ = _build(tmp_path)
    _edit(base, 'summary.json', lambda d: d.__setitem__(flag, 0))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_zero_case_authority_flag_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    _edit(base, 'case-cash-2.json', lambda d: d.__setitem__('authoritative', 0))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_registration_plan_text_must_hash_to_the_plan_digest(tmp_path):
    base, _ = _build(tmp_path)
    _edit(base, 'registration.json', lambda d: d.__setitem__('plan_text', d['plan_text'] + 'tampered'))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_registration_plan_digest_must_match_identity(tmp_path):
    base, _ = _build(tmp_path)
    _edit(base, 'registration.json', lambda d: d.__setitem__('plan_sha256', '0' * 64))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_case_identity_must_match_the_identity_file(tmp_path):
    base, _ = _build(tmp_path)
    _edit(base, 'case-cash-2.json', lambda d: d['identity'].__setitem__('plan_sha256', '0' * 64))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_ledger_rejects_extra_trials(tmp_path):
    base, _ = _build(tmp_path)
    connection = sqlite3.connect(str(base / 'trials.sqlite'))
    try:
        connection.execute("INSERT INTO trial_events VALUES (?,0,?,?,?,?,'PLANNED',0,99,'',?)",
                           ('f' * 64, 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64, 'e' * 64))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_ledger_rejects_a_corrupted_commitment(tmp_path):
    base, _ = _build(tmp_path)
    connection = sqlite3.connect(str(base / 'trials.sqlite'))
    try:
        connection.execute("UPDATE trial_heads SET global_head_hash=? WHERE trial_id=''", ('0' * 64,))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_invalidated_marker_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    (base / 'invalidated.json').write_text('{}')
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_unexpected_failure_marker_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    (base / 'failed-run.json').write_text('{}')
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_relative_root_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        reader.inspect_rf2('relative/run')


def test_missing_root_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        reader.inspect_rf2(str(tmp_path / 'absent'))


def test_symlinked_root_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    link = tmp_path / 'link'
    link.symlink_to(base, target_is_directory=True)
    with pytest.raises(ValueError):
        reader.inspect_rf2(link)


def test_oversized_evidence_file_is_rejected(tmp_path, monkeypatch):
    base, _ = _build(tmp_path)
    monkeypatch.setattr(reader, 'MAX_FILE_BYTES', 32)
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_duplicate_json_key_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    (base / 'summary.json').write_text('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)


def test_nonfinite_json_value_is_rejected(tmp_path):
    base, _ = _build(tmp_path)
    document = json.loads((base / 'summary.json').read_text())
    document['cases']['cash/2']['max_drawdown'] = float('nan')
    (base / 'summary.json').write_text(json.dumps(document))
    with pytest.raises(ValueError):
        reader.inspect_rf2(base)
