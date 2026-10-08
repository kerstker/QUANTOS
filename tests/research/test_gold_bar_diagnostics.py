from decimal import getcontext

import pytest

from services.research.gold_bar_diagnostics import account_diagnostics, compact_diagnostic


def simulation():
    return dict(ending_state=dict(account_equity='999.9', cashflow_identity=dict(
        initial_equity='1000', ending_equity='999.9', attributed_change='-0.1',
        fee='0.01', funding='0', residual='0')),
        equity_history=[dict(equity='1000', drawdown='0'),
                        dict(equity='999.8', drawdown='0.0002'),
                        dict(equity='999.9', drawdown='0.0001')])


def test_projection_uses_cash_identity_and_whole_observed_drawdown_history():
    old = getcontext().prec
    try:
        getcontext().prec = 2
        result = account_diagnostics(simulation())
        assert result['net_equity_change'] == '-0.1'
        assert result['fees'] == '0.01'
        assert result['max_observed_drawdown'] == '0.0002'
        assert result['valuation'] == 'MARK_TO_MARKET_INCLUDES_OPEN_POSITIONS'
        assert getcontext().prec == 2
    finally:
        getcontext().prec = old


@pytest.mark.parametrize('damage', ['residual', 'identity', 'history', 'drawdown', 'nonfinite'])
def test_broken_account_evidence_cannot_be_published_as_cost_diagnostics(damage):
    value = simulation()
    if damage == 'residual':
        value['ending_state']['cashflow_identity']['residual'] = '0.01'
    elif damage == 'identity':
        value['ending_state']['cashflow_identity']['attributed_change'] = '1'
    elif damage == 'history':
        value['equity_history'][-1]['equity'] = '1001'
    elif damage == 'drawdown':
        value['equity_history'][1]['drawdown'] = '-1'
    else:
        value['ending_state']['cashflow_identity']['fee'] = 'NaN'
    with pytest.raises(ValueError):
        account_diagnostics(value)


def test_model_summary_rounding_is_separate_from_exact_account_report():
    assert compact_diagnostic('-0.10817727') == '-0.10818'
    assert compact_diagnostic(None) is None
    with pytest.raises(ValueError):
        compact_diagnostic('Infinity')


def test_target_observations_distinguish_netting_from_no_signal():
    from services.research.gold_bar_diagnostics import target_diagnostics
    rows = []
    for index, (a, b, c, net) in enumerate([
        ('0', '0', '0', '0'), ('0.01', '0', '-0.01', '0'),
        ('0.02', '0', '-0.01', '0.01'), ('0.01', '0.01', '0', '0.02'),
    ]):
        rows.append(dict(timestamp=index, desired=dict(channel=a, trend=b, reversion=c),
                         net_target=net, actual_quantity='0'))
    result = target_diagnostics(rows)
    assert result['observation_count'] == 4
    assert [result[k] for k in ('all_zero_count', 'opposed_zero_count',
                               'opposed_nonzero_count', 'unopposed_nonzero_count')] == [1, 1, 1, 1]
    assert target_diagnostics([])['observation_count'] == 0
    rows[-1]['net_target'] = '0'
    with pytest.raises(ValueError, match='TARGET_NET_MISMATCH'):
        target_diagnostics(rows)
