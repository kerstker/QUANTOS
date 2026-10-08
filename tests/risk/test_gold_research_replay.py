"""Original engines must review the observed account, including intragrid risk."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D

import pytest

from quantos.streaming_identity import StreamArray
from tests.research.test_gold_v2_strategy import T
from tests.validation.test_gold_net_attribution import portfolio
from tests.validation.test_gold_research_risk_policy import policy


def account(monkeypatch, *, fee='.001', cash='1000', end=40, frequency=None):
    from services.validation.gold_account_risk_inputs import build_account_risk_inputs
    value = portfolio(monkeypatch, fee=fee, cash=cash)
    frequency = end if frequency is None else frequency
    sample = dict(start_ms=T, end_ms=T + end, frequency_ms=frequency)
    return build_account_risk_inputs(value['simulation'], portfolio=value, sample=sample,
        settlements=[], account_currency='USDT', research_risk_policy=policy(
            start=T, end=T + end, frequency=frequency)), value


def review(value, **kwargs):
    from services.risk.research_risk.gold_research_replay import review_gold_account
    return review_gold_account(value, **kwargs)


def test_historical_shared_exposure_cannot_disappear_with_terminal_flat_book(monkeypatch):
    value, original = account(monkeypatch, fee='.5', cash='200')
    assert all(v == 0 for v in value.net_attribution.weights.values())
    before = deepcopy(original)
    result = review(value)
    gate = result['engines']['concentration']
    assert gate['status'] == 'FAIL' and gate['invocations'] > 0
    assert gate['shared_instrument_effective_bets_upper_bound'] == 1
    assert gate['original_report']['max_cluster'] > 0
    assert result['status'] == 'REJECTED'
    assert result['full_research_risk_9_of_9'] is False
    assert original == before


def test_original_policy_and_account_identity_are_bound_without_model_defaults(monkeypatch):
    value, original = account(monkeypatch)
    result = review(value)
    assert set(result['engines']) == set(value.projection['risk_policy']['policy']['input_contract']['engine_set'])
    assert result['native_result_hash'] == original['simulation']['result_hash']
    assert result['policy_sha256'] == value.projection['risk_policy']['policy_sha256']
    model = result['engines']['model_alpha']
    assert model['status'] == 'INSUFFICIENT' and model['invocations'] == 0
    assert 'BOUND_ORIGINAL_TRIAL_MODEL_FACTS_MISSING' in model['reasons']
    assert result['economic_authority'] is result['promotion_approved'] is result['submission_enabled'] is False


def test_atomic_loss_and_declared_interval_duration_use_separate_original_reports(monkeypatch):
    value, _ = account(monkeypatch, fee='.5', cash='200')
    result = review(value)
    gate = result['engines']['drawdown']
    assert gate['status'] == 'FAIL'
    assert gate['original_report']['max_drawdown'] == pytest.approx(float(value.projection['causal_drawdown']['max_drawdown']))
    assert gate['duration_unit'] == 'DECLARED_RETURN_INTERVALS'
    assert gate['original_duration_report']['max_duration'] <= len(value.equity_curve)


def test_dormant_zero_contribution_is_not_an_independent_correlation_observation(monkeypatch):
    value, _ = account(monkeypatch)
    result = review(value)
    gate = result['engines']['correlation']
    assert 'reversion' not in gate['component_ids']
    assert gate['status'] == 'INSUFFICIENT'
    assert gate['invocations'] == 0


@pytest.mark.parametrize('damage', ['unit', 'axis', 'boolean', 'nan', 'policy'])
def test_unbound_or_invalid_original_vectors_fail_closed(monkeypatch, damage):
    value, _ = account(monkeypatch)
    if damage == 'unit':
        value = replace(value, currency='CNY')
    elif damage == 'axis':
        value = replace(value, timestamps=(value.timestamps[0] + 1,))
    elif damage == 'boolean':
        value = replace(value, returns=(True,))
    elif damage == 'nan':
        value = replace(value, returns=(D('NaN'),))
    else:
        p = deepcopy(value.projection)
        p['risk_policy']['policy']['config']['max_drawdown'] = '0.5'
        value = replace(value, projection=p)
    with pytest.raises(ValueError):
        review(value)


def test_no_policy_cannot_silently_use_original_defaults(monkeypatch):
    value, _ = account(monkeypatch)
    p = deepcopy(value.projection)
    del p['risk_policy']
    result = review(replace(value, projection=p))
    assert result['status'] == 'INSUFFICIENT'
    assert all(v['invocations'] == 0 for v in result['engines'].values())


def test_original_read_port_expires_and_cannot_be_serialized_as_an_input(monkeypatch):
    from services.research.gold_native_source_reader import SourceReports
    value, _ = account(monkeypatch)
    port = SourceReports()
    assert port.review_gold_risk(value) == review(value)
    port.close_inputs()
    with pytest.raises(ValueError, match='CONTEXT_CLOSED'):
        port.review_gold_risk(value)


def test_retained_original_population_keeps_risk_port_and_owner_lease(monkeypatch):
    from services.research.gold_native_source_reader import SourceReports
    from services.validation.project_research_evidence_artifact import retained_reports
    from quantos.contracts.registered_research_run import CHUNKED_GOLD_SCHEMAS
    value, _ = account(monkeypatch)
    owner = SourceReports()
    owner['historical'] = object()
    owner['newer'] = object()
    cut = dict(runs={'historical': dict(plan=dict(schema=next(iter(CHUNKED_GOLD_SCHEMAS))),
        result_material=dict(status='COMPLETE'), input_material=dict(status='INCOMPLETE'))})
    retained = retained_reports(cut, owner)
    assert set(retained) == {'historical'}
    assert retained.review_gold_risk(value) == owner.review_gold_risk(value)
    twice = retained_reports(cut, retained)
    owner.close_inputs()
    for port in (retained, twice):
        with pytest.raises(ValueError, match='CONTEXT_CLOSED'):
            port.review_gold_risk(value)


def capacity_account(value, *, adv='0.01', delay=None):
    from services.validation.gold_capacity_inputs import build_capacity_inputs
    from tests.validation.test_gold_capacity_inputs import policy as capacity_policy, snapshots
    sample = value.projection['sample']
    cap = deepcopy(snapshots()[0])
    day = T // 86400000 * 86400000
    p = cap['policy']
    p.update(period_index=day // 86400000, consumption_window_start=day,
        consumption_window_end=day + 86400000, required_adv_window_start=day - 30 * 86400000,
        required_adv_window_end=day, adv_notional=adv, maximum_notional=str(D(adv) * D('.01')))
    cap.update(adv_window_start=day - 30 * 86400000, adv_window_end=day,
        available_time=day if delay is None else delay, ingestion_time=day if delay is None else delay)
    def rows():
        for row in value.position_history.items():
            yield dict(timestamp=row['available_at_ms'], sequence=row['sequence'], equity=row['equity'])
    capacity = build_capacity_inputs(capacity_policy(sample), capacity_history=[cap],
        capacity_history_policy='GOLD_ORIGINAL_PUBLIC_ADV_CAPACITY_SNAPSHOTS_V1', sample=sample,
        account_observations=StreamArray(rows), allocation_observations=value.net_attribution.history,
        account_returns_available=True, maximum_snapshots=100, maximum_intervals=100, maximum_points=100)
    p = deepcopy(value.projection)
    p['capacity_inputs'] = capacity.projection
    return replace(value, capacity_inputs=capacity, projection=p)


def test_intragrid_capacity_uses_original_atomic_allocation_not_closing_weights(monkeypatch):
    value, _ = account(monkeypatch)
    value = capacity_account(value)
    # The declared grid begins flat; only original atomic states reveal the exposure.
    capacity = value.capacity_inputs
    assert capacity.engine_inputs[0]['alpha_daily_capacity'] == {}
    result = review(value)
    for name in ('capacity', 'liquidity'):
        assert result['engines'][name]['status'] == 'FAIL'
        assert result['engines'][name]['invocations'] > 0


def test_all_eight_available_engines_execute_on_original_aligned_series(monkeypatch):
    from services.validation.gold_market_states import MarketStateInputs
    value, _ = account(monkeypatch, frequency=10)
    value = capacity_account(value, adv='1000000')
    # Synthetic labels isolate the risk-port unit test. Production labels come
    # exclusively from the separately validated original-market adapter.
    value = replace(value, market_states=MarketStateInputs(('TEST_REGIME',) * 4,
        StreamArray(lambda: iter(())), dict(synthetic=True)))
    result = review(value)
    assert result['engines_executed'] == 8
    assert result['engines']['market']['invocations'] == 1
    assert result['engines']['regime']['original_report']['total_regimes'] == 1
    assert result['engines']['correlation']['invocations'] == 1
    assert result['engines']['stress']['original_report']['provenance'] == 'REAL_HISTORICAL_REPLAY'
    assert result['engine_replay_completed'] is result['full_research_risk_9_of_9'] is False


def test_missing_early_atomic_capacity_cannot_be_filled_by_a_later_receipt(monkeypatch):
    value, _ = account(monkeypatch)
    value = capacity_account(value, adv='1000000', delay=T + 11)
    result = review(value)
    for name in ('capacity', 'liquidity'):
        gate = result['engines'][name]
        assert gate['status'] == 'INSUFFICIENT' and gate['invocations'] > 0
        assert 'ATOMIC_CAPACITY_OR_ACCOUNT_COVERAGE_INCOMPLETE' in gate['reasons']


def test_atomic_history_budget_is_enforced_before_reporting_pass(monkeypatch):
    value, _ = account(monkeypatch)
    with pytest.raises(ValueError, match='ATOMIC_BUDGET'):
        review(value, maximum_atomic_points=1)


def test_short_grid_stress_uses_actual_timestamps_and_never_synthetic_multiplier(monkeypatch):
    value, _ = account(monkeypatch)
    # One genuine return still supports realized stress; correlation needs at least two.
    result = review(value)
    stress = result['engines']['stress']['original_report']
    assert stress['provenance'] == 'REAL_HISTORICAL_REPLAY'
    assert stress['scenarios_skipped'] > 0


@pytest.mark.parametrize('field', ['age_days', 'validation_confidence', 'paper_confidence',
    'decay_flag', 'overfit_flag', 'leakage_flag'])
@pytest.mark.parametrize('damage', ['missing', 'none', 'bool_or_string'])
def test_model_engine_never_treats_unknown_or_malformed_model_facts_as_healthy(field, damage):
    from services.risk.research_risk.domain.models import RiskConfig
    from services.risk.research_risk.domain.risk_modules import ModelAlphaRiskEngine
    item = dict(age_days=1.0, validation_confidence=.8, paper_confidence=.8,
        decay_flag=False, overfit_flag=False, leakage_flag=False)
    if damage == 'missing':
        del item[field]
    else:
        item[field] = None if damage == 'none' else ('false' if field.endswith('_flag') else True)
    assert ModelAlphaRiskEngine().evaluate([item], RiskConfig()).passed is False


@pytest.mark.parametrize('field,value', [('age_days', -1), ('age_days', float('nan')),
    ('validation_confidence', 1.1), ('paper_confidence', float('inf'))])
def test_model_engine_rejects_nonfinite_negative_age_and_out_of_range_confidence(field, value):
    from services.risk.research_risk.domain.models import RiskConfig
    from services.risk.research_risk.domain.risk_modules import ModelAlphaRiskEngine
    item = dict(age_days=1.0, validation_confidence=.8, paper_confidence=.8,
        decay_flag=False, overfit_flag=False, leakage_flag=False)
    item[field] = value
    assert ModelAlphaRiskEngine().evaluate([item], RiskConfig()).passed is False
