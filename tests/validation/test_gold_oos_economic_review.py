"""Preregistered original holdout checks cannot confer independent OOS authority."""
from copy import deepcopy
from decimal import Decimal, localcontext

import pytest

from quantos.contracts.decimal_arithmetic import canonical_decimal
from quantos.contracts.research_statistics import DAY_MS, fixed_frequency_equity
from tests.validation.test_gold_oos_material import declared


def policy():
    return dict(schema='QUANTOS_GOLD_OOS_ECONOMIC_POLICY_V1',
        method='ORIGINAL_FIXED_CANDIDATE_NET_HOLDOUT_ECONOMICS_V1',
        profile='ROBUST_RESEARCH_SIGNIFICANCE_V1',
        economic_checks=dict(minimum_net_return='0', minimum_benchmark_excess='0',
            maximum_drawdown='0.05', minimum_observations=30, minimum_duration_ms=30*DAY_MS),
        return_rule='STRICTLY_POSITIVE_AND_AT_LEAST_DECLARED_MINIMUM',
        missing_evidence='INSUFFICIENT_RETAIN_KNOWN_FAILURES',
        economic_authority=False, submission_enabled=False)


def core():
    value = declared()
    value.update(schema='QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V3',
        oos_evaluation_policy=policy())
    return value


def grid(equities):
    sample = dict(start_ms=0, end_ms=(len(equities)-1)*DAY_MS, frequency_ms=DAY_MS)
    history = [dict(timestamp=i*DAY_MS, sequence=i, equity=str(e)) for i, e in enumerate(equities)]
    return sample, fixed_frequency_equity(history, **sample)


def review(equities=None, baseline=None, *, complete=True):
    from services.validation.gold_oos_economic_review import review_grids
    equities = list(range(100, 131)) if equities is None else equities
    sample, candidate = grid(equities)
    _, cash = grid([100]*len(equities) if baseline is None else baseline)
    return review_grids(policy(), sample=sample, candidate_grid=candidate,
        benchmark_grid=cash, coverage_complete=complete)


def test_new_core_commits_oos_policy_without_changing_training_sample_or_unknown_access():
    from quantos.contracts import research_evaluation_plan as contract
    value = core()
    before = deepcopy(value)
    assert contract.validate_core(value) == value
    assert contract.statistical_sample(value)['end_ms'] < value['splits']['locked_oos']['start_ms']
    assert contract.core_status(value)['oos_status'] == 'UNKNOWN'
    assert value == before
    revised = deepcopy(value)
    revised['oos_evaluation_policy']['economic_checks']['maximum_drawdown'] = '0.04'
    assert contract.core_sha256(revised) != contract.core_sha256(value)


@pytest.mark.parametrize('damage', ['old-core', 'missing-policy', 'prescreen', 'short-duration',
    'few-observations', 'loss-limit', 'nan', 'bool', 'authority', 'extra-field'])
def test_holdout_policy_cannot_be_appended_or_relax_the_existing_research_floor(damage):
    from quantos.contracts import research_evaluation_plan as contract
    value = core()
    p = value['oos_evaluation_policy']
    if damage == 'old-core':
        value['schema'] = 'QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V2'
    elif damage == 'missing-policy':
        del value['oos_evaluation_policy']
    elif damage == 'prescreen':
        p['profile'] = 'RESEARCH_PRESCREEN_V1'
    elif damage == 'short-duration':
        p['economic_checks']['minimum_duration_ms'] -= 1
    elif damage == 'few-observations':
        p['economic_checks']['minimum_observations'] = 29
    elif damage == 'loss-limit':
        p['economic_checks']['maximum_drawdown'] = '0.05001'
    elif damage == 'nan':
        p['economic_checks']['minimum_net_return'] = 'NaN'
    elif damage == 'bool':
        p['economic_checks']['minimum_observations'] = True
    elif damage == 'authority':
        p['economic_authority'] = True
    else:
        p['approved'] = True
    with pytest.raises(ValueError):
        contract.validate_core(value)


def test_original_holdout_economics_are_separate_from_independent_oos_and_permission():
    value = review()
    assert value['economic_decision'] == 'PASS'
    assert value['oos_decision'] == 'INSUFFICIENT'
    assert value['metrics']['sample_net_return'] == '0.3'
    assert value['metrics']['benchmark_net_return'] == '0'
    assert value['checks']['net_return'] == value['checks']['benchmark_excess'] == 'PASS'
    assert value['checks']['observations'] == value['checks']['duration'] == 'PASS'
    assert value['independent_oos_verified'] is value['economic_authority'] is value['submission_enabled'] is False


@pytest.mark.parametrize('case', ['flat', 'negative', 'baseline-better', 'drawdown', 'nonpositive'])
def test_known_holdout_failure_cannot_be_masked_by_missing_independent_review(case):
    equity, baseline = list(range(100, 131)), None
    if case == 'flat':
        equity = [100]*31
    elif case == 'negative':
        equity[-1] = 99
    elif case == 'baseline-better':
        baseline = list(range(100, 162, 2))
    elif case == 'drawdown':
        equity[15] = 80
    else:
        equity[15] = 0
    value = review(equity, baseline)
    assert value['economic_decision'] == value['oos_decision'] == 'FAIL'


def test_short_positive_holdout_is_insufficient_but_observed_loss_remains_failure():
    positive = review([100, 101])
    assert positive['economic_decision'] == positive['oos_decision'] == 'INSUFFICIENT'
    assert positive['checks']['observations'] == positive['checks']['duration'] == 'INSUFFICIENT'
    negative = review([100, 99])
    assert negative['economic_decision'] == negative['oos_decision'] == 'FAIL'


def test_late_or_uncovered_cash_does_not_grade_a_partial_curve():
    value = review([100, 90], complete=False)
    assert value['economic_decision'] == value['oos_decision'] == 'INSUFFICIENT'
    assert value['metrics']['sample_net_return'] is None
    assert value['checks']['net_return'] == 'INSUFFICIENT'
    assert 'ORIGINAL_HOLDOUT_CASH_COVERAGE_INCOMPLETE' in value['reasons']


def test_original_late_receipt_is_not_graded_as_flat_or_profitable_holdout():
    from services.validation.gold_native_statistics import oos_account_material
    from tests.validation.test_gold_oos_material import accounts
    simulation, baseline, receipts = accounts()
    receipts[0]['available_at_ms'] = 90
    value = oos_account_material(simulation, baseline,
        sample=dict(start_ms=10, end_ms=40, frequency_ms=10), settlements=receipts,
        snapshot_as_of_ms=100, streamed=False, evaluation_policy=policy())
    assert value['candidate']['window']['net_return'] == '0'
    assert value['material_status'] == 'INSUFFICIENT'
    assert value['economic_review']['economic_decision'] == 'INSUFFICIENT'
    assert value['economic_review']['metrics']['sample_net_return'] is None


def test_partial_original_attempt_retains_the_exact_policy_without_invented_checks():
    from services.validation.gold_native_statistics import project_gold_oos_material
    from quantos.contracts.registered_research_run import EVALUATED_SOURCE_REANALYSIS_SCHEMAS, digest
    value = core()
    run = dict(plan=dict(schema=next(iter(EVALUATED_SOURCE_REANALYSIS_SCHEMAS)),
        evaluation_core=value, invocation_id='b'*64, input_sha256=value['input_sha256']),
        partial=True, originals={})
    actual = project_gold_oos_material(run, value['cohort'][0], project_cut_sha256='c'*64,
        snapshot_as_of_ms=value['registered_at_ms'])
    assert actual['schema'] == 'QUANTOS_GOLD_OOS_ORIGINAL_MATERIAL_V2'
    assert actual['economic_review']['policy_sha256'] == digest(value['oos_evaluation_policy'])
    assert actual['economic_review']['economic_decision'] == 'INSUFFICIENT'
    assert actual['economic_review']['candidate_grid_sha256'] is None


def test_different_benchmark_calendar_and_tampered_original_grid_are_rejected():
    from services.validation.gold_oos_economic_review import review_grids
    sample, candidate = grid(list(range(100, 131)))
    _, cash = grid([100]*31)
    for change in ('calendar', 'return'):
        changed = deepcopy(cash)
        changed['points'][-1]['timestamp' if change == 'calendar' else 'net_return'] = 1 if change == 'calendar' else '5'
        with pytest.raises(ValueError):
            review_grids(policy(), sample=sample, candidate_grid=candidate,
                benchmark_grid=changed, coverage_complete=True)


def test_original_decimal_drawdown_cannot_round_an_over_limit_loss_into_a_pass():
    from services.validation.project_research_statistics import review_native_economics
    sample, candidate = grid(['100', '94.999999999999999999999999999999'])
    _, cash = grid(['100', '100'])
    metrics, checks, _ = review_native_economics(candidate['points'], cash['points'], policy()['economic_checks'])
    assert Decimal(metrics['maximum_drawdown']) > Decimal('0.05')
    assert checks['drawdown'] == 'FAIL'
    assert sample['end_ms'] == DAY_MS


@pytest.mark.parametrize('precision', [6, 28, 80])
def test_original_holdout_review_is_independent_of_ambient_decimal_precision(precision):
    expected = review()
    with localcontext() as context:
        context.prec = precision
        assert review() == expected
    assert canonical_decimal(Decimal(expected['metrics']['sample_net_return'])) == '0.3'
