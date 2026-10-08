"""Original holdout economics; no candidate selection, independent lock or risk grant."""
from decimal import Decimal

from quantos.contracts import research_statistical_review as common
from quantos.contracts import gold_oos_evaluation as policy_contract
from quantos.contracts.decimal_arithmetic import ledger_decimal_context
from quantos.contracts.research_statistics import fixed_frequency_equity
from services.validation.project_research_statistics import review_native_economics

CHECKS = ('coverage', 'positive_equity', 'observations', 'duration', 'net_return', 'benchmark_excess', 'drawdown')


def _points(grid, sample):
    common.require(type(grid) is dict and type(grid.get('points')) is list, 'GOLD_OOS_ORIGINAL_GRID')
    rows = grid['points']
    common.require(len(rows) == common.sample(sample), 'GOLD_OOS_GRID_LENGTH')
    for row in rows:
        common.fields(row, 'timestamp equity net_return')
        common.integer(row['timestamp'])
        common.number(row['equity'])
    history = [dict(timestamp=row['timestamp'], sequence=i, equity=row['equity']) for i, row in enumerate(rows)]
    common.require(grid == fixed_frequency_equity(history, **sample), 'GOLD_OOS_ORIGINAL_GRID_CHANGED')
    return rows


@ledger_decimal_context()
def review_grids(policy, *, sample, candidate_grid, benchmark_grid, coverage_complete):
    """Internal port: the owning original reader supplies both retained grids."""
    policy = policy_contract.validate(policy)
    common.sample(sample)
    common.require(type(coverage_complete) is bool, 'GOLD_OOS_COVERAGE')
    checks = {name: 'INSUFFICIENT' for name in CHECKS}
    metrics = {name: None for name in ('sample_net_return', 'benchmark_net_return', 'benchmark_excess', 'maximum_drawdown')}
    reasons = set()
    candidate_hash = benchmark_hash = None
    if not coverage_complete or candidate_grid is None or benchmark_grid is None:
        reasons.add('ORIGINAL_HOLDOUT_CASH_COVERAGE_INCOMPLETE')
    else:
        candidate, baseline = _points(candidate_grid, sample), _points(benchmark_grid, sample)
        candidate_hash, benchmark_hash = common.digest(candidate_grid), common.digest(benchmark_grid)
        checks['coverage'] = 'PASS'
        complete_equity = all(Decimal(row['equity']) > 0 for row in (*candidate, *baseline))
        checks['positive_equity'] = 'PASS' if complete_equity else 'FAIL'
        rules = policy['economic_checks']
        checks['observations'] = 'PASS' if len(candidate)-1 >= rules['minimum_observations'] else 'INSUFFICIENT'
        checks['duration'] = 'PASS' if sample['end_ms']-sample['start_ms'] >= rules['minimum_duration_ms'] else 'INSUFFICIENT'
        if complete_equity:
            metrics, economics, original_reasons = review_native_economics(candidate, baseline, rules)
            checks.update(economics)
            reasons.update(original_reasons)
            # A flat account has no edge even when the minimum is exactly zero.
            for check, name in (('net_return', 'sample_net_return'), ('benchmark_excess', 'benchmark_excess')):
                if Decimal(metrics[name]) <= 0:
                    checks[check] = 'FAIL'
    reasons.update('GOLD_OOS_' + name.upper() + '_FAILED' for name, status in checks.items() if status == 'FAIL')
    reasons.update('GOLD_OOS_' + name.upper() + '_INSUFFICIENT' for name, status in checks.items() if status == 'INSUFFICIENT')
    decision = 'FAIL' if 'FAIL' in checks.values() else ('INSUFFICIENT' if 'INSUFFICIENT' in checks.values() else 'PASS')
    value = dict(schema='QUANTOS_GOLD_OOS_ECONOMIC_REVIEW_V1', policy_sha256=common.digest(policy),
        sample=dict(sample), candidate_grid_sha256=candidate_hash, benchmark_grid_sha256=benchmark_hash,
        economic_decision=decision, oos_decision='FAIL' if decision == 'FAIL' else 'INSUFFICIENT',
        metrics=metrics, checks=checks, reasons=sorted(reasons),
        scope='ORIGINAL_HOLDOUT_GRID_ECONOMICS_NOT_INDEPENDENT_OOS_OR_FULL_RISK',
        limitations=['INDEPENDENT_OOS_LOCK_ACCESS_AND_TIME_NOT_VERIFIED',
            'FULL_INTRAPERIOD_RISK_AND_COST_CALIBRATION_REQUIRED_SEPARATELY',
            'NO_OOS_CANDIDATE_SELECTION_OR_TRAINING_STATISTIC_REWRITE'],
        independent_oos_verified=False, economic_authority=False, submission_enabled=False)
    value['review_sha256'] = common.digest(value)
    return value
