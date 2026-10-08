"""Declared OOS materials must come from the original net account and clock."""
from copy import deepcopy
from decimal import Decimal, localcontext

import pytest

from tests.validation.test_gold_native_statistics import funding_case
from tests.validation.test_research_evaluation_plan import core


METHOD = 'ORIGINAL_RECEIPT_AVAILABLE_NET_ACCOUNT_AND_CASH_BASELINE_GRID_V1'


def declared():
    value = core()
    value.update(schema='QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V2',
        native_adapter='GOLD_BAR_FORWARD_V1', oos_material_method=METHOD)
    return value


def accounts():
    simulation, receipts = funding_case()
    cash = dict(initial_equity='100', ending_equity='99.4', attributed_change='-0.6',
        residual='0', fee='0', funding='-1')
    simulation.update(result_hash='1' * 64, full_research_risk_9_of_9=False,
        risk_history_source='LEDGER_EVENT_REPLAY', reconciliation=cash,
        ending_state=dict(cashflow_identity=deepcopy(cash), account_equity='99.4',
            positions={}, pending_initial_margin_execution_prices={}))
    baseline = deepcopy(simulation)
    for row in baseline['equity_history']:
        row['equity'] = '100'
    baseline['ledger_events'] = [dict(simulation['ledger_events'][0],
        cash_flow='0', pre_settlement_quantity='0')]
    cash = dict(initial_equity='100', ending_equity='100', attributed_change='0',
        residual='0', fee='0', funding='0')
    baseline.update(result_hash='2' * 64, reconciliation=cash,
        ending_state=dict(cashflow_identity=deepcopy(cash), account_equity='100',
            positions={}, pending_initial_margin_execution_prices={}))
    return simulation, baseline, receipts


def material(*, sample=None, as_of=100, streamed=False, receipts=None):
    from services.validation.gold_native_statistics import oos_account_material
    simulation, baseline, actual = accounts()
    return oos_account_material(simulation, baseline,
        sample=sample or dict(start_ms=10, end_ms=40, frequency_ms=10),
        settlements=actual if receipts is None else receipts,
        snapshot_as_of_ms=as_of, streamed=streamed)


def test_declared_new_oos_material_method_preserves_unknown_lock_and_train_validation():
    from quantos.contracts import research_evaluation_plan as contract
    value = declared()
    assert contract.validate_core(value) == value
    assert contract.core_status(value)['oos_status'] == 'UNKNOWN'
    assert contract.statistical_sample(value)['end_ms'] == value['splits']['validation']['end_ms']
    assert contract.statistical_sample(value)['end_ms'] < value['splits']['locked_oos']['start_ms']


@pytest.mark.parametrize('damage', ['old', 'method', 'missing', 'selection', 'no-oos'])
def test_oos_extraction_cannot_be_added_to_old_or_unbound_declarations(damage):
    from quantos.contracts import research_evaluation_plan as contract
    value = declared()
    if damage == 'old':
        value['schema'] = 'QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V1'
    elif damage == 'method':
        value['oos_material_method'] = 'SELF_DECLARED_PASS'
    elif damage == 'missing':
        del value['oos_material_method']
    elif damage == 'selection':
        value['selection'].update(method='TRAIN_ONLY', candidate_id=None)
    else:
        value['splits']['locked_oos'] = None
    with pytest.raises(ValueError):
        contract.validate_core(value)


@pytest.mark.parametrize('streamed', [False, True])
def test_oos_uses_original_available_net_equity_and_cash_baseline_without_reset(streamed):
    from services.validation.gold_native_statistics import oos_account_material
    simulation, baseline, receipts = accounts()
    before = deepcopy((simulation, baseline))
    value = oos_account_material(simulation, baseline,
        sample=dict(start_ms=10, end_ms=40, frequency_ms=10), settlements=receipts,
        snapshot_as_of_ms=100, streamed=streamed)
    assert value['material_status'] == 'AVAILABLE'
    candidate = value['candidate']
    assert candidate['window']['initial_equity'] == '100.2'
    assert candidate['window']['ending_equity'] == '99.4'
    assert candidate['window']['net_equity_change'] == '-0.8'
    assert candidate['window']['sample_count'] == 3
    assert Decimal(candidate['window']['net_return']) < 0
    assert value['benchmark']['window']['net_return'] == '0'
    assert value['benchmark_excess_net_return'] == candidate['window']['net_return']
    assert candidate['full_run_cash_identity']['initial_equity'] == '100'
    assert candidate['full_run_cash_identity']['net_equity_change'] == '-0.6'
    assert candidate['window']['annualized_sharpe'] is None
    assert candidate['window']['statistics_status'] == 'INSUFFICIENT_DURATION'
    assert value['statistical_decision'] == 'NOT_EVALUATED'
    assert value['independent_oos_verified'] is value['economic_authority'] is value['submission_enabled'] is False
    assert (simulation, baseline) == before


def test_delayed_receipt_cannot_make_a_flat_oos_grid_a_complete_cash_result():
    _, _, receipts = accounts()
    receipts[0]['available_at_ms'] = 90
    value = material(receipts=receipts)
    assert value['candidate']['window']['net_return'] == '0'
    assert value['material_status'] == 'INSUFFICIENT'
    assert value['late_receipts_at_window_end'] == 1
    assert 'OOS_REQUIRED_CASH_RECEIPTS_AFTER_WINDOW' in value['limitations']


def test_unobserved_oos_tail_has_no_invented_returns_or_benchmark_comparison():
    value = material(sample=dict(start_ms=10, end_ms=50, frequency_ms=10))
    assert value['material_status'] == 'INSUFFICIENT'
    assert value['candidate']['window'] is value['benchmark']['window'] is None
    assert value['benchmark_excess_net_return'] is None


@pytest.mark.parametrize('as_of', [True, -1, 39])
def test_invalid_or_future_oos_snapshot_is_rejected(as_of):
    with pytest.raises(ValueError):
        material(as_of=as_of)


@pytest.mark.parametrize('precision', [6, 28, 80])
def test_oos_decimal_window_and_hashes_do_not_depend_on_ambient_precision(precision):
    expected = material()
    with localcontext() as context:
        context.prec = precision
        assert material() == expected


def test_unreconciled_original_account_cannot_supply_oos_material():
    from services.validation.gold_native_statistics import oos_account_material
    simulation, baseline, receipts = accounts()
    simulation['reconciliation']['residual'] = '1'
    with pytest.raises(ValueError):
        oos_account_material(simulation, baseline, sample=dict(start_ms=10, end_ms=40, frequency_ms=10),
            settlements=receipts, snapshot_as_of_ms=100, streamed=False)


def test_incomplete_original_run_has_bound_missing_material_without_invented_cash():
    from services.validation.gold_native_statistics import project_gold_oos_material
    from quantos.contracts.registered_research_run import EVALUATED_SOURCE_REANALYSIS_SCHEMAS
    value = declared()
    run = dict(plan=dict(schema=next(iter(EVALUATED_SOURCE_REANALYSIS_SCHEMAS)),
        evaluation_core=value, invocation_id='b' * 64, input_sha256=value['input_sha256']),
        partial=True, originals={})
    actual = project_gold_oos_material(run, value['cohort'][0], project_cut_sha256='c' * 64,
        snapshot_as_of_ms=value['registered_at_ms'])
    assert actual['material_status'] == 'INSUFFICIENT'
    assert actual['candidate'] is actual['benchmark'] is actual['benchmark_excess_net_return'] is None
    assert actual['invocation_id'] == run['plan']['invocation_id']
    assert actual['oos_status'] == 'UNKNOWN' and actual['independent_oos_verified'] is False


def test_oos_subject_cannot_select_a_different_candidate_after_the_boundary():
    from services.validation.gold_native_statistics import project_gold_oos_material
    from quantos.contracts.registered_research_run import EVALUATED_SOURCE_REANALYSIS_SCHEMAS
    value = declared()
    run = dict(plan=dict(schema=next(iter(EVALUATED_SOURCE_REANALYSIS_SCHEMAS)), evaluation_core=value),
        partial=True, originals={})
    with pytest.raises(ValueError, match='OOS_PREDECLARED_CANDIDATE'):
        project_gold_oos_material(run, dict(value['cohort'][0], candidate_id='other'),
            project_cut_sha256='c' * 64, snapshot_as_of_ms=value['registered_at_ms'])
