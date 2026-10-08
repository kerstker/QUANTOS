"""Actual registered native calculations and original statistical kernels."""
import copy
import dataclasses
import importlib
from decimal import Decimal as D

import pytest

from quantos.contracts.registered_research_run import digest
from tests.contracts.test_research_statistical_review import policy, DAY
from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def long_arguments(original, *, name='candidate-a', quantity='0.1', sign=1):
    """Synthetic prices; actual original ledger computes every risk equity input."""
    from tests.execution import test_authoritative_perpetual_backtest as f
    from services.execution.perpetual_ledger_v2 import LinearPerpetualLedger, MarkEvent, FundingEvent
    from services.execution.authoritative_perpetual_backtest import ObservedLedgerEvent, ExecutableVenueEvent
    args=copy.deepcopy(original);start=f.DECISION
    rule=dataclasses.replace(f._rule(),effective_until=start+33*DAY)
    args['contract_rules']=(rule,)
    args['funding_policies']=(dataclasses.replace(f._funding_policy(),coverage_end=start+33*DAY,
        settlement_interval_ms=40*DAY,settlement_times=(start-5,)),)
    args['alpha_definition']['alpha_id']=name
    book=LinearPerpetualLedger((rule.spec,),initial_collateral=args['initial_collateral'])
    events=[];decisions=[]
    q=D(quantity)
    prior=D(0)
    for i in range(30):
        now=start+i*DAY
        price=D('100')+D(sign)*D(i+i//3)
        mark=MarkEvent(now-10,'BTCUSDT',price)
        book.process_events((mark,))
        events.append(ObservedLedgerEvent(event=mark,available_time=now-9,ingestion_time=now-8,
            source_identity=f'synthetic-mark-{i}',rule_id=rule.rule_id))
        if i==0:
            funding=FundingEvent(start-5,'BTCUSDT',D('.001'),price)
            book.process_events((funding,))
            events.append(ObservedLedgerEvent(event=funding,available_time=start-4,ingestion_time=start-3,
                source_identity='synthetic-funding',rule_id=rule.rule_id))
        cap=f._capacity(period_index=now//DAY)
        # All original executions, including the final close, charge real fees.
        cost=dataclasses.replace(f._cost(),fee_rate=D('.001'),
            spread_rate=D('0'),impact_rate=D('0'))
        target=q if i%2==0 else D(0)
        execution=f._execution(timestamp=now+10,quantity=str(target-prior),capacity=cap,cost=cost)
        execution=dataclasses.replace(execution,execution_id=f'execution-{i}',execution_price=price,
            opportunity=dataclasses.replace(execution.opportunity,coverage_start=now-10,
                executable_events=(ExecutableVenueEvent(now-10,price),ExecutableVenueEvent(now+10,price))))
        signal=dataclasses.replace(f._decision().signal_inputs[0],record_id=f'signal-{i}',
            event_time=now-20,available_time=now-10,ingestion_time=now-5,
            value={'instrument_id':'BTCUSDT','score':target/D('.4')})
        risk=f._risk(total_aum=float(book.account_equity()),capacity=cap)
        # Keep the real original risk contract with a bounded three-point
        # diagnostic history; actual account observations are separate.
        times=risk.timestamps[-3:]
        full=dataclasses.replace(risk.full_inputs,timestamps=times,
            alpha_returns={k:((-0.001,0.0,0.001) if j==0 else (0.001,-0.002,0.001))
                           for j,k in enumerate(risk.full_inputs.alpha_returns)},
            regime_labels=risk.full_inputs.regime_labels[-3:],alpha_metadata=({k:v for k,v in risk.full_inputs.alpha_metadata[0].items() if k!='alpha_id'},))
        values=dict(portfolio_returns=risk.returns[-3:],initial_equity=risk.equity_curve[-4],
            equity_curve=risk.equity_curve[-3:-1]+(float(book.account_equity()),),timestamps=times,alpha_returns=full.alpha_returns,
            regime_labels=full.regime_labels,alpha_metadata=full.alpha_metadata,
            capacity_and_aum=dict(capacity_cny=dict(full.capacity_cny),daily_volume_cny=full.daily_volume_cny,
                                  total_aum=full.total_aum))
        risk=dataclasses.replace(risk,returns=values['portfolio_returns'],initial_equity=values['initial_equity'],
            equity_curve=values['equity_curve'],timestamps=times,full_inputs=full,
            source_records={k:dataclasses.replace(v,value=values[k]) for k,v in risk.source_records.items()})
        decisions.append(dataclasses.replace(f._decision(execution=execution,signal=signal,risk=risk),
            decision_id=f'decision-{i}',timestamp=now))
        book.process_events((execution.build_trade_event(rule.spec),))
        prior=target
    terminal=MarkEvent(start+30*DAY,'BTCUSDT',price)
    events.append(ObservedLedgerEvent(event=terminal,available_time=terminal.timestamp,
        ingestion_time=terminal.timestamp,source_identity='synthetic-terminal',rule_id=rule.rule_id))
    args.update(decisions=tuple(decisions),market_events=tuple(events))
    return args


def identity(attempt):
    return {key:attempt[key] for key in ('invocation_id','native_trial_id','search_id','provenance_trial_id','config_hash')}


def build_statistics(registered_case, technical_arguments, *, sign=1, moments=None, declare_locked_oos=False):
    from tools import registered_technical_research_run as run
    from tests.tools.test_registered_research_evaluation import declaration
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f=registered_case
    args=[long_arguments(technical_arguments,name=n,quantity=q,sign=sign) for n,q in
          [('candidate-a','0.3'),('candidate-b','0.2'),('fixed-native-baseline','0.1')]]
    benchmark=dict(id='fixed-native-baseline',definition_sha256=run._candidate(args[-1])[1],
        input_sha256=digest(run.technical_input(args[-1])),comparison='SAME_NATIVE_SOURCE_DATASET_GRID')
    p=policy(source=digest(args[0]['release_identity']),dataset=digest(args[0]['dataset_identity']),benchmark=benchmark)
    p['selection_policy']['definition_sha256']=run._candidate(args[0])[1]
    if moments is not None:p['dsr_policy']['moments']=moments
    plans=[];results=[]
    for i,arg in enumerate(args):
        invocation=str(i+1)*64
        core=declaration(f,arg,invocation)
        core['sample']=copy.deepcopy(p['sample'])
        core['splits']=dict(train=copy.deepcopy({k:v for k,v in p['sample'].items() if k!='frequency_ms'}),
                            validation=None,locked_oos=None)
        if declare_locked_oos:
            # Declaration geometry only: there are no synthetic OOS observations
            # or independent custody claims in this original-input fixture.
            start=p['sample']['start_ms']
            end=p['sample']['end_ms']
            core['sample']['end_ms']=end+10*DAY
            core['splits']=dict(train=dict(start_ms=start,end_ms=start+15*DAY),
                validation=dict(start_ms=start+16*DAY,end_ms=end),
                locked_oos=dict(start_ms=end+DAY,end_ms=end+10*DAY))
        core['selection']['validation_policy_sha256']=digest(p)
        core['benchmark']={k:benchmark[k] for k in ('id','definition_sha256','input_sha256')}
        plan=run.prepare_technical(arg,project_root=f['root']/'project',search_id='statistics-'+str(i),
            producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-technical',
            registered_at_ms=f['clock'][0],invocation_id=invocation,evaluation_core=core)
        result=run.run_technical(arg,destination=f['run'].run_directory(f['root']/'project',plan),
            registry=f['registry'],clock_ms=lambda:f['clock'][0])
        plans.append(plan);results.append(result)
    cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    attempts=sorted((identity(a) for a in cut['attempts']),key=lambda a:(a['invocation_id'],a['native_trial_id']))
    cohort=dict(schema='QUANTOS_RESEARCH_STATISTICAL_COHORT_V1',study_sha256=p['study_scope']['study_sha256'],
        policy_sha256=digest(p),project_cut_sha256=cut['cut_sha256'],frozen_at_ms=f['clock'][0],attempts=attempts,
        selected_attempt=attempts[0],benchmark_attempt=attempts[-1])
    return dict(f=f,policy=p,cohort=cohort,cut=cut,args=args,plans=plans,results=results)


@pytest.fixture
def native_statistics(registered_case,technical_arguments):
    return build_statistics(registered_case,technical_arguments)


def refresh(case):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f=case['f'];cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    case['cut']=cut;case['cohort']['project_cut_sha256']=cut['cut_sha256']
    case['cohort']['attempts']=sorted((identity(a) for a in cut['attempts']),key=lambda a:(a['invocation_id'],a['native_trial_id']))


def add_attempt(case,arguments,*,number,execute=True,invocation_id=None):
    from tools import registered_technical_research_run as run
    from tests.tools.test_registered_research_evaluation import declaration
    f=case['f'];p=case['policy'];invocation=invocation_id or str(number)*64
    core=declaration(f,arguments,invocation);core['sample']=copy.deepcopy(p['sample'])
    core['splits']=dict(train={k:v for k,v in p['sample'].items() if k!='frequency_ms'},validation=None,locked_oos=None)
    core['selection']['validation_policy_sha256']=digest(p)
    core['benchmark']={k:p['benchmark_policy'][k] for k in ('id','definition_sha256','input_sha256')}
    plan=run.prepare_technical(arguments,project_root=f['root']/'project',search_id='statistics-'+str(number),
        producer_id='isolated-native',actor_id='AUTOMATED_TEST',dataset_version='native-technical',
        registered_at_ms=f['clock'][0],invocation_id=invocation,evaluation_core=core)
    if execute:return run.run_technical(arguments,destination=f['run'].run_directory(f['root']/'project',plan),
        registry=f['registry'],clock_ms=lambda:f['clock'][0])
    return plan


def review(case, **changes):
    api=importlib.import_module('services.validation.project_research_statistics')
    benchmark=api.project_native_series(project_cut=case['cut'],attempt=case['cohort']['benchmark_attempt'],
                                        sample=case['policy']['sample'],sample_method=case['policy']['sample_method'])
    kwargs=dict(project_cut=case['cut'],policy=case['policy'],cohort=case['cohort'],benchmark=benchmark,
                as_of_ms=case['f']['clock'][0])
    kwargs.update(changes)
    return api.review_project_statistics(**kwargs)


def test_original_statistical_review_accepts_pre_oos_grid_without_oos_authority(registered_case, technical_arguments):
    from quantos.contracts.research_evaluation_plan import core_status
    case=build_statistics(registered_case,technical_arguments,declare_locked_oos=True)
    before=copy.deepcopy(case['plans'])
    value=review(case)
    assert value['statistical_decision']=='PASS'
    assert all(row['policy_bound'] for row in value['attempts'])
    assert value['population']['registered_attempt_count']==3
    assert 'NO_INDEPENDENT_OOS_PROOF' in value['limitations']
    assert value['economic_authority'] is value['submission_enabled'] is value['promotion_approved'] is False
    assert case['plans']==before
    for plan in case['plans']:
        status=core_status(plan['evaluation_core'])
        assert status['oos_status']=='UNKNOWN'
        assert status['submission_enabled'] is status['economic_authority'] is False


def test_real_registered_native_population_can_pass_statistics_without_authority(native_statistics):
    value=review(native_statistics)
    assert value['statistical_decision']=='PASS'
    assert value['population']['attempt_count']==3
    assert value['population']['selection_attempt_count']==2
    assert value['qualification']=='NOT_ASSESSED'
    assert value['promotion_approved'] is value['economic_authority'] is value['submission_enabled'] is False
    assert all(r['authoritative'] is False for r in native_statistics['results'])
    # Original kernels, independently called over the actually projected points.
    import math
    import statistics
    from services.validation import project_research_statistics as api
    from services.validation.deflated_sharpe import deflated_sharpe_ratio,pbo_cscv
    from services.validation.catalog_evidence import _observed_sr,_VAR_SR_FLOOR
    from services.validation.cpcv import cpcv_summary
    c=native_statistics;p=c['policy'];vectors={}
    for attempt in c['cohort']['attempts'][:-1]:
        row=api.project_native_series(project_cut=c['cut'],attempt=attempt,sample=p['sample'],sample_method=p['sample_method'])
        assert row['cash']['closed_at_sample_end'] is True
        vectors[digest(attempt)]=[float(D(v['net_return'])) for v in row['points'][1:]]
    selected=digest(c['cohort']['selected_attempt']);sr={k:_observed_sr(v) for k,v in vectors.items()}
    seq=vectors[selected];mean=statistics.mean(seq);m2=statistics.mean((v-mean)**2 for v in seq)
    skew=statistics.mean((v-mean)**3 for v in seq)/m2**1.5
    kurt=statistics.mean((v-mean)**4 for v in seq)/m2**2
    expected=deflated_sharpe_ratio(sr[selected],30,2,max(statistics.pvariance(sr.values()),_VAR_SR_FLOOR),skew,kurt)
    assert value['metrics']['dsr']==pytest.approx(expected)
    assert value['metrics']['skew']==pytest.approx(skew) and value['metrics']['kurtosis']==pytest.approx(kurt)
    assert value['metrics']['cpcv']==cpcv_summary(vectors,n_groups=4,k_test=2,embargo=1)
    matrix=[[math.fsum(v[30*b//4:30*(b+1)//4]) for v in vectors.values()] for b in range(4)]
    assert value['metrics']['pbo']==pbo_cscv(matrix)
    assert value['checks']=={k:'PASS' for k in value['checks']}


def test_exact_original_benchmark_is_required(native_statistics):
    value=review(native_statistics,benchmark=None)
    assert value['statistical_decision']=='INSUFFICIENT'
    assert 'BENCHMARK_MISSING' in value['reasons']


def test_actual_native_fixture_finishes_with_original_closed_cash(native_statistics):
    for result in native_statistics['results']:
        body=result['detail']['result_hash_payload']
        assert body['ending_state']['positions']=={}
        assert len(body['decision_contexts'])==30
        assert D(body['decision_contexts'][-1]['pretrade_equity'])>D(body['reconciliation']['ending_equity'])


def test_native_losses_fail_without_changing_a_threshold(registered_case,technical_arguments):
    case=build_statistics(registered_case,technical_arguments,sign=-1)
    value=review(case)
    assert value['statistical_decision']=='FAIL'
    assert value['checks']['net_return']==value['checks']['benchmark_excess']=='FAIL'
    assert D(value['metrics']['sample_net_return'])<0
    assert value['economic_authority'] is False


def test_full_real_population_keeps_duplicate_failed_and_not_started_attempts(native_statistics):
    case=native_statistics
    definition_count=case['cut']['unique_definition_count']
    add_attempt(case,case['args'][0],number=4)
    refresh(case)
    value=review(case)
    assert value['population']['attempt_count']==4
    assert value['population']['unique_definition_count']==definition_count
    assert value['metrics']['n_trials']==3
    bad=copy.deepcopy(case['args'][1]);bad['alpha_definition']['alpha_id']='actually-invalid-causal-input'
    decision=bad['decisions'][0];signal=dataclasses.replace(decision.signal_inputs[0],
        available_time=decision.timestamp+1,ingestion_time=decision.timestamp+2)
    bad['decisions']=(dataclasses.replace(decision,signal_inputs=(signal,)),)+bad['decisions'][1:]
    with pytest.raises(ValueError):add_attempt(case,bad,number=5)
    add_attempt(case,case['args'][1],number=6,execute=False)
    refresh(case)
    value=review(case)
    assert value['population']['attempt_count']==6 and value['population']['failed_attempt_count']==1
    assert value['population']['unfinished_attempt_count']>=1
    assert value['statistical_decision']=='INSUFFICIENT'
    assert value['metrics']['dsr'] is None and value['metrics']['pbo'] is None
    assert any(a['native_status']=='FAILED' for a in value['attempts'])
    assert any(a['identity']['provenance_trial_id'] is None for a in value['attempts'])
    assert 'NATIVE_ATTEMPT_NOT_COMPLETE' in value['reasons']
    registry=case['f']['registry'];now=case['f']['clock'][0]
    registry.begin_search_attempt(search_id='old-unmapped',search_kind='BACKTEST',source='old-producer',
        dataset_version='unknown-original',recorded_at=now)
    registry.register_search_candidates('old-unmapped',['b'*64],registered_at=now)
    refresh(case);value=review(case)
    assert value['population']['attempt_count']==7 and value['population']['unmapped_attempt_count']==1
    assert value['metrics']['n_trials']==6 and value['metrics']['dsr'] is None
    assert 'PROJECT_POPULATION_UNMAPPED' in value['reasons']


def test_originals_roster_benchmark_grid_and_report_types_are_not_caller_claims(native_statistics):
    from services.validation import project_research_statistics as api
    from quantos.contracts import research_statistical_review as contract
    c=native_statistics;p=c['policy'];attempt=c['cohort']['selected_attempt']
    projected=api.project_native_series(project_cut=c['cut'],attempt=attempt,sample=p['sample'],sample_method=p['sample_method'])
    altered=copy.deepcopy(projected);altered['points'][1]['equity']='9999'
    with pytest.raises(ValueError,match='BENCHMARK_PROJECTION_CHANGED'):review(c,benchmark=altered)
    cohort=copy.deepcopy(c['cohort']);cohort['attempts'].pop(1)
    with pytest.raises(ValueError,match='COMPLETE_ATTEMPT_SET'):review(c,cohort=cohort)
    partial=api.project_native_series(project_cut=c['cut'],attempt=attempt,sample=p['sample'],
        sample_method='NATIVE_PRETRADE_EQUITY_EXACT_GRID_V1')
    assert partial['status']=='INSUFFICIENT' and partial['points']==[]
    # A shorter terminal window cannot discard the native later event/cash.
    sample=dict(p['sample'],end_ms=p['sample']['end_ms']-DAY)
    partial=api.project_native_series(project_cut=c['cut'],attempt=attempt,sample=sample,sample_method=p['sample_method'])
    assert partial['reasons']==['CANONICAL_FLAT_TERMINAL_NOT_COVERED']
    report=review(c)
    for key,replacement in [('population',{'attempt_count':True}),('metrics',{'dsr':True}),('attempts',[])]:
        broken=copy.deepcopy(report);broken[key]=replacement
        with pytest.raises(ValueError):contract.validate_report(broken)


def test_real_gold_forward_is_not_relabelled_backtest(registered_case):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation.project_research_statistics import project_native_series
    f=registered_case;plan=f['prepare']('gold')
    f['run'].run_gold(f['arguments'],destination=f['directories']['gold'],registry=f['registry'],clock_ms=lambda:f['clock'][0])
    cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    value=project_native_series(project_cut=cut,attempt=identity(cut['attempts'][0]),sample=policy()['sample'],
        sample_method='CANONICAL_FLAT_TERMINAL_CASH_EXACT_GRID_V1')
    assert value['status']=='INSUFFICIENT' and value['reasons']==['FORWARD_NOT_BACKTEST']
    assert value['native_result_sha256'] is not None and value['points']==[]
    assert plan['search']['search_kind']=='FORWARD'


def test_preregistered_normal_reference_is_only_insufficient_diagnostic(registered_case,technical_arguments):
    c=build_statistics(registered_case,technical_arguments,moments='NORMAL_REFERENCE_SKEW_0_KURT_3')
    value=review(c)
    assert value['statistical_decision']=='INSUFFICIENT'
    assert all(v=='PASS' for v in value['checks'].values())
    assert 'EMPIRICAL_MOMENTS_REQUIRED' in value['reasons']
    assert value['promotion_approved'] is False


def test_cscv_combinatorial_work_is_bounded_before_kernel_execution():
    from services.validation.project_research_statistics import _work_budget
    with pytest.raises(ValueError,match='WORK_BUDGET'):
        _work_budget(1000,31,dict(n_groups=4,k_test=2),16)


def test_empirical_moments_report_unresolvable_float_variance_as_missing():
    from services.validation.project_research_statistics import _population_moments
    assert _population_moments([1e-250,2e-250,3e-250]) is None
    assert _population_moments([0.0,0.0]) is None
    skew,kurt=_population_moments([-1.0,0.0,1.0])
    assert skew==0 and kurt==pytest.approx(1.5)


@pytest.mark.parametrize('late_fact',['registry','invocation.json','outcome.json'])
def test_cohort_cannot_predate_an_actual_later_registry_fact(registered_case,late_fact):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f=registered_case;plan=f['prepare']('gold-cut-clock')
    f['run'].run_gold(f['arguments'],destination=f['directories']['gold-cut-clock'],registry=f['registry'],clock_ms=lambda:f['clock'][0])
    p=policy(source=digest(plan['source_identity']),benchmark=dict(id=plan['trials'][1]['candidate_id'],
        definition_sha256=plan['trials'][1]['config_hash'],input_sha256=plan['input_sha256'],comparison='SAME_NATIVE_SOURCE_DATASET_GRID'))
    p['selection_policy']['candidate_id']=plan['trials'][0]['candidate_id']
    p['selection_policy']['definition_sha256']=plan['trials'][0]['config_hash'];p['market_scope']=['XAUUSDT']
    if late_fact=='registry':
        f['registry'].begin_search_attempt(search_id='actual-later-search',search_kind='BACKTEST',source='old-producer',
            dataset_version='original-data',recorded_at=f['clock'][0]+1)
    else:
        import os
        from tests.tools.test_registered_research_run import join
        f['prepare']('actual-interrupted-run')
        child=os.fork()
        if child==0:
            try:
                original=f['run']._put
                def put(path,*args,**kwargs):
                    result=original(path,*args,**kwargs)
                    if path.name==('running.json' if late_fact=='invocation.json' else 'report.json'):
                        f['clock'][0]+=10
                    if path.name==late_fact:os._exit(73)
                    return result
                f['run']._put=put
                f['run'].run_gold(f['arguments'],destination=f['directories']['actual-interrupted-run'],
                    registry=f['registry'],clock_ms=lambda:f['clock'][0])
            except BaseException:os._exit(98)
            os._exit(99)
        assert join(child)==73
        assert (f['directories']['actual-interrupted-run']/late_fact).is_file()
    cut=ProjectResearchEvidenceReader(f['root']/'project',registry_database=f['root']/'registry.db')()
    rows=sorted((identity(a) for a in cut['attempts']),key=lambda a:(a['invocation_id'],a['native_trial_id']))
    selected=next(a for a in rows if a['config_hash']==plan['trials'][0]['config_hash'])
    benchmark=next(a for a in rows if a['config_hash']==plan['trials'][1]['config_hash'])
    c=dict(schema='QUANTOS_RESEARCH_STATISTICAL_COHORT_V1',study_sha256=p['study_scope']['study_sha256'],
        policy_sha256=digest(p),project_cut_sha256=cut['cut_sha256'],frozen_at_ms=f['clock'][0],attempts=rows,
        selected_attempt=selected,benchmark_attempt=benchmark)
    case=dict(f=f,policy=p,cohort=c,cut=cut)
    with pytest.raises(ValueError,match='COHORT_BEFORE_PROJECT_FACT'):review(case)


def test_thresholds_use_original_unrounded_cpcv_distribution():
    from services.validation.cpcv import cpcv_summary,cpcv_oos_distribution
    values=[(-0.01 if i%2 else 0.01)-1e-12 for i in range(32)]
    inputs={'a':values,'b':[v*0.9 for v in values]}
    summary=cpcv_summary(inputs,n_groups=4,k_test=2,embargo=1)
    distribution=cpcv_oos_distribution(inputs,n_groups=4,k_test=2,embargo=1)
    assert summary['p05_oos_sharpe']==0.0  # Existing display rounding unchanged.
    from services.validation.project_research_statistics import _cpcv_thresholds
    exact=_cpcv_thresholds(distribution[0])
    assert exact['p05_oos_sharpe']<0
    assert exact['frac_positive']==0


def test_same_name_other_definition_cannot_replace_the_predeclared_definition(native_statistics):
    from tools import registered_technical_research_run as producer
    case=native_statistics;original=copy.deepcopy(case['cohort']['selected_attempt'])
    other=copy.deepcopy(case['args'][0])
    other['alpha_definition']['position_mapping']['max_abs_quantity']=D('11')
    other_definition=producer._candidate(other)[1]
    assert other_definition!=original['config_hash']
    output=add_attempt(case,other,number=4,invocation_id='0'*64)
    assert output['authoritative'] is False
    refresh(case)
    substituted=next(a for a in case['cohort']['attempts'] if a['invocation_id']=='0'*64)
    forged=copy.deepcopy(case['cohort']);forged['selected_attempt']=substituted
    with pytest.raises(ValueError,match='PREDECLARED_SELECTION'):review(case,cohort=forged)
    value=review(case)
    assert value['selected_attempt']==original
    assert value['population']['attempt_count']==4
    assert value['population']['unique_definition_count']==4
    assert value['metrics']['n_trials']==3
    assert any(row['identity']==substituted for row in value['attempts'])
    # Lexicographic selection remains applicable only among the same exact
    # original definition, even with a smaller different-definition attempt.
    add_attempt(case,case['args'][0],number=5,invocation_id='0'*63+'1')
    refresh(case)
    with pytest.raises(ValueError,match='PREDECLARED_ATTEMPT_TIE'):review(case)
    repeated=next(a for a in case['cohort']['attempts'] if a['invocation_id']=='0'*63+'1')
    case['cohort']['selected_attempt']=repeated
    value=review(case)
    assert repeated['config_hash']==original['config_hash']
    assert value['selected_attempt']==repeated and value['population']['attempt_count']==5
    assert value['population']['unique_definition_count']==4 and value['metrics']['n_trials']==4
