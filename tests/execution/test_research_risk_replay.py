"""Original producers/engines demonstrate why reproducibility is not authority."""
import copy
from dataclasses import replace
from decimal import Decimal, localcontext, ROUND_DOWN

import pytest

from quantos.contracts.backtest_identity import decode_identity_payload, encode_identity_payload
from services.execution import authoritative_perpetual_backtest as native
from services.risk.research_risk.domain.gate import RiskApprovalGate
from services.risk.research_risk.domain.models import RiskConfig, RiskRun
from tests.execution import test_authoritative_perpetual_backtest as fixtures
from tools.registered_technical_research_run import technical_input


def arguments(monkeypatch, *, covered=False, forged=False):
    captured = {}
    with monkeypatch.context() as patch:
        patch.setattr(fixtures, 'run_authoritative_linear_perpetual_v2', lambda **kw: captured.update(kw))
        fixtures._run()
    if covered:
        decision = captured['decisions'][0]
        old = decision.research_risk
        axis = tuple(fixtures.DECISION - 70 + i for i in range(60))
        curve = [1000.0] * 60
        if forged:
            curve[20] = 1000.1
        previous = 1000.0
        returns = []
        for equity in curve:
            returns.append(equity / previous - 1.0)
            previous = equity
        full = replace(old.full_inputs, timestamps=axis)
        source_values = {name: value.value for name, value in old.source_records.items()}
        source_values.update(portfolio_returns=tuple(returns), initial_equity=1000.0,
                             equity_curve=tuple(curve), timestamps=axis)
        records = {name: replace(old.source_records[name], value=value,
                    event_time=axis[-1], available_time=fixtures.DECISION-2,
                    ingestion_time=fixtures.DECISION-1) for name, value in source_values.items()}
        risk = replace(old, returns=tuple(returns), initial_equity=1000.0,
                       equity_curve=tuple(curve), timestamps=axis, full_inputs=full,
                       source_records=records, observed_time=axis[-1],
                       available_time=fixtures.DECISION-2, ingestion_time=fixtures.DECISION-1)
        captured['decisions'] = (replace(decision, research_risk=risk),)
        first = captured['market_events'][0]
        captured['market_events'] = (replace(first,
            event=replace(first.event, timestamp=fixtures.DECISION-100),
            available_time=fixtures.DECISION-99, ingestion_time=fixtures.DECISION-98),
            *captured['market_events'][1:])
    return captured


def test_original_engine_cluster_keys_can_hide_actual_weight_then_adapter_rejects():
    returns = [0.001, -0.0005, 0.0002] * 20
    config = RiskConfig(max_single_alpha_weight=.3, max_cluster_weight=.15,
                        min_effective_number_of_bets=1)
    def evaluate(keys):
        run = RiskRun(portfolio_id='isolated-counterexample', config=config)
        RiskApprovalGate(config).evaluate(run, returns, {'leg-a': .1, 'leg-b': .1},
            [1000.0] * 60, {'leg-a': 1e9, 'leg-b': 1e9},
            {key: returns for key in keys}, timestamps=list(range(60)))
        return run.gate_reports['concentration']
    aligned = evaluate(('leg-a', 'leg-b'))
    wrong = evaluate(('unmapped-a', 'unmapped-b'))
    assert aligned.max_cluster == .2 and aligned.passed is False
    assert wrong.max_cluster == .1 and wrong.passed is True
    from services.execution.research_risk_replay import validate_risk_keys
    with pytest.raises(ValueError, match='RISK_KEY'):
        validate_risk_keys({'leg-a': .1, 'leg-b': .1}, {'unmapped-a': returns, 'unmapped-b': returns})


def test_self_consistent_original_history_is_not_this_ledger(monkeypatch):
    values = arguments(monkeypatch, covered=True, forged=True)
    result = native.run_authoritative_linear_perpetual_v2(**values)
    assert result['detail']['engine_attestations']['risk_history_ledger_replayed'] is False
    from services.execution.research_risk_replay import replay_technical_risk
    replay = replay_technical_risk(document=technical_input(values), original_result=result)
    assert replay['status'] == 'REJECTED'
    assert replay['decisions'][0]['coverage']['status'] == 'MISMATCH'
    assert replay['decisions'][0]['coverage']['points'][20]['actual_equity'] == '1000'
    assert replay['decisions'][0]['coverage']['points'][20]['claimed_equity'] == '1000.1'


def test_third_manifest_group_requires_full_native_binding(monkeypatch):
    values = arguments(monkeypatch)
    document = technical_input(values)
    data = decode_identity_payload(document['manifest_inputs']['data_identity'])
    data['research_risk_inputs'][0]['source_identity'] = 'unrelated-but-same-length'
    document['manifest_inputs']['data_identity'] = encode_identity_payload(data)
    # The older inert projection deliberately checks transport, not native reconstruction.
    from quantos.contracts.registered_technical_input import project_manifest_inputs
    project_manifest_inputs(document)
    from services.execution.research_risk_replay import decode_technical_input
    with pytest.raises(ValueError, match='MANIFEST_INPUT'):
        decode_technical_input(document)


def test_original_history_before_run_remains_insufficient(monkeypatch):
    values = arguments(monkeypatch)
    result = native.run_authoritative_linear_perpetual_v2(**values)
    from services.execution.research_risk_replay import replay_technical_risk
    replay = replay_technical_risk(document=technical_input(values), original_result=result)
    assert replay['status'] == 'REJECTED'  # Separate, demonstrated wrong risk keys.
    assert replay['decisions'][0]['coverage']['status'] == 'INSUFFICIENT'
    assert 'HISTORY_BEFORE_SOURCE_CUT' in replay['decisions'][0]['reasons']
    assert replay['economic_authority'] is False


def test_real_covered_flat_history_matches_without_inventing_strategy_legs(monkeypatch):
    values = arguments(monkeypatch, covered=True)
    result = native.run_authoritative_linear_perpetual_v2(**values)
    original = copy.deepcopy(result)
    from services.execution.research_risk_replay import replay_technical_risk
    replay = replay_technical_risk(document=technical_input(values), original_result=result)
    assert replay['decisions'][0]['coverage']['status'] == 'MATCH'
    assert replay['decisions'][0]['key_binding'] == 'UNMAPPED'
    assert replay['status'] == 'REJECTED'
    assert replay['reproducibility']['original_result_match'] is True
    assert result == original


@pytest.mark.parametrize('group', ('research_risk_inputs', 'market_events', 'execution_observations'))
def test_all_native_material_groups_are_rebuilt(monkeypatch, group):
    from services.execution.research_risk_replay import decode_technical_input
    document = technical_input(arguments(monkeypatch))
    data = decode_identity_payload(document['manifest_inputs']['data_identity'])
    data[group][0]['source_identity'] = 'replaced-after-original-capture'
    document['manifest_inputs']['data_identity'] = encode_identity_payload(data)
    with pytest.raises(ValueError, match='MANIFEST_INPUT'):
        decode_technical_input(document)


@pytest.mark.parametrize('tag', ('unknown.Class', 'os.system'))
def test_unknown_native_types_are_never_imported(monkeypatch, tag):
    from services.execution.research_risk_replay import decode_technical_input
    document = technical_input(arguments(monkeypatch))
    def replace_type(node):
        if type(node) is list:
            if node and node[0] == 'native':
                node[1] = tag
                return True
            return any(replace_type(child) for child in node)
        return False
    assert replace_type(document['arguments'])
    with pytest.raises(ValueError, match='TECHNICAL_NATIVE_TYPE'):
        decode_technical_input(document)


def test_original_native_result_cannot_be_relabelled_or_rehashed(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    from quantos.contracts.backtest_identity import exact_identity_hash
    values = arguments(monkeypatch)
    result = native.run_authoritative_linear_perpetual_v2(**values)
    result['detail']['result_hash_payload']['execution_results'][0]['status'] = 'REJECTED'
    result['detail']['result_hash'] = exact_identity_hash(result['detail']['result_hash_payload'])
    with pytest.raises(ValueError, match='ORIGINAL_RESULT'):
        replay_technical_risk(document=technical_input(values), original_result=result)


@pytest.mark.parametrize('field', ('authoritative', 'risk_history_ledger_replayed', 'deployment_equivalent_risk'))
def test_old_false_claims_are_not_upgraded(monkeypatch, field):
    from services.execution.research_risk_replay import replay_technical_risk
    values = arguments(monkeypatch)
    result = native.run_authoritative_linear_perpetual_v2(**values)
    if field == 'authoritative':
        result[field] = True
    else:
        result['detail']['engine_attestations'][field] = True
    with pytest.raises(ValueError, match='ORIGINAL_RESULT'):
        replay_technical_risk(document=technical_input(values), original_result=result)


def test_real_same_timestamp_funding_mark_trade_reconciles(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    values = arguments(monkeypatch)
    execution = fixtures._execution(timestamp=fixtures.DECISION+20)
    values['decisions'] = (fixtures._decision(execution=execution),)
    funding = values['market_events'][-1]
    values['market_events'] = (*values['market_events'], replace(funding,
        event=native.MarkEvent(execution.timestamp, 'BTCUSDT', Decimal('101'))))
    result = native.run_authoritative_linear_perpetual_v2(**values)
    settlement = next(row for row in result['detail']['result_hash_payload']['ledger_events']
                      if row['event_type'] == 'FUNDING_SETTLEMENT')
    assert settlement['pre_settlement_quantity'] == '0'
    assert result['detail']['result_hash_payload']['reconciliation']['funding'] == '0'
    assert replay_technical_risk(document=technical_input(values), original_result=result)['reproducibility']['ledger_match']


def test_rejected_capacity_does_not_create_replay_trade(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    values = arguments(monkeypatch)
    result = native.run_authoritative_linear_perpetual_v2(**values)
    old = values['decisions'][0]
    execution = replace(old.execution, capacity=fixtures._capacity(maximum_too_small=True))
    values['decisions'] = (replace(old, execution=execution, capacity_snapshots=(execution.capacity,)),)
    # Even with its own valid native manifest material, this input cannot borrow a result.
    with pytest.raises(ValueError):
        replay_technical_risk(document=technical_input(values), original_result=result)


def test_replay_uses_original_decimal_context(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    values = arguments(monkeypatch, covered=True)
    result = native.run_authoritative_linear_perpetual_v2(**values)
    document = technical_input(values)
    expected = replay_technical_risk(document=document, original_result=result)
    with localcontext() as context:
        context.prec = 7
        context.rounding = ROUND_DOWN
        assert replay_technical_risk(document=document, original_result=result) == expected


def test_report_contract_cannot_relabel_mismatched_points_as_match(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    from quantos.contracts.research_risk_replay import validate_report
    values = arguments(monkeypatch, covered=True)
    report = replay_technical_risk(document=technical_input(values),
                                   original_result=native.run_authoritative_linear_perpetual_v2(**values))
    report['decisions'][0]['coverage']['points'][0]['claimed_equity'] = '900'
    with pytest.raises(ValueError, match='POINT_MATCH'):
        validate_report(report)


def test_history_replay_contains_real_fees_funding_and_marks_before_second_decision(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    values = arguments(monkeypatch, covered=True)
    first = values['decisions'][0]
    axis = tuple(fixtures.DECISION-70+i for i in range(57)) + tuple(fixtures.DECISION+i for i in (10, 20, 30))
    # These are the original ledger's exact cash-flow values, not a new pricing formula.
    curve = (1000.0,) * 57 + (999.94, 1000.0299, 1000.1299)
    previous = 1000.0
    returns = []
    for equity in curve:
        returns.append(equity/previous-1)
        previous = equity
    evidence = first.research_risk
    full = replace(evidence.full_inputs, total_aum=curve[-1], timestamps=axis)
    source_values = {k: v.value for k, v in evidence.source_records.items()}
    source_values.update(portfolio_returns=tuple(returns), equity_curve=curve, timestamps=axis,
        capacity_and_aum=dict(capacity_cny=dict(full.capacity_cny), daily_volume_cny=full.daily_volume_cny,
                              total_aum=full.total_aum))
    records = {k: replace(evidence.source_records[k], value=v, event_time=axis[-1],
               available_time=fixtures.DECISION+98, ingestion_time=fixtures.DECISION+99)
               for k, v in source_values.items()}
    evidence = replace(evidence, returns=tuple(returns), equity_curve=curve, timestamps=axis,
        full_inputs=full, source_records=records, observed_time=axis[-1],
        available_time=fixtures.DECISION+98, ingestion_time=fixtures.DECISION+99)
    execution = replace(fixtures._execution(timestamp=fixtures.DECISION+110, quantity='-0.1'),
                        execution_id='native-close')
    second = replace(first, decision_id='close-after-native-funding', timestamp=fixtures.DECISION+100,
        execution=execution, research_risk=evidence,
        signal_inputs=(replace(first.signal_inputs[0],
                               value={'instrument_id': 'BTCUSDT', 'score': Decimal('0')}),))
    values['decisions'] = (first, second)
    last = values['market_events'][1]
    values['market_events'] = (*values['market_events'], replace(last,
        event=replace(last.event, timestamp=fixtures.DECISION+200),
        available_time=fixtures.DECISION+201, ingestion_time=fixtures.DECISION+202))
    original = native.run_authoritative_linear_perpetual_v2(**values)
    assert original['detail']['result_hash_payload']['reconciliation']['ending_equity'] == '999.8699'
    report = replay_technical_risk(document=technical_input(values), original_result=original)
    coverage = report['decisions'][1]['coverage']
    assert coverage['status'] == 'MATCH'
    assert [p['actual_equity'] for p in coverage['points'][-3:]] == ['999.94', '1000.0299', '1000.1299']
    assert len({p['input_prefix_sha256'] for p in coverage['points'][-3:]}) == 3
    assert all(report['decisions'][1]['engine_checks'][engine]['status'] == 'REPLAYED_MATCH'
               for engine in ('market', 'drawdown', 'stress'))


def test_actual_multi_instrument_keys_do_not_invent_strategy_attribution(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    case = fixtures._two_instrument_existing_position_case(second_decision_time=fixtures.DECISION+100,
        btc_post_fill_mark_time=fixtures.DECISION+11, btc_quantity='.1', btc_score='.25', mark_based_equity=990.04)
    second = case['decisions'][1]
    evidence = second.research_risk
    renamed = dict(zip(('BTCUSDT', 'ETHUSDT'), evidence.full_inputs.alpha_returns.values(), strict=True))
    full = replace(evidence.full_inputs, alpha_returns=renamed)
    sources = dict(evidence.source_records)
    sources['alpha_returns'] = replace(sources['alpha_returns'], value=renamed)
    case['decisions'] = (case['decisions'][0], replace(second, research_risk=replace(evidence,
                            full_inputs=full, source_records=sources)))
    captured = {}
    with monkeypatch.context() as patch:
        patch.setattr(fixtures, 'run_authoritative_linear_perpetual_v2', lambda **kw: captured.update(kw))
        fixtures._run(**case)
    result = native.run_authoritative_linear_perpetual_v2(**captured)
    report = replay_technical_risk(document=technical_input(captured), original_result=result)
    decision = report['decisions'][1]
    assert decision['key_binding'] == 'MATCH'
    assert 'INSUFFICIENT_CORRELATION_COHORT' in decision['reasons']
    assert decision['engine_checks']['correlation']['status'] == 'INSUFFICIENT'
    assert report['economic_authority'] is False


@pytest.mark.parametrize('field', ('economic_authority', 'submission_enabled'))
def test_report_is_never_an_authorization(monkeypatch, field):
    from services.execution.research_risk_replay import replay_technical_risk
    from quantos.contracts.research_risk_replay import validate_report
    values = arguments(monkeypatch)
    report = replay_technical_risk(document=technical_input(values),
                                   original_result=native.run_authoritative_linear_perpetual_v2(**values))
    report[field] = True
    with pytest.raises(ValueError, match='AUTHORITY'):
        validate_report(report)


def test_unknown_engine_and_numeric_bool_alias_are_rejected(monkeypatch):
    from services.execution.research_risk_replay import replay_technical_risk
    from quantos.contracts.research_risk_replay import validate_report
    values = arguments(monkeypatch, covered=True)
    original = replay_technical_risk(document=technical_input(values),
                                    original_result=native.run_authoritative_linear_perpetual_v2(**values))
    bad = copy.deepcopy(original)
    bad['decisions'][0]['engine_checks']['new-authority'] = {}
    with pytest.raises(ValueError, match='ENGINES'):
        validate_report(bad)
    bad = copy.deepcopy(original)
    bad['decisions'][0]['coverage']['points'][0]['input_events'] = True
    with pytest.raises(ValueError, match='COVERED'):
        validate_report(bad)


def test_input_complexity_is_bounded_before_reconstruction():
    from services.execution.research_risk_replay import decode_technical_input
    shared = 'x' * 1_000_000
    with pytest.raises(ValueError, match='BYTES'):
        decode_technical_input({'arguments': [shared] * 100})


def test_real_short_liquidation_backstop_is_part_of_replayed_cut(monkeypatch):
    from services.execution import perpetual_ledger_v2 as ledger
    from services.execution.research_risk_replay import replay_technical_risk
    values = arguments(monkeypatch)
    first = values['decisions'][0]
    execution = replace(first.execution, quantity_delta=Decimal('-.1'))
    first = replace(first, execution=execution, signal_inputs=(replace(first.signal_inputs[0],
                    value={'instrument_id': 'BTCUSDT', 'score': Decimal('-.25')}),))
    values['decisions'] = (first,)
    terminal = values['market_events'][1]
    terminal = replace(terminal, event=replace(terminal.event, mark_price=Decimal('20000'),
        liquidation_backstop=ledger.LiquidationBackstop(insurance_adjustment=Decimal('1010.0499'),
                                                        adl_adjustment=Decimal('0'))))
    values['market_events'] = (values['market_events'][0], terminal, values['market_events'][2])
    original = native.run_authoritative_linear_perpetual_v2(**values)
    payload = original['detail']['result_hash_payload']
    assert len(payload['liquidations']) == 1
    assert payload['reconciliation']['ending_equity'] == '0'
    assert payload['reconciliation']['insurance_adjustment'] == '1010.0499'
    report = replay_technical_risk(document=technical_input(values), original_result=original)
    assert report['reproducibility']['ledger_match'] is True
    assert report['economic_authority'] is False


def test_decoded_short_price_floor_still_rejects_original_breach(monkeypatch):
    from services.execution.research_risk_replay import decode_technical_input
    case = fixtures._two_instrument_existing_position_case(second_decision_time=fixtures.DECISION+100,
        btc_post_fill_mark_time=fixtures.DECISION+11, btc_quantity='-1.4', btc_score='-3.5',
        eth_quantity='1.7', eth_score='4.25', mark_based_equity=1137.76, risk_equity=999.16)
    captured = {}
    with monkeypatch.context() as patch:
        patch.setattr(fixtures, 'run_authoritative_linear_perpetual_v2', lambda **kw: captured.update(kw))
        fixtures._run(**case)
    decoded = decode_technical_input(technical_input(captured))
    with pytest.raises(native.AuthoritativeBacktestInputError, match=r'Concentration: .*max_single=0\.170'):
        native.run_authoritative_linear_perpetual_v2(**decoded)


def test_single_actual_instrument_cannot_satisfy_original_two_series_gate(monkeypatch):
    from services.execution.research_risk_replay import decode_technical_input
    values = arguments(monkeypatch)
    first = values['decisions'][0]
    old = first.research_risk
    series = {'BTCUSDT': old.returns}
    full = replace(old.full_inputs, alpha_returns=series)
    records = dict(old.source_records)
    records['alpha_returns'] = replace(records['alpha_returns'], value=series)
    values['decisions'] = (replace(first, research_risk=replace(old, full_inputs=full, source_records=records)),)
    decoded = decode_technical_input(technical_input(values))
    with pytest.raises(native.AuthoritativeBacktestInputError, match='alpha_returns_need_at_least_two'):
        native.run_authoritative_linear_perpetual_v2(**decoded)
