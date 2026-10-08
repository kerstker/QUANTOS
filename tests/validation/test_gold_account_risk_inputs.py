"""Gold risk consumes one real cash account, on its receipt-available clock."""

import copy
from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import SimulationCostModel
from quantos.contracts.decimal_arithmetic import ledger_decimal_context
from tests.research.test_gold_atomic_phase_loss import recovered_settlement
from tests.research.test_gold_loss_control import run_control
from tests.research.test_gold_v2_strategy import T, frame
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def inputs(simulation, *, end=40, frequency=10, settlements=None, **kwargs):
    from services.validation.gold_account_risk_inputs import build_account_risk_inputs
    return build_account_risk_inputs(simulation,
        sample=dict(start_ms=T, end_ms=T + end, frequency_ms=frequency),
        settlements=[] if settlements is None else settlements, account_currency='USDT', **kwargs)


def case():
    _, simulation = run_control(tuple(frame(i) for i in (0, 10, 20, 30, 40)),
                               costs=SimulationCostModel(fee_rate=D('.5')))
    return simulation


@ledger_decimal_context()
def test_actual_fees_and_initial_cash_form_aligned_risk_series_without_mutation():
    simulation = case()
    before = copy.deepcopy(simulation)
    value = inputs(simulation)
    assert value.initial_equity == value.opening_equity == D('100')
    assert value.equity_curve == (D('95'), D('90'), D('90'), D('90'))
    assert value.returns == (D('-.05'), D('90') / D('95') - 1, D('0'), D('0'))
    assert value.timestamps == (T + 10, T + 20, T + 30, T + 40)
    assert value.currency == 'USDT'
    assert value.periods_per_year == D(365 * 86_400_000) / 10
    projection = value.projection
    assert projection['source_groups']['portfolio_returns']['status'] == 'AVAILABLE'
    assert projection['source_groups']['equity_curve']['count'] == len(value.returns)
    assert projection['causal_drawdown']['max_drawdown'] == '0.1'
    assert projection['full_run_covered'] is True
    assert projection['full_research_risk_ready'] is False
    assert set(projection['missing_groups']) == {
        'weights', 'alpha_returns', 'regime_labels', 'alpha_metadata', 'capacity_and_aum', 'risk_config'}
    assert projection['economic_authority'] is projection['submission_enabled'] is False
    assert simulation == before


def test_event_density_never_changes_return_frequency_or_fabricates_alpha_legs():
    original = case()
    dense = copy.deepcopy(original)
    dense['equity_history'].insert(2, dict(dense['equity_history'][1]))
    for index, row in enumerate(dense['equity_history']):
        row['sequence'] = index
    a, b = inputs(original), inputs(dense)
    assert (a.returns, a.equity_curve, a.timestamps, a.periods_per_year) == (
        b.returns, b.equity_curve, b.timestamps, b.periods_per_year)
    assert a.projection['source_groups']['portfolio_returns'] == b.projection['source_groups']['portfolio_returns']
    assert a.projection['available_history_sha256'] != b.projection['available_history_sha256']
    assert a.projection['source_groups']['alpha_returns']['status'] == 'MISSING'
    assert 'alpha_returns' not in a.__dict__


def test_funding_drawdown_is_causal_and_survives_same_frame_price_recovery():
    _, simulation = run_control((frame(0), frame(10), recovered_settlement(),
                                 frame(1010, price='115'), frame(1020, price='115')),
                                costs=SimulationCostModel(fee_rate=D('.45')))
    settlements = [dict(settlement_at_ms=T + 1000, requested_at_ms=T + 1000,
        available_at_ms=T + 1005, instrument_id='XAUUSDT', funding_rate='0.1', mark_price='100')]
    early = inputs(simulation, end=1000, frequency=1000, settlements=settlements)
    assert early.equity_curve == (D('95.5'),)
    assert early.projection['causal_drawdown']['max_drawdown'] == '0.045'
    assert early.projection['full_run_covered'] is False
    known = inputs(simulation, end=1005, frequency=1005, settlements=settlements)
    # Raw atomic observations are retained even when the grid samples recovery.
    assert known.projection['causal_drawdown']['max_drawdown'] == '0.055'
    assert known.projection['causal_drawdown']['trough']['phase'] == 'FUNDING'
    assert known.projection['causal_drawdown']['trough']['timestamp'] == T + 1000
    assert known.projection['causal_drawdown']['trough']['available_at_ms'] == T + 1005
    final = inputs(simulation, end=1020, frequency=1020, settlements=settlements)
    assert final.projection['causal_drawdown']['max_drawdown'] == '0.09175'
    assert final.projection['full_run_covered'] is True


@pytest.mark.parametrize('damage', ['sequence', 'boolean', 'initial', 'nan', 'cash', 'unit', 'budget'])
def test_invalid_original_account_or_unit_fails_closed(damage):
    simulation = case()
    options = {}
    if damage == 'sequence': simulation['equity_history'][1]['sequence'] = 0
    elif damage == 'boolean': simulation['equity_history'][1]['timestamp'] = True
    elif damage == 'initial': simulation['equity_history'][0]['phase'] = 'MARKET'
    elif damage == 'nan': simulation['equity_history'][1]['equity'] = 'NaN'
    elif damage == 'cash': simulation['reconciliation']['residual'] = '1'
    elif damage == 'unit': options['target_currency'] = 'CNY'
    else: options['maximum_points'] = 1
    with pytest.raises(ValueError): inputs(simulation, **options)


def test_nonpositive_equity_is_not_cleaned_away_or_turned_into_a_risk_return():
    simulation = case()
    next(row for row in simulation['equity_history']
         if row['timestamp'] == T + 10 and row['phase'] == 'FILL')['equity'] = '0'
    value = inputs(simulation)
    assert value.returns is None
    assert value.projection['source_groups']['portfolio_returns']['status'] == 'UNAVAILABLE_NONPOSITIVE_EQUITY'
    assert value.projection['causal_drawdown']['max_drawdown'] == '1'
    assert value.projection['full_research_risk_ready'] is False


def test_an_intragrid_bankrupt_account_cannot_hide_behind_a_recovered_grid():
    simulation = case()
    next(row for row in simulation['equity_history']
         if row['timestamp'] == T + 10 and row['phase'] == 'FILL')['equity'] = '0'
    value = inputs(simulation, frequency=40)
    assert value.returns is None
    assert value.projection['source_groups']['portfolio_returns']['status'] == 'UNAVAILABLE_NONPOSITIVE_EQUITY'
    assert value.projection['causal_drawdown']['max_drawdown'] == '1'


def test_a_later_statistical_anchor_cannot_reset_original_cash_loss():
    from services.validation.gold_account_risk_inputs import build_account_risk_inputs
    value = build_account_risk_inputs(case(), sample=dict(start_ms=T + 20, end_ms=T + 40, frequency_ms=10),
                                     settlements=[], account_currency='USDT')
    assert value.opening_equity == D('100')
    assert value.initial_equity == D('90')
    assert value.returns == (D('0'), D('0'))
    assert value.projection['causal_drawdown']['max_drawdown'] == '0.1'


def test_an_uncovered_end_does_not_fabricate_flat_returns_after_the_last_observation():
    value = inputs(case(), end=50)
    assert value.returns is value.equity_curve is value.timestamps is None
    assert value.projection['source_groups']['portfolio_returns']['status'] == 'UNAVAILABLE_SAMPLE_COVERAGE'
    assert value.projection['full_run_covered'] is False


@pytest.mark.parametrize('damage', ['wrong-unit', 'wrong-volume', 'wrong-market', 'missing', 'segment-conflict'])
def test_account_currency_is_bound_to_all_original_product_manifests(damage):
    from services.validation.gold_account_risk_inputs import currency_from_dataset
    manifest = dict(instruments=['XAUUSDT'], units=dict(price='USDT', quantity='BASE', volume='QUOTE_USDT'))
    assert currency_from_dataset(dict(quote_manifest=manifest)) == 'USDT'
    second = copy.deepcopy(manifest)
    if damage == 'wrong-unit': second['units']['price'] = 'CNY'
    elif damage == 'wrong-volume': second['units']['volume'] = 'BASE'
    elif damage == 'wrong-market': second['instruments'] = ['XAUT']
    elif damage == 'missing': second['units'] = {}
    else: second['units']['price'] = 'USDC'
    with pytest.raises(ValueError): currency_from_dataset(dict(quote_manifest=dict(segments=[manifest, second])))


def test_unit_review_keeps_original_source_capacity_and_checks_the_last_part():
    from quantos.contracts.gold_chunked_source import MAX_PARTS
    from services.validation.gold_account_risk_inputs import currency_from_dataset
    manifest = dict(instruments=['XAUUSDT'], units=dict(price='USDT', quantity='BASE', volume='QUOTE_USDT'))
    parts = [manifest] * MAX_PARTS
    assert currency_from_dataset(dict(quote_manifest=dict(segments=parts))) == 'USDT'
    parts[-1] = dict(instruments=['XAUUSDT'], units=dict(price='USDC', quantity='BASE', volume='QUOTE_USDT'))
    with pytest.raises(ValueError): currency_from_dataset(dict(quote_manifest=dict(segments=parts)))
    with pytest.raises(ValueError): currency_from_dataset(dict(quote_manifest=dict(segments=[manifest] * (MAX_PARTS + 1))))


def test_repeatable_disk_views_are_consumed_without_eager_history_materialization():
    from tests.validation.test_gold_execution_risk_review import Repeatable
    simulation = case()
    ordinary = inputs(simulation)
    for field in ('equity_history', 'ledger_events'):
        simulation[field] = Repeatable(simulation[field])
    streamed = inputs(simulation, streamed=True)
    assert streamed == ordinary


def test_real_registered_candidates_and_net_account_export_risk_inputs_in_signed_facts(
        tmp_path, monkeypatch, technical_arguments):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import project_research_evidence_authority as authority
    from tests.tools.test_demo_gold_research_checkpoint import native_program, originals
    from tools import registered_gold_collection as bridge
    fixture = native_program(tmp_path, monkeypatch, source=technical_arguments['release_identity'],
        extended=True, register_original_trials=True)
    base = fixture['root'] / 'data/cache/registered_research'
    cut = ProjectResearchEvidenceReader(base / 'project', registry_database=base / 'registry.db')()
    invocation = bridge.invocation_id(1)
    before = originals(fixture['root'])
    for trial in cut['runs'][invocation]['plan']['trials']:
        subject = authority.project_subject(cut, invocation_id=invocation, native_trial_id=trial['native_trial_id'])
        facts = authority.project_facts(cut, subject)
        value = facts['execution_risk_review']['account_risk_inputs']
        net = trial['candidate_id'] == 'net_portfolio'
        assert facts['schema'] == ('QUANTOS_PROJECT_RESEARCH_FACTS_V9' if net else 'QUANTOS_PROJECT_RESEARCH_FACTS_V7')
        assert value['native_result_hash'] == facts['native_result_hash']
        assert value['sample']['frequency_ms'] == 10_000
        assert value['currency'] == 'USDT'
        assert value['source_groups']['portfolio_returns']['status'] == 'AVAILABLE'
        assert value['source_groups']['account_positions']['status'] == 'AVAILABLE'
        assert value['position_inputs']['alpha_allocation_bound'] is False
        assert value['source_groups']['alpha_returns']['status'] == ('AVAILABLE' if net else 'MISSING')
        if net:
            assert value['schema'] == 'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V4'
            assert value['source_groups']['weights']['status'] == 'AVAILABLE'
            assert value['net_attribution']['cash_conservation_residual'] == '0'
            assert value['net_attribution']['independent_strategy_pnl'] is False
        assert value['economic_authority'] is facts['promotion_approved'] is False
    assert originals(fixture['root']) == before
