"""Review original Gold gate records without granting research authority."""

import copy
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

import pytest

from services.execution.perpetual_ledger_v2 import FundingEvent
from services.execution.perpetual_simulation import SimulationCostModel, SimulationLimits
from tests.research.test_gold_loss_control import run_control
from tests.research.test_gold_v2_strategy import frame
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def review(simulation, loss, **options):
    from services.validation.gold_execution_risk_review import review_execution_risk

    return review_execution_risk(simulation, loss, **options)


def native_case(*, costs=None, frames=None):
    control, simulation = run_control(
        frames or tuple(frame(i) for i in (0, 10, 20, 30, 40)), costs=costs
    )
    return simulation, control.report(simulation)


def test_original_fee_loss_reductions_remain_visible_and_never_qualify():
    simulation, loss = native_case(costs=SimulationCostModel(fee_rate=Decimal('.5')))
    before = copy.deepcopy((simulation, loss))
    value = review(simulation, loss)
    assert value['status'] == 'INSUFFICIENT'
    assert value['projected_fill_checks']['status'] == 'CONSISTENT'
    assert value['projected_fill_checks']['count'] == 2
    assert value['loss_control']['first_threshold_breach'] == loss['trigger']
    assert value['loss_control']['status'] == 'STOPPED_FLAT'
    assert value['loss_control']['ending_equity'] == '90'
    assert value['reconciliation_residual'] == '0'
    assert value['native_result_hash'] == simulation['result_hash']
    assert value['mathematical_independence'] is False
    assert value['full_research_risk_9_of_9'] is False
    assert 'NINE_ENGINE_RISK_REPLAY_NOT_IMPLEMENTED' in value['limitations']
    assert value['economic_authority'] is value['submission_enabled'] is False
    assert (simulation, loss) == before


@pytest.mark.parametrize('damage', ['minimum', 'gross', 'positive', 'drawdown', 'single', 'boolean', 'nan'])
def test_forged_native_risk_predicates_fail_closed(damage):
    simulation, loss = native_case()
    event = simulation['risk_events'][0]
    if damage == 'minimum':
        event['risk_equity'] = '1000000'
    elif damage == 'gross':
        event['gross_notional'] = '0'
    elif damage in ('positive', 'drawdown', 'single'):
        event['checks'][{'positive': 'positive_equity', 'drawdown': 'drawdown', 'single': 'single_name'}[damage]] = False
    elif damage == 'boolean':
        event['approved'] = 1
    else:
        event['executable_equity'] = 'NaN'
    with pytest.raises(ValueError):
        review(simulation, loss)


@pytest.mark.parametrize('damage', ['sequence', 'sequence-boolean', 'equity', 'initial', 'status', 'residual', 'history-order'])
def test_forged_loss_record_or_cash_reconciliation_fails_closed(damage):
    simulation, loss = native_case(costs=SimulationCostModel(fee_rate=Decimal('.5')))
    if damage in ('sequence', 'equity'):
        loss['trigger'][damage] = 0 if damage == 'sequence' else '94'
    elif damage == 'sequence-boolean':
        loss['trigger']['sequence'] = True
    elif damage == 'initial':
        loss['policy']['initial_equity'] = '101'
    elif damage == 'status':
        loss['status'] = 'STOPPING_UNRESOLVED'
    elif damage == 'residual':
        simulation['reconciliation']['residual'] = '1'
    else:
        simulation['equity_history'][1]['sequence'] = 0
    with pytest.raises(ValueError):
        review(simulation, loss)


def test_funding_loss_and_unresolved_exit_are_preserved():
    settlement = frame(1000)
    settlement = replace(settlement, market_events=(
        FundingEvent(settlement.timestamp, 'XAUUSDT', Decimal('.1'), Decimal('100')),
        *settlement.market_events,
    ))
    simulation, loss = native_case(
        frames=(frame(0), frame(10, size='.05'), settlement),
        costs=SimulationCostModel(fee_rate=Decimal('.9')),
    )
    value = review(simulation, loss)
    assert value['loss_control']['status'] == 'STOPPING_UNRESOLVED'
    assert value['loss_control']['first_threshold_breach']['equity'] == '95'
    assert value['projected_fill_checks']['rejected_count'] == 1
    assert 'STOP_EXIT_NOT_RESOLVED' in value['limitations']


def test_an_unobserved_terminal_loss_is_unknown_instead_of_an_invented_stop():
    simulation, loss = native_case()
    # A retained V1 report has no observation witness. Its unobserved tail stays
    # unknown; a V2 report with an unchanged witness must reject this rewrite.
    loss['schema'] = 'GOLD_RESEARCH_LOSS_CONTROL_V1'
    del loss['history_observation']
    simulation['equity_history'][-1]['equity'] = '90'
    simulation['ending_state']['account_equity'] = '90'
    simulation['reconciliation']['ending_equity'] = '90'
    simulation['reconciliation']['attributed_change'] = '-10'
    simulation['ending_state']['cashflow_identity'] = dict(simulation['reconciliation'])
    value = review(simulation, loss)
    assert value['loss_control']['original_trigger'] is None
    assert value['loss_control']['first_threshold_breach']['equity'] == '90'
    assert 'LOSS_CALLBACK_OBSERVATION_COVERAGE_UNPROVEN' in value['limitations']


class Repeatable(Sequence):
    """A disk-view stand-in that cannot be sliced into an eager history."""

    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        assert not isinstance(index, slice)
        return self.rows[index]

    def __iter__(self):
        return iter(self.rows)


def test_repeatable_history_review_matches_regular_originals_and_obeys_its_budget():
    simulation, loss = native_case()
    expected = review(simulation, loss)
    for key in ('equity_history', 'risk_events', 'orders'):
        simulation[key] = Repeatable(simulation[key])
    assert review(simulation, loss, streamed=True) == expected
    with pytest.raises(ValueError):
        review(simulation, loss, maximum_points=1)


def test_native_rejected_cost_fill_is_diagnostic_not_a_missing_or_successful_fill():
    from tests.research.test_gold_v2_strategy import execute
    from services.execution.perpetual_simulation import OrderIntent
    from services.research.gold_loss_control import GoldResearchLossControl

    control = GoldResearchLossControl(
        lambda ctx: (OrderIntent('too-large', 'XAUUSDT', Decimal('.1')),)
        if ctx.timestamp == frame(0).timestamp else (),
        initial_equity=Decimal('100'),
    )
    simulation = execute(
        (frame(0), frame(10)), control, initial_collateral=Decimal('100'),
        limits=SimulationLimits(max_gross_leverage=Decimal('.101')),
        costs=SimulationCostModel(fee_rate=Decimal('.9')),
        risk_policy=control.risk, risk_policy_identity=control.identity,
    )
    assert simulation['risk_events'][0]['approved'] is False
    value = review(simulation, control.report(simulation))
    assert value['projected_fill_checks']['rejected_count'] == 1
    assert value['projected_fill_checks']['approved_count'] == 0
    assert value['status'] == 'INSUFFICIENT'


def test_declared_original_candidate_and_portfolio_risk_enter_project_facts(
    tmp_path, monkeypatch, technical_arguments
):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import project_research_evidence_authority as authority
    from tests.tools.test_demo_gold_research_checkpoint import native_program, originals
    from tools import registered_gold_collection as bridge

    fixture = native_program(
        tmp_path, monkeypatch, source=technical_arguments['release_identity'],
        extended=True, register_original_trials=True,
    )
    base = fixture['root'] / 'data/cache/registered_research'
    cut = ProjectResearchEvidenceReader(base / 'project', registry_database=base / 'registry.db')()
    invocation = bridge.invocation_id(1)
    before = originals(fixture['root'])
    for trial in cut['runs'][invocation]['plan']['trials']:
        subject = authority.project_subject(cut, invocation_id=invocation, native_trial_id=trial['native_trial_id'])
        facts = authority.project_facts(cut, subject)
        diagnostic = facts['execution_risk_review']
        net=trial['candidate_id']=='net_portfolio'
        assert facts['schema'] == ('QUANTOS_PROJECT_RESEARCH_FACTS_V9' if net else 'QUANTOS_PROJECT_RESEARCH_FACTS_V7')
        assert diagnostic['schema'] == ('QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V6' if net else 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V4')
        if net:
            attribution=diagnostic['account_risk_inputs']['net_attribution']
            assert attribution['cash_conservation_residual']=='0'
            assert attribution['independent_strategy_pnl'] is False
        assert diagnostic['native_result_hash'] == facts['native_result_hash']
        assert diagnostic['loss_control']['policy_preregistered'] is True
        assert diagnostic['projected_fill_checks']['status'] == 'CONSISTENT'
        assert 'NINE_ENGINE_RISK_REPLAY_NOT_IMPLEMENTED' in facts['limitations']
        assert 'RISK_REPLAY_ADAPTER_UNAVAILABLE' in facts['limitations']
        assert facts['promotion_approved'] is facts['submission_enabled'] is False
    assert originals(fixture['root']) == before


@pytest.mark.parametrize('damage', ['missing-gate', 'extra-gate', 'quantity', 'order', 'venue'])
def test_accepted_risk_reports_must_cover_the_exact_original_ledger_fills(damage):
    simulation, loss = native_case(costs=SimulationCostModel(fee_rate=Decimal('.5')))
    if damage == 'missing-gate':
        simulation['risk_events'].pop(0)
    elif damage == 'extra-gate':
        simulation['risk_events'].append(copy.deepcopy(simulation['risk_events'][-1]))
    else:
        fill = next(row for row in simulation['ledger_events'] if row['event_type'] == 'TRADE')
        field, value = {'quantity': ('filled_quantity', '.01'), 'order': ('order_id', 'other'),
                        'venue': ('instrument_id', 'BTCUSDT')}[damage]
        fill[field] = value
    with pytest.raises(ValueError):
        review(simulation, loss)
