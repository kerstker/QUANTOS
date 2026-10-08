"""Bounded statistical declarations and diagnostics; no custody or permission."""
import copy
import math
import re
from decimal import Decimal

from quantos.contracts.decimal_arithmetic import canonical_decimal
from quantos.contracts.registered_research_run import canonical, digest as digest
from quantos.contracts import gold_long_statistics_budget as long_budget

SCHEMA='QUANTOS_RESEARCH_STATISTICAL_POLICY_V1'
COHORT_SCHEMA='QUANTOS_RESEARCH_STATISTICAL_COHORT_V1'
REPORT_SCHEMA='QUANTOS_PROJECT_STATISTICAL_REPORT_V1'
GOLD_REPORT_SCHEMA='QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V1'
SCOPED_GOLD_REPORT_SCHEMA='QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V2'
LONG_GOLD_REPORT_SCHEMA='QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V3'
GOLD_BACKTEST_REPORT_SCHEMA='QUANTOS_GOLD_BACKTEST_STATISTICAL_REPORT_V1'
SCOPED_GOLD_REPORT_SCHEMAS=(SCOPED_GOLD_REPORT_SCHEMA,LONG_GOLD_REPORT_SCHEMA,GOLD_BACKTEST_REPORT_SCHEMA)
GOLD_REPORT_SCHEMAS=(GOLD_REPORT_SCHEMA,*SCOPED_GOLD_REPORT_SCHEMAS)
IDENTITY_FIELDS=frozenset('invocation_id native_trial_id search_id provenance_trial_id config_hash'.split())
MAX_ATTEMPTS=1000
MAX_POINTS=100000
MAX_MATRIX_CELLS=1000000
MAX_WORK=20000000
PROFILES={'RESEARCH_PRESCREEN_V1':(Decimal('.6'),Decimal('.5')),
          'ROBUST_RESEARCH_SIGNIFICANCE_V1':(Decimal('.95'),Decimal('.25'))}
METHODS=('NATIVE_PRETRADE_EQUITY_EXACT_GRID_V1','CANONICAL_FLAT_TERMINAL_CASH_EXACT_GRID_V1')
POLICY_FIELDS=frozenset('schema profile sample_method source_identity_sha256 market_scope dataset_manifest_sha256 study_scope '
    'trial_budget sample split_policy selection_policy dsr_policy cpcv_policy pbo_policy benchmark_policy '
    'oos_policy economic_checks elimination_policy termination_policy submission_enabled economic_authority'.split())
CHECKS=frozenset('dsr cpcv_positive cpcv_tail pbo net_return benchmark_excess drawdown'.split())


def require(condition, reason):
    if not condition:
        raise ValueError('RESEARCH_STATISTICS_'+reason)


def fields(value, names):
    require(type(value) is dict and set(value)==set(names.split() if type(names) is str else names),'FIELDS')


def integer(value, *, minimum=0, maximum=2**63-1):
    require(type(value) is int and minimum<=value<=maximum,'INTEGER')
    return value


def hashed(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None,'HASH')


def text(value):
    require(type(value) is str and 0<len(value)<=200 and value==value.strip() and '\0' not in value,'TEXT')


def number(value, *, minimum=None, maximum=None):
    require(type(value) is str and len(value)<=100 and re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',value) is not None,'DECIMAL')
    result=Decimal(value)
    require(result.is_finite() and canonical_decimal(result)==value,'DECIMAL')
    require((minimum is None or result>=minimum) and (maximum is None or result<=maximum),'RANGE')
    return result


def sample(value, *, budget=None):
    fields(value,'start_ms end_ms frequency_ms')
    start=integer(value['start_ms']);end=integer(value['end_ms'])
    step=integer(value['frequency_ms'],minimum=1,maximum=86400000)
    maximum = MAX_POINTS if budget is None else long_budget.validate(budget)['maximum_points']
    require(end>=start and (end-start)%step==0 and (end-start)//step+1<=maximum,'GRID')
    return (end-start)//step+1


def identity(value):
    fields(value,IDENTITY_FIELDS)
    for key in ('invocation_id','native_trial_id','config_hash'):hashed(value[key])
    text(value['search_id'])
    if value['provenance_trial_id'] is not None:text(value['provenance_trial_id'])
    return value


def embedded_benchmark(value):
    fields(value,'kind id invocation_id original_record_sha256 native_result_sha256')
    require(value['kind']=='ORIGINAL_EMBEDDED_CASH_ACCOUNT'
        and value['id']=='GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1','EMBEDDED_BENCHMARK')
    hashed(value['invocation_id'])
    for key in ('original_record_sha256','native_result_sha256'):
        if value[key] is not None:
            hashed(value[key])
    require((value['original_record_sha256'] is None)==(value['native_result_sha256'] is None),
        'EMBEDDED_BENCHMARK_MISSING')
    return value


def _validate_statistical_policy(value, *, schema=SCHEMA, methods=METHODS):
    canonical(value,maximum=65536)
    fields(value,POLICY_FIELDS)
    require(value['schema']==schema and value['submission_enabled'] is False
        and value['economic_authority'] is False,'SCHEMA_AUTHORITY')
    require(type(value['profile']) is str and value['profile'] in PROFILES,'PROFILE')
    require(type(value['sample_method']) is str and value['sample_method'] in methods,'SAMPLE_METHOD')
    hashed(value['source_identity_sha256']);hashed(value['dataset_manifest_sha256'])
    require(type(value['market_scope']) is list and len(value['market_scope'])==1,'MARKET_SCOPE')
    text(value['market_scope'][0])
    study=value['study_scope'];fields(study,'study_sha256 registered_at_ms')
    hashed(study['study_sha256']);integer(study['registered_at_ms'])
    fields(value['trial_budget'],'max_attempts')
    integer(value['trial_budget']['max_attempts'],minimum=1,maximum=MAX_ATTEMPTS)
    sample(value['sample'])
    split=value['split_policy'];fields(split,'scope maximum_information_horizon_ms embargo_ms')
    require(split['scope']=='TRAIN_VALIDATION_ONLY','SPLIT_SCOPE')
    horizon=integer(split['maximum_information_horizon_ms']);embargo=integer(split['embargo_ms'])
    require(embargo>=horizon>=value['sample']['frequency_ms'] and embargo%value['sample']['frequency_ms']==0,'INFORMATION_HORIZON')
    selection=value['selection_policy'];fields(selection,'method candidate_id definition_sha256 tie_break')
    require(selection['method']=='PREDECLARED_CANDIDATE' and selection['tie_break']=='ATTEMPT_ID_ASCENDING','SELECTION')
    text(selection['candidate_id']);hashed(selection['definition_sha256'])
    dsr=value['dsr_policy'];fields(dsr,'method trial_count moments variance minimum_probability')
    require(dsr['method']=='ORIGINAL_DSR_V1' and dsr['trial_count']=='ALL_CANDIDATE_ATTEMPTS'
        and dsr['moments'] in ('EMPIRICAL_POPULATION_CENTRAL_MOMENTS_V1','NORMAL_REFERENCE_SKEW_0_KURT_3')
        and dsr['variance']=='ORIGINAL_POPULATION_VARIANCE_FLOOR_V1','DSR_METHOD')
    number(dsr['minimum_probability'],minimum=PROFILES[value['profile']][0],maximum=1)
    cv=value['cpcv_policy'];fields(cv,'method n_groups k_test minimum_positive_fraction minimum_p05_sharpe')
    require(cv['method']=='ORIGINAL_CPCV_V1','CPCV_METHOD')
    n=integer(cv['n_groups'],minimum=2,maximum=16);k=integer(cv['k_test'],minimum=1,maximum=n-1)
    require(math.comb(n,k)<=5000,'CPCV_SPLITS')
    number(cv['minimum_positive_fraction'],minimum=Decimal('.5'),maximum=1)
    number(cv['minimum_p05_sharpe'],minimum=0)
    pbo=value['pbo_policy'];fields(pbo,'method block_count maximum_probability')
    require(pbo['method']=='ORIGINAL_CSCV_V1','PBO_METHOD')
    require(integer(pbo['block_count'],minimum=2,maximum=16)%2==0,'PBO_BLOCKS')
    number(pbo['maximum_probability'],minimum=0,maximum=PROFILES[value['profile']][1])
    benchmark=value['benchmark_policy'];fields(benchmark,'id definition_sha256 input_sha256 comparison')
    text(benchmark['id']);require(benchmark['id']!=selection['candidate_id'],'BENCHMARK_ROLE');hashed(benchmark['definition_sha256']);hashed(benchmark['input_sha256'])
    require(benchmark['comparison']=='SAME_NATIVE_SOURCE_DATASET_GRID','BENCHMARK_METHOD')
    fields(value['oos_policy'],'status')
    require(value['oos_policy']['status']=='NOT_EVALUATED_BY_THIS_STATISTICAL_REPORT','OOS_AUTHORITY')
    economic=value['economic_checks'];fields(economic,'minimum_net_return minimum_benchmark_excess maximum_drawdown minimum_observations minimum_duration_ms')
    number(economic['minimum_net_return'],minimum=0);number(economic['minimum_benchmark_excess'],minimum=0)
    number(economic['maximum_drawdown'],minimum=0,maximum=1)
    integer(economic['minimum_observations'],minimum=30,maximum=MAX_POINTS-1)
    integer(economic['minimum_duration_ms'],minimum=30*86400000)
    require(value['elimination_policy']=='RETAIN_ALL_ATTEMPTS_NO_FABRICATED_RETURNS'
        and value['termination_policy']=='MISSING_INSUFFICIENT_THRESHOLD_FAIL','FAILURE_POLICY')
    return copy.deepcopy(value)


def validate_statistical_policy(value):
    return _validate_statistical_policy(value)


def validate_cohort(value):
    canonical(value,maximum=1000000)
    fields(value,'schema study_sha256 policy_sha256 project_cut_sha256 frozen_at_ms attempts selected_attempt benchmark_attempt')
    require(value['schema']==COHORT_SCHEMA,'COHORT_SCHEMA')
    for key in ('study_sha256','policy_sha256','project_cut_sha256'):hashed(value[key])
    integer(value['frozen_at_ms'])
    attempts=value['attempts']
    require(type(attempts) is list and len(attempts)<=MAX_ATTEMPTS,'ATTEMPTS')
    for row in attempts:identity(row)
    keys=[(row['invocation_id'],row['native_trial_id']) for row in attempts]
    require(keys==sorted(set(keys)),'ATTEMPT_ORDER')
    selected=identity(value['selected_attempt']);benchmark=identity(value['benchmark_attempt'])
    require(selected in attempts and benchmark in attempts and selected!=benchmark,'COHORT_SELECTION')
    return copy.deepcopy(value)


def finite(value, *, minimum=None, maximum=None):
    require(type(value) in (float,int) and math.isfinite(value)
        and (minimum is None or value>=minimum) and (maximum is None or value<=maximum),'FINITE_NUMBER')


def _report_body(value):
    scoped=value['schema'] in SCOPED_GOLD_REPORT_SCHEMAS
    point_bound=long_budget.MAX_POINTS if value['schema']==LONG_GOLD_REPORT_SCHEMA else MAX_POINTS
    population=value['population']
    fields(population,'attempt_count unique_definition_count selection_attempt_count mapped_attempt_count '
        'registered_attempt_count unmapped_attempt_count unfinished_attempt_count failed_attempt_count')
    for count in population.values():integer(count,maximum=MAX_ATTEMPTS)
    require(population['mapped_attempt_count']+population['unmapped_attempt_count']==population['attempt_count']
        and population['selection_attempt_count']<=population['attempt_count'],'POPULATION_COUNTS')
    rows=value['attempts']
    require(type(rows) is list and len(rows)==population['mapped_attempt_count'],'REPORT_ATTEMPTS')
    keys=[]
    for row in rows:
        fields(row,'identity native_status series_status series_sha256 policy_bound')
        identity(row['identity']);keys.append((row['identity']['invocation_id'],row['identity']['native_trial_id']))
        require(row['native_status'] in ('PLANNED','RUNNING','PASSED','FAILED','REGISTERED_NOT_STARTED',
            'PROVENANCE_NOT_PERSISTED','NOT_REGISTERED'),'NATIVE_STATUS')
        statuses=('READY','INSUFFICIENT','OUTSIDE_COMPARISON_SCOPE') if scoped else ('READY','INSUFFICIENT')
        require(row['series_status'] in statuses and type(row['policy_bound']) is bool,'SERIES_STATUS')
        hashed(row['series_sha256'])
    require(keys==sorted(set(keys)),'REPORT_ATTEMPT_ORDER')
    if value['schema'] in GOLD_REPORT_SCHEMAS:
        require(value['selected_attempt'] in [r['identity'] for r in rows]
            or (value['selected_attempt'] is None and 'NO_PREDECLARED_SELECTION' in value['reasons']),
            'REPORT_SELECTION')
        embedded_benchmark(value['benchmark_attempt'])
        require(population['selection_attempt_count']==population['attempt_count'],'GOLD_TRIAL_COUNT')
        if scoped:
            from quantos.contracts.gold_project_statistics import validate_comparison
            comparison=validate_comparison(value['comparison'],[r['identity'] for r in rows],value['policy_sha256'])
            included=comparison['included_attempts']
            require(value['selected_attempt'] is None or value['selected_attempt'] in included,'GOLD_COMPARISON_SELECTION')
            for row in rows:
                inside=row['identity'] in included
                require((row['series_status']=='OUTSIDE_COMPARISON_SCOPE') == (not inside)
                    and (inside or row['policy_bound'] is False),'GOLD_COMPARISON_SERIES')
            if comparison['excluded_attempts']:
                require(value['checks']['dsr']=='INSUFFICIENT'
                    and value['metrics']['dsr'] is value['metrics']['var_sr'] is None
                    and 'GLOBAL_DSR_VARIANCE_NOT_COMPARABLE' in value['reasons'],'GOLD_GLOBAL_DSR_UNKNOWN')
    else:
        require(value['selected_attempt'] in [r['identity'] for r in rows]
            and value['benchmark_attempt'] in [r['identity'] for r in rows],'REPORT_SELECTION')
    metrics=value['metrics']
    fields(metrics,'dsr observed_sr var_sr n_trials cpcv cpcv_thresholds pbo matrix_sha256 calendar_sha256 skew kurtosis '
        'sample_net_return benchmark_net_return benchmark_excess maximum_drawdown selected_cash')
    integer(metrics['n_trials'],maximum=MAX_ATTEMPTS)
    require(metrics['n_trials']==population['selection_attempt_count'],'TRIAL_COUNT')
    for k in ('dsr','pbo'):
        if metrics[k] is not None:finite(metrics[k],minimum=0,maximum=1)
    for k in ('observed_sr','var_sr','skew','kurtosis'):
        if metrics[k] is not None:finite(metrics[k])
    for k in ('matrix_sha256','calendar_sha256'):
        if metrics[k] is not None:hashed(metrics[k])
    for k in ('sample_net_return','benchmark_net_return','benchmark_excess','maximum_drawdown'):
        if metrics[k] is not None:number(metrics[k])
    if metrics['cpcv_thresholds'] is not None:
        fields(metrics['cpcv_thresholds'],'frac_positive p05_oos_sharpe')
        finite(metrics['cpcv_thresholds']['frac_positive'],minimum=0,maximum=1)
        finite(metrics['cpcv_thresholds']['p05_oos_sharpe'])
    cv=metrics['cpcv']
    if cv is not None:
        fields(cv,'n_splits frac_positive selected_counts test_boundaries mean_oos_sharpe '
            'median_oos_sharpe std_oos_sharpe p05_oos_sharpe p95_oos_sharpe min_oos_sharpe')
        integer(cv['n_splits'],minimum=1,maximum=5000);finite(cv['frac_positive'],minimum=0,maximum=1)
        for k in ('mean_oos_sharpe','median_oos_sharpe','std_oos_sharpe','p05_oos_sharpe','p95_oos_sharpe','min_oos_sharpe'):finite(cv[k])
        require(type(cv['selected_counts']) is dict,'SELECTED_COUNTS')
        for k,v in cv['selected_counts'].items():hashed(k);integer(v,minimum=1,maximum=5000)
        require(sum(cv['selected_counts'].values())==cv['n_splits'],'SPLIT_COUNT')
        require(type(cv['test_boundaries']) is list and len(cv['test_boundaries'])==cv['n_splits'],'SPLIT_BOUNDARIES')
        for boundaries in cv['test_boundaries']:
            require(type(boundaries) is list and 1<=len(boundaries)<=16,'SPLIT_BOUNDARIES')
            for pair in boundaries:
                require(type(pair) is list and len(pair)==2,'SPLIT_BOUNDARY')
                integer(pair[0],maximum=point_bound)
                integer(pair[1],minimum=pair[0]+1,maximum=point_bound)
    cash=metrics['selected_cash']
    if cash is not None:
        fields(cash,'full_run positions_flat sample_terminal_equity_matches post_sample_execution_count '
            'closed_at_sample_end sample_is_full_event_history')
        for k in ('positions_flat','sample_terminal_equity_matches','closed_at_sample_end','sample_is_full_event_history'):
            require(type(cash[k]) is bool,'CASH_FLAG')
        integer(cash['post_sample_execution_count'],maximum=point_bound)
        fields(cash['full_run'],'initial_equity ending_equity net_equity_change fees funding reconciliation_residual '
            'max_observed_drawdown frequency_ms sample_count annualized_sharpe statistics_status')
        for k in ('initial_equity','ending_equity','net_equity_change','fees','funding','reconciliation_residual'):
            number(cash['full_run'][k])
        for k in ('max_observed_drawdown','annualized_sharpe'):
            if cash['full_run'][k] is not None:number(cash['full_run'][k])
        integer(cash['full_run']['frequency_ms'],minimum=1);integer(cash['full_run']['sample_count'])
        text(cash['full_run']['statistics_status'])


def validate_report(value):
    canonical(value,maximum=16000000)
    fields(value,'schema profile policy_sha256 cohort_sha256 project_cut_sha256 study_sha256 as_of_ms statistical_decision '
        'reasons population attempts selected_attempt benchmark_attempt series_sha256 metrics checks limitations '
        'qualification independent_oos_verified promotion_approved submission_enabled economic_authority'
        + (' comparison' if value.get('schema') in SCOPED_GOLD_REPORT_SCHEMAS else '')
        + (' computation_budget' if value.get('schema')==LONG_GOLD_REPORT_SCHEMA else ''))
    require(value['schema'] in (REPORT_SCHEMA,*GOLD_REPORT_SCHEMAS)
        and value['statistical_decision'] in ('PASS','FAIL','INSUFFICIENT'),'REPORT_STATUS')
    if value['schema']==LONG_GOLD_REPORT_SCHEMA:
        long_budget.validate(value['computation_budget'])
    for key in ('policy_sha256','cohort_sha256','project_cut_sha256','study_sha256','series_sha256'):hashed(value[key])
    require(type(value['profile']) is str and value['profile'] in PROFILES,'PROFILE')
    integer(value['as_of_ms'])
    if value['selected_attempt'] is not None:
        identity(value['selected_attempt'])
    else:
        require(value['schema'] in GOLD_REPORT_SCHEMAS,'REPORT_SELECTION')
    if value['schema'] in GOLD_REPORT_SCHEMAS:
        embedded_benchmark(value['benchmark_attempt'])
    else:
        identity(value['benchmark_attempt'])
    require(value['qualification']=='NOT_ASSESSED' and all(value[k] is False for k in
        ('independent_oos_verified','promotion_approved','submission_enabled','economic_authority')),'REPORT_AUTHORITY')
    for key in ('reasons','limitations'):
        require(type(value[key]) is list and len(value[key])<=MAX_ATTEMPTS and value[key]==sorted(set(value[key])),'REASONS')
        for item in value[key]:text(item)
    _report_body(value)
    fields(value['checks'],CHECKS)
    require(all(v in ('PASS','FAIL','INSUFFICIENT') for v in value['checks'].values()),'CHECK_STATUS')
    if value['schema'] in GOLD_REPORT_SCHEMAS:
        for check,metric in (('dsr','dsr'),('pbo','pbo'),('net_return','sample_net_return'),
                ('benchmark_excess','benchmark_excess'),('drawdown','maximum_drawdown'),
                ('cpcv_positive','cpcv_thresholds'),('cpcv_tail','cpcv_thresholds')):
            require(value['checks'][check]=='INSUFFICIENT' or value['metrics'][metric] is not None,
                'GOLD_CHECK_WITHOUT_METRIC')
    expected='FAIL' if 'FAIL' in value['checks'].values() else ('INSUFFICIENT' if value['reasons']
        or 'INSUFFICIENT' in value['checks'].values() else 'PASS')
    require(value['statistical_decision']==expected,'REPORT_DECISION')
    if expected=='PASS':
        require(value['metrics']['selected_cash'] is not None
            and value['metrics']['selected_cash']['closed_at_sample_end'] is True
            and value['population']['selection_attempt_count']>=2
            and all(row['series_status']=='READY' and row['policy_bound'] is True for row in value['attempts'])
            and value['population']['unmapped_attempt_count']==value['population']['failed_attempt_count']
                ==value['population']['unfinished_attempt_count']==0,'COMPLETE_PASS')
    if value['profile']=='RESEARCH_PRESCREEN_V1':
        require('PRESCREEN_PASS_IS_NOT_RESEARCH_SIGNIFICANCE' in value['limitations'],'PROFILE_LIMITATION')
    return copy.deepcopy(value)


__all__=['validate_statistical_policy','validate_cohort','validate_report']
