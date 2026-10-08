"""Replay statistics from actual native project originals, without execution imports.

The result judges its declared sample, not independent OOS, risk ownership,
source custody, closed-position profitability, or candidate/order permission.
"""
import copy
import math
import statistics
from decimal import Decimal

from quantos.contracts import research_statistical_review as contract
from quantos.contracts.backtest_identity import decode_identity_payload, exact_identity_hash
from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.registered_technical_input import project_manifest_inputs, _decode
from quantos.contracts.registered_research_run import SOURCE_REANALYSIS_SCHEMAS
from quantos.contracts.research_statistics import fixed_frequency_equity
from services.validation import project_research_evidence_authority as originals
from services.validation import research_result_projection as projection
from services.validation.project_research_population import validate_project_cut
from services.validation.project_research_evidence_artifact import last_project_fact_ms
from services.validation.catalog_evidence import _observed_sr, _VAR_SR_FLOOR
from services.validation.deflated_sharpe import deflated_sharpe_ratio, pbo_cscv
from services.validation.cpcv import cpcv_summary, cpcv_oos_distribution, _quantile

require=contract.require
digest=contract.digest
canonical=contract.canonical


def _identity(row):
    return {key:row[key] for key in contract.IDENTITY_FIELDS}


def _same(left,right,reason):
    require(canonical(left)==canonical(right),reason)


def _selected(cut,attempt):
    contract.identity(attempt)
    rows=[row for row in cut['attempts'] if _identity(row)==attempt]
    require(len(rows)==1,'ATTEMPT_BINDING')
    run=cut['runs'][attempt['invocation_id']]
    trial=next(t for t in run['plan']['trials'] if t['native_trial_id']==attempt['native_trial_id'])
    return rows[0],run,trial


@ledger_decimal_context()
def _series(cut,attempt,sample,sample_method):
    contract.sample(sample)
    require(type(sample_method) is str and sample_method in contract.METHODS,'SAMPLE_METHOD')
    row,run,trial=_selected(cut,attempt)
    result=dict(schema='QUANTOS_NATIVE_STATISTICAL_SERIES_V1',attempt=copy.deepcopy(attempt),
        status='INSUFFICIENT',reasons=[],method=None,source_identity_sha256=digest(run['plan']['source_identity']),
        dataset_manifest_sha256=None,market=None,original_record_sha256=None,native_result_sha256=None,
        original_manifest_sha256=None,input_sha256=run['plan']['input_sha256'],published_at_ms=None,
        sample=copy.deepcopy(sample),points=[],statistics_status=None,cash=None,market_input_sha256=None)
    if row['status']!='PASSED' or run['partial']:
        result['reasons']=['NATIVE_ATTEMPT_NOT_COMPLETE'];return result
    report=originals._original(run,'archive/report.json')
    manifest=originals._original(run,'archive/manifest.json')
    document=originals._original(run,'input.json')
    result['original_record_sha256']=run['originals']['archive/report.json']['sha256']
    result['original_manifest_sha256']=run['originals']['archive/manifest.json']['sha256']
    result['published_at_ms']=originals._original(run,'outcome.json')['completed_at_ms']
    if run['plan']['schema'] in SOURCE_REANALYSIS_SCHEMAS:
        result.update(market='XAUUSDT',
            dataset_manifest_sha256=document['source_binding']['manifest_sha256'])
        result['reasons']=['GOLD_BACKTEST_STATISTICAL_ADAPTER_UNAVAILABLE']
        return result
    if run['plan']['adapter']!='TECHNICAL_LINEAR_PERPETUAL_V2':
        # Original forward report hashes remain verifiable but are not silently
        # recast as historical backtest/OOS observations.
        native_hash, _, _, _, _ = originals._native_projection(run, trial, dict(market='XAUUSDT', evidence_kind='FORWARD'))
        result.update(native_result_sha256=native_hash,method='NATIVE_FORWARD_EQUITY_V1',market='XAUUSDT')
        result['reasons']=['FORWARD_NOT_BACKTEST'];return result
    transported=project_manifest_inputs(document,manifest=manifest)
    material={k:decode_identity_payload(v) for k,v in transported['values'].items()}
    universe=material['universe']['value']
    require(type(universe) in (tuple,list) and len(universe)==1,'NATIVE_MARKET')
    market=universe[0]
    docs=dict(record=report,manifest=manifest,input=transported,source_identity=run['plan']['source_identity'],
        dataset_manifest=material['data_identity']['dataset_identity'],
        candidate_definition=trial['definition_preimage']['candidate_definition'],trial_history=run['ledger']['history'])
    simulation,selected,native_hash,_,claims=projection._technical(dict(candidate_id=trial['candidate_id'],
        market=market,native_run_id=report['run_id']),docs)
    require(selected==attempt['native_trial_id'],'NATIVE_TRIAL')
    metrics,_,_=projection._native_metrics(simulation,sample,claims,'BACKTEST')
    result.update(method=sample_method,market=market,native_result_sha256=native_hash,
        dataset_manifest_sha256=digest(docs['dataset_manifest']),
        market_input_sha256=exact_identity_hash(material['data_identity']['market_events']))
    descriptions=_decode(document['arguments'])['decisions']
    contexts=simulation['decision_contexts']
    require(len(descriptions)==len(contexts),'DECISION_COUNT')
    by_time={};execution_times={}
    for description,context in zip(descriptions,contexts,strict=True):
        require(description.type_name=='services.execution.authoritative_perpetual_backtest.DecisionRequest','DECISION_TYPE')
        value=description.fields;timestamp=contract.integer(context['decision_time_ms'])
        require(contract.integer(value['timestamp'])==timestamp and value['decision_id']==context['decision_id']
            and timestamp not in by_time,'DECISION_BINDING')
        for signal in value['signal_inputs']:
            fields=signal.fields
            times=[contract.integer(fields[key]) for key in ('event_time','available_time','ingestion_time')]
            require(times[0]<=times[1]<=times[2]<=timestamp,'SIGNAL_AVAILABILITY')
        execution=value['execution'].fields
        require(execution['timestamp']>timestamp and execution['execution_id']==context['execution_id'],'EXECUTION_AVAILABILITY')
        require(execution['execution_id'] not in execution_times,'EXECUTION_DUPLICATE')
        execution_times[execution['execution_id']]=contract.integer(execution['timestamp'])
        by_time[timestamp]=context
    require(set(execution_times)=={v['execution_id'] for v in simulation['execution_results']}
        and len(execution_times)==len(simulation['execution_results']),'EXECUTION_RESULT_BINDING')
    expected=list(range(sample['start_ms'],sample['end_ms']+1,sample['frequency_ms']))
    terminal=sample_method=='CANONICAL_FLAT_TERMINAL_CASH_EXACT_GRID_V1'
    wanted=expected[:-1] if terminal else expected
    if any(t not in by_time for t in wanted):
        result['reasons']=['EXACT_GRID_OBSERVATION_MISSING'];return result
    ending=Decimal(metrics['ending_equity']);state=simulation['ending_state']
    event_times=[contract.integer(v['timestamp']) for v in simulation['ledger_events']]
    data_events=material['data_identity']['market_events']
    availability=[contract.integer(e[k]) for e in data_events for k in ('available_time','ingestion_time')]
    native_times=[contract.integer(e['event']['timestamp']) for e in data_events]
    covered=bool(event_times and native_times) and max(event_times)==sample['end_ms']         and max(native_times)==sample['end_ms'] and max(availability)<=sample['end_ms']         and max(execution_times.values(),default=0)<=sample['end_ms']         and max(by_time,default=0)<=sample['end_ms']
    # Read only canonical ending cash; never derive equity from price/positions.
    flat=not state['positions'] and not state['pending_initial_margin_execution_prices']
    cash_matches=ending==Decimal(state['wallet_balance'])==Decimal(state['account_equity'])
    history=[dict(timestamp=t,sequence=i,equity=by_time[t]['pretrade_equity']) for i,t in enumerate(wanted)]
    if terminal:
        opening_exact=Decimal(history[0]['equity'])==Decimal(metrics['initial_equity'])             and not by_time[sample['start_ms']]['pretrade_positions']             and min(execution_times.values(),default=sample['start_ms'])>sample['start_ms']
        if not (covered and flat and cash_matches and opening_exact):
            result['reasons']=['CANONICAL_FLAT_TERMINAL_NOT_COVERED'];return result
        require(sample['end_ms'] not in by_time,'TERMINAL_OVERLAPS_PRETRADE')
        history.append(dict(timestamp=sample['end_ms'],sequence=len(history),equity=state['wallet_balance']))
    grid=fixed_frequency_equity(history,**sample)
    result['points']=[dict(row,available_at_ms=row['timestamp']) for row in grid['points']]
    result['statistics_status']=grid['statistics_status']
    last=Decimal(history[-1]['equity'])
    result['cash']=dict(full_run=metrics,positions_flat=flat,
        sample_terminal_equity_matches=last==ending,
        post_sample_execution_count=sum(t>sample['end_ms'] for t in execution_times.values()),
        closed_at_sample_end=terminal and covered and flat and cash_matches,
        sample_is_full_event_history=False)
    if grid['statistics_status'] in ('INSUFFICIENT_DURATION','NONPOSITIVE_EQUITY'):
        result['reasons']=[grid['statistics_status']];return result
    result['status']='READY'
    return result


def project_native_series(*,project_cut,attempt,sample,sample_method):
    """Read only retained originals; supplied curves are never accepted."""
    cut=validate_project_cut(project_cut)
    return _series(cut,attempt,sample,sample_method)


def _registration(run,policy,policy_hash):
    core=run['plan'].get('evaluation_core')
    if core is None:return False
    # Native validate_plan and native ledger already bind the exact core.
    if core['selection']['validation_policy_sha256']!=policy_hash:return False
    locked = core['splits']['locked_oos']
    expected_sample = core['sample']
    if locked is not None:
        # Statistical methods consume one continuous grid. Never include OOS or
        # undeclared gaps when binding the preregistered train/validation sample.
        windows = [core['splits'][name] for name in ('train', 'validation')
                   if core['splits'][name] is not None]
        require(bool(windows), 'PREDECLARED_STATISTICAL_WINDOW_MISSING')
        frequency = core['sample']['frequency_ms']
        require(all(left['end_ms'] + frequency == right['start_ms']
                    for left, right in zip(windows[:-1], windows[1:], strict=True)),
                'PREDECLARED_STATISTICAL_CONTIGUITY')
        expected_sample = dict(start_ms=windows[0]['start_ms'], end_ms=windows[-1]['end_ms'],
                               frequency_ms=frequency)
    _same(expected_sample,policy['sample'],'PREDECLARED_SAMPLE')
    require(core['registered_at_ms']>=policy['study_scope']['registered_at_ms'],'POLICY_REGISTRATION')
    benchmark=policy['benchmark_policy']
    _same(core['benchmark'],{k:benchmark[k] for k in ('id','definition_sha256','input_sha256')},'PREDECLARED_BENCHMARK')
    require(core['source_identity_sha256']==policy['source_identity_sha256']
        and core['dataset_manifest_sha256']==policy['dataset_manifest_sha256'],'PREDECLARED_DATA')
    if locked is not None:
        require(policy['sample']['end_ms']<locked['start_ms'],'LOCKED_OOS_NOT_STATISTICAL_TUNING')
    return True




def _cpcv_thresholds(sharpes):
    # Keep original display summary rounding, but never use it to erase a
    # negative tail or to cross a narrowly preregistered threshold.
    return dict(frac_positive=sum(v>0 for v in sharpes)/len(sharpes),
        p05_oos_sharpe=_quantile(sorted(sharpes),0.05))


def _work_budget(attempts,count,groups,blocks, *, budget=None):
    splits=math.comb(groups['n_groups'],groups['k_test'])
    work=attempts*count*splits+attempts*blocks*math.comb(blocks,blocks//2)
    maximum_cells,maximum_work=contract.MAX_MATRIX_CELLS,contract.MAX_WORK
    if budget is not None:
        from quantos.contracts.gold_long_statistics_budget import validate
        capacity=validate(budget)
        contract.integer(count,minimum=1,maximum=capacity['maximum_points'])
        maximum_cells,maximum_work=capacity['maximum_matrix_cells'],capacity['maximum_work']
    require(attempts*count<=maximum_cells and work<=maximum_work,'WORK_BUDGET')
    return splits


def _population_moments(values):
    mean=math.fsum(values)/len(values);centered=[v-mean for v in values]
    m2=math.fsum(v*v for v in centered)/len(values)
    if not math.isfinite(m2) or m2<=0 or m2*m2==0:return None
    try:
        skew=(math.fsum(v**3 for v in centered)/len(values))/m2**1.5
        kurt=(math.fsum(v**4 for v in centered)/len(values))/m2**2
    except (OverflowError,ZeroDivisionError):return None
    return (skew,kurt) if all(math.isfinite(v) for v in (skew,kurt)) else None

@ledger_decimal_context()
def review_native_methods(vectors, selected_id, policy, selection_count):
    """The original DSR/CPCV/CSCV calculations, shared by native adapters."""
    require(type(vectors) is dict and len(vectors) >= 2, "METHOD_CANDIDATES")
    require(selected_id is None or selected_id in vectors, "METHOD_SELECTION")
    lengths = {len(v) for v in vectors.values()}
    require(len(lengths) == 1 and next(iter(lengths)) >= 2, "METHOD_SAMPLE")
    require(all(math.isfinite(v) for seq in vectors.values() for v in seq), "NONFINITE_RETURN")
    contract.integer(selection_count, minimum=len(vectors), maximum=contract.MAX_ATTEMPTS)
    groups = policy["cpcv_policy"]
    ready = list(vectors)
    splits = _work_budget(len(ready), next(iter(lengths)) + 1, groups, policy["pbo_policy"]["block_count"],
                          budget=policy.get('computation_budget'))
    reasons = set()
    metrics = dict(
        dsr=None,
        observed_sr=None,
        var_sr=None,
        n_trials=selection_count,
        cpcv=None,
        cpcv_thresholds=None,
        pbo=None,
        matrix_sha256=digest(vectors),
        calendar_sha256=None,
        skew=None,
        kurtosis=None,
    )
    checks = {name: "INSUFFICIENT" for name in ("dsr", "cpcv_positive", "cpcv_tail", "pbo")}

    def test(name, value, threshold, greater=True):
        checks[name] = "PASS" if (value >= threshold if greater else value <= threshold) else "FAIL"

    observed = {key: _observed_sr(seq) for key, seq in vectors.items()}
    if selected_id is None:
        reasons.add("NO_PREDECLARED_SELECTION")
    elif any(v is None for v in observed.values()):
        reasons.add("ZERO_VARIANCE_CANDIDATE")
    else:
        variance = max(statistics.pvariance(observed.values()), _VAR_SR_FLOOR)
        values = vectors[selected_id]
        moments = _population_moments(values)
        metrics.update(observed_sr=observed[selected_id], var_sr=variance)
        if moments is None:
            reasons.add("DSR_MOMENTS_UNRESOLVED")
        else:
            skew, kurt = moments
            metrics.update(skew=skew, kurtosis=kurt)
            if policy["dsr_policy"]["moments"] == "NORMAL_REFERENCE_SKEW_0_KURT_3":
                skew, kurt = 0.0, 3.0
            try:
                dsr = deflated_sharpe_ratio(
                    observed[selected_id], len(values), selection_count, variance, skew=skew, kurt=kurt
                )
            except ValueError:
                reasons.add("DSR_MOMENTS_UNSUPPORTED")
            else:
                metrics["dsr"] = dsr
                test("dsr", dsr, float(policy["dsr_policy"]["minimum_probability"]))
    embargo = policy["split_policy"]["embargo_ms"] // policy["sample"]["frequency_ms"]
    distribution = cpcv_oos_distribution(vectors, n_groups=groups["n_groups"], k_test=groups["k_test"], embargo=embargo)
    if distribution is None or len(distribution[0]) != splits or any(not math.isfinite(v) for v in distribution[0]):
        reasons.add("CPCV_INCOMPLETE_OR_NONFINITE")
    else:
        cv = cpcv_summary(vectors, n_groups=groups["n_groups"], k_test=groups["k_test"], embargo=embargo)
        metrics["cpcv"] = cv
        raw_cv = _cpcv_thresholds(distribution[0])
        metrics["cpcv_thresholds"] = raw_cv
        test("cpcv_positive", raw_cv["frac_positive"], float(groups["minimum_positive_fraction"]))
        test("cpcv_tail", raw_cv["p05_oos_sharpe"], float(groups["minimum_p05_sharpe"]))
    blocks = policy["pbo_policy"]["block_count"]
    length = len(next(iter(vectors.values())))
    if length < blocks * 2:
        reasons.add("PBO_BLOCKS_INSUFFICIENT")
    else:
        matrix = [
            [math.fsum(vectors[key][length * b // blocks : length * (b + 1) // blocks]) for key in ready]
            for b in range(blocks)
        ]
        pbo = pbo_cscv(matrix)
        metrics["pbo"] = pbo
        test("pbo", pbo, float(policy["pbo_policy"]["maximum_probability"]), False)
    return metrics, checks, reasons


@ledger_decimal_context()
def review_native_economics(points, baseline_points, policy):
    """Compare original grid equity with the original same-grid benchmark."""
    reasons = set()
    metrics = {}
    checks = {}

    def test(name, value, threshold, greater=True):
        checks[name] = "PASS" if (value >= threshold if greater else value <= threshold) else "FAIL"

    equity = [Decimal(p["equity"]) for p in points]
    if (
        len(points) - 1 < policy["minimum_observations"]
        or points[-1]["timestamp"] - points[0]["timestamp"] < policy["minimum_duration_ms"]
    ):
        reasons.add("SAMPLE_TOO_SHORT")
    net = equity[-1] / equity[0] - 1
    peak, drawdown = equity[0], Decimal(0)
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak-value)/peak)
    metrics.update(sample_net_return=canonical_decimal(net), maximum_drawdown=canonical_decimal(drawdown))
    test("net_return", net, Decimal(policy["minimum_net_return"]))
    test("drawdown", drawdown, Decimal(policy["maximum_drawdown"]), False)
    if baseline_points is not None:
        require([p["timestamp"] for p in baseline_points] == [p["timestamp"] for p in points], "BENCHMARK_CALENDAR")
        base = Decimal(baseline_points[-1]["equity"]) / Decimal(baseline_points[0]["equity"]) - 1
        metrics.update(benchmark_net_return=canonical_decimal(base), benchmark_excess=canonical_decimal(net - base))
        test("benchmark_excess", net - base, Decimal(policy["minimum_benchmark_excess"]))
    return metrics, checks, reasons

@ledger_decimal_context()
def review_project_statistics(*,project_cut,policy,cohort,benchmark,as_of_ms):
    policy=contract.validate_statistical_policy(policy);cohort=contract.validate_cohort(cohort)
    cut=validate_project_cut(project_cut);contract.integer(as_of_ms)
    require(cohort['policy_sha256']==digest(policy) and cohort['study_sha256']==policy['study_scope']['study_sha256']
        and cohort['project_cut_sha256']==cut['cut_sha256'],'COHORT_BINDING')
    rows=sorted((_identity(row) for row in cut['attempts']),key=lambda r:(r['invocation_id'],r['native_trial_id']))
    _same(rows,cohort['attempts'],'COMPLETE_ATTEMPT_SET')
    require(policy['study_scope']['registered_at_ms']<=cohort['frozen_at_ms']<=as_of_ms,'REVIEW_TIME')
    require(last_project_fact_ms(cut)<=cohort['frozen_at_ms'],'COHORT_BEFORE_PROJECT_FACT')
    require(cut['attempt_count']<=policy['trial_budget']['max_attempts'],'ATTEMPT_BUDGET')
    count=contract.sample(policy['sample'])
    groups=policy['cpcv_policy']
    _work_budget(len(rows),count,groups,policy['pbo_policy']['block_count'])
    selected_row,selected_run,selected_trial=_selected(cut,cohort['selected_attempt'])
    require(selected_trial['candidate_id']==policy['selection_policy']['candidate_id']
        and selected_trial['config_hash']==policy['selection_policy']['definition_sha256'],'PREDECLARED_SELECTION')
    matching=[r for r in rows if r['config_hash']==selected_trial['config_hash']
        and _selected(cut,r)[2]['candidate_id']==selected_trial['candidate_id']]
    require(cohort['selected_attempt']==matching[0],'PREDECLARED_ATTEMPT_TIE')
    reasons=set();limitations={'NO_INDEPENDENT_OOS_PROOF','NO_RESEARCH_OR_EXECUTION_AUTHORITY',
        'INFORMATION_HORIZON_ABOVE_RETURN_SPAN_IS_DECLARED',
        'FIXED_GRID_IS_NOT_INTRAPERIOD_RISK_EVIDENCE'}
    if policy['profile']=='RESEARCH_PRESCREEN_V1':limitations.add('PRESCREEN_PASS_IS_NOT_RESEARCH_SIGNIFICANCE')
    if policy['dsr_policy']['moments']=='NORMAL_REFERENCE_SKEW_0_KURT_3':
        reasons.add('EMPIRICAL_MOMENTS_REQUIRED');limitations.add('NORMAL_REFERENCE_MOMENTS_DIAGNOSTIC_ONLY')
    if cut['population_coverage']!='COMPLETE':reasons.add('PROJECT_POPULATION_UNMAPPED')
    if not cut['all_runs_finalized']:reasons.add('PROJECT_RUNS_UNFINISHED')
    attempts=[];series={};bound={}
    for row in rows:
        status,run,_=_selected(cut,row)
        bound[digest(row)]=_registration(run,policy,digest(policy))
        if not bound[digest(row)]:reasons.add('STATISTICAL_POLICY_NOT_PREREGISTERED')
        for events in run['ledger']['history'].values():
            require(all(e['at']<=cohort['frozen_at_ms'] for e in events),'COHORT_BEFORE_NATIVE_FACT')
        value=_series(cut,row,policy['sample'],policy['sample_method']);series[digest(row)]=value
        if value['published_at_ms'] is not None:require(value['published_at_ms']<=cohort['frozen_at_ms'],'COHORT_BEFORE_PUBLICATION')
        if value['status']=='READY':
            if not value['cash']['closed_at_sample_end']:reasons.add('FULL_RUN_TERMINAL_NOT_COVERED')
            require(value['source_identity_sha256']==policy['source_identity_sha256']
                and value['dataset_manifest_sha256']==policy['dataset_manifest_sha256']
                and value['market'] in policy['market_scope'],'SERIES_DATA_BINDING')
        else:reasons.update(value['reasons'])
        attempts.append(dict(identity=copy.deepcopy(row),native_status=status['status'],series_status=value['status'],
            series_sha256=digest(value),policy_bound=bound[digest(row)]))
    benchmark_id=digest(cohort['benchmark_attempt']);baseline=series[benchmark_id]
    _,baseline_run,baseline_trial=_selected(cut,cohort['benchmark_attempt'])
    declaration=policy['benchmark_policy']
    require(baseline_trial['candidate_id']==declaration['id'] and baseline_trial['config_hash']==declaration['definition_sha256']
        and baseline_run['plan']['input_sha256']==declaration['input_sha256'],'BENCHMARK_BINDING')
    if benchmark is None:reasons.add('BENCHMARK_MISSING')
    else:_same(benchmark,baseline,'BENCHMARK_PROJECTION_CHANGED')
    benchmark_bound=all(bound.values())
    candidate_ids=[digest(row) for row in rows if not (benchmark_bound and row==cohort['benchmark_attempt'])]
    selected_id=digest(cohort['selected_attempt']);selected=series[selected_id]
    selection_count=cut['attempt_count']-(1 if benchmark_bound else 0)
    checks={name:'INSUFFICIENT' for name in contract.CHECKS}
    metrics=dict(dsr=None,observed_sr=None,var_sr=None,n_trials=selection_count,cpcv=None,cpcv_thresholds=None,pbo=None,
        matrix_sha256=None,calendar_sha256=None,skew=None,kurtosis=None,sample_net_return=None,benchmark_net_return=None,
        benchmark_excess=None,maximum_drawdown=None,selected_cash=selected['cash'])
    ready=[key for key in candidate_ids if series[key]['status']=='READY']
    if len(ready)<2:reasons.add('TWO_REAL_CANDIDATE_SERIES_REQUIRED')
    def test(name,value,threshold,greater=True):
        checks[name]='PASS' if (value>=threshold if greater else value<=threshold) else 'FAIL'
    complete=len(ready)==len(candidate_ids) and len(ready)>=2 and cut['population_coverage']=='COMPLETE'
    if complete:
        calendars=[[p['timestamp'] for p in series[key]['points']] for key in ready]
        require(all(c==calendars[0] for c in calendars),'CALENDAR_MISMATCH')
        require(all(series[key]['market_input_sha256']==selected['market_input_sha256'] for key in ready),'MARKET_INPUT_CHANGED')
        vectors={key:[float(Decimal(p['net_return'])) for p in series[key]['points'][1:]] for key in ready}
        require(all(math.isfinite(v) for seq in vectors.values() for v in seq),'NONFINITE_RETURN')
        metrics['matrix_sha256']=digest(vectors);metrics['calendar_sha256']=digest(calendars[0])
        method_metrics,method_checks,method_reasons=review_native_methods(vectors,selected_id,policy,selection_count)
        method_metrics['calendar_sha256']=metrics['calendar_sha256']
        metrics.update(method_metrics)
        checks.update(method_checks)
        reasons.update(method_reasons)
    if selected['status']=='READY':
        baseline_points=None
        if baseline['status']=='READY' and benchmark is not None:
            require(baseline['market_input_sha256']==selected['market_input_sha256'],'BENCHMARK_MARKET_INPUT')
            baseline_points=baseline['points']
        economic_metrics,economic_checks,economic_reasons=review_native_economics(
            selected['points'],baseline_points,policy['economic_checks'])
        metrics.update(economic_metrics)
        checks.update(economic_checks)
        reasons.update(economic_reasons)
    verdict='FAIL' if 'FAIL' in checks.values() else ('INSUFFICIENT' if reasons or 'INSUFFICIENT' in checks.values() else 'PASS')
    report=dict(schema=contract.REPORT_SCHEMA,profile=policy['profile'],policy_sha256=digest(policy),cohort_sha256=digest(cohort),
        project_cut_sha256=cut['cut_sha256'],study_sha256=cohort['study_sha256'],as_of_ms=as_of_ms,
        statistical_decision=verdict,reasons=sorted(reasons),population=dict(attempt_count=cut['attempt_count'],
            unique_definition_count=cut['unique_definition_count'],selection_attempt_count=selection_count,
            mapped_attempt_count=cut['mapped_attempt_count'],registered_attempt_count=cut['registered_attempt_count'],
            unmapped_attempt_count=len(cut['unmapped']['search_candidates']),unfinished_attempt_count=len(cut['unfinished_attempts']),
            failed_attempt_count=sum(a['status']=='FAILED' for a in cut['attempts'])),attempts=attempts,
        selected_attempt=copy.deepcopy(cohort['selected_attempt']),benchmark_attempt=copy.deepcopy(cohort['benchmark_attempt']),
        series_sha256=digest(series),metrics=metrics,checks=checks,limitations=sorted(limitations),qualification='NOT_ASSESSED',
        independent_oos_verified=False,promotion_approved=False,submission_enabled=False,economic_authority=False)
    return contract.validate_report(report)
