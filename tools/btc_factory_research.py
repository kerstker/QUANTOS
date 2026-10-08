"""Fixed BTC factory policy replay with canonical accounting and resumable cases."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal as D
import json
from pathlib import Path
import time
import numpy as np

from quantos.runtime_paths import runtime_state_root
from quantos.local_artifacts import with_result_hash as seal
from services.research.btc_factory_replay import DAY, MINUTE, POLICIES, policy_targets, THRESHOLD_BPS
from services.research.perpetual_alpha import replay
from services.research.trial_ledger import TrialLedger, TrialRecord, TrialLedgerError
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.btc_mechanism_study import digest, forecasts, load_panel
from tools.cache_io import atomic_write_json
from tools.perpetual_alpha_research import load_data, metrics
from tools.perpetual_replay_research import source_identity

PLAN=Path(__file__).resolve().parents[1]/'docs/engineering/plans/2026-09-07-btc-factory-replay.md'
SCHEMA='QUANTOS_BTC_FACTORY_REPLAY_V1'


def snapshot(directory):
    return {str(p.relative_to(directory)):digest(p.read_bytes())
            for p in sorted(directory.rglob('*')) if p.is_file()}



def verified(path):
    document=json.loads(path.read_text())
    if document.get('result_hash')!=digest({k:v for k,v in document.items() if k!='result_hash'}):
        raise ValueError('artifact hash mismatch')
    return document


def _trial(ledger, trial_id):
    try:
        return ledger.load(trial_id)
    except TrialLedgerError as exc:
        if str(exc) != 'unknown trial':raise
        return None


def _event(ledger,record,status):
    current=_trial(ledger,record['trial_id'])
    ledger.append(TrialRecord(**record,status=status,event_index=len(current.events) if current else 0,
                              at=time.time_ns()//1_000_000))


def run_factory(params):
    if (set(params)-{'attempt','resume'} or type(params.get('attempt')) is not int
            or not 1<=params['attempt']<=1000 or type(params.get('resume',False)) is not bool):
        raise ValueError('invalid bounded factory request')
    root=runtime_state_root()/'data/cache'
    directory=root/'perpetual_alpha/input';output=root/'btc_factory/results'
    output.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(output/'factory.lock') as acquired:
        if not acquired:raise ValueError('factory calculation already running')
        return _run(directory,output,params['attempt'],params.get('resume',False))


def _run(directory,output,attempt,resume):
    identity={'schema':SCHEMA,'source':source_identity(),'input_hash':digest(snapshot(directory)),
              'plan_hash':digest(PLAN.read_bytes()),'attempt':attempt,
              'fee_bps':'3.6','threshold_bps':THRESHOLD_BPS,'policies':list(POLICIES)}
    dest=output/str(attempt)
    if dest.exists() and not resume:raise ValueError('attempt exists; use explicit resume or a new attempt')
    if resume and not dest.exists():raise ValueError('resume attempt missing')
    with TrialLedger(str(output/'trials.db')) as ledger:
        if resume:
            plan=verified(dest/'plan.json')
            if plan['identity']!=identity:raise ValueError('resume source/plan/input identity changed')
            ledger.verify()
            if (dest/'failure.json').exists():raise ValueError('failed input attempt cannot resume; use new attempt')
        else:
            dest.mkdir();(dest/'cases').mkdir()
            records=[];cases=[]
            for policy in POLICIES:
                for slip in (2,5,10):
                    for stop in (False,True):
                        case={'policy':policy,'slip_bps':slip,'stop':stop}
                        definition=digest({'identity':identity,'case':case})
                        record=dict(trial_id=definition,alpha_id=digest(policy),family_id=digest('BTC_FACTORY_V1'),
                                    exact_definition_id=definition,validation_policy_digest=identity['plan_hash'])
                        records.append(record);cases.append({**case,'trial_id':definition})
            plan=seal({'identity':identity,'trials':records,'cases':cases})
            atomic_write_json(str(dest/'plan.json'),plan,indent=2)
        # Also completes registration if a process stopped while appending the plan.
        for record in plan['trials']:
            if _trial(ledger,record['trial_id']) is None:_event(ledger,record,'PLANNED')
        existing={}
        for case,record in zip(plan['cases'],plan['trials']):
            path=dest/'cases'/f"{case['trial_id']}.json"
            state=ledger.load(record['trial_id']).status
            if path.exists():
                value=verified(path)
                if value['case']!=case or value['identity']!=identity:raise ValueError('case identity mismatch')
                if state=='RUNNING':_event(ledger,record,value['status'])
                elif state!=value['status']:raise ValueError('case terminal state mismatch')
                existing[case['trial_id']]=value
            elif state in ('PASSED','FAILED','CANCELLED'):
                raise ValueError('terminal case artifact missing')
        if (dest/'result.json').exists():
            result=verified(dest/'result.json')
            if len(existing)!=48 or result['identity']!=identity:raise ValueError('incomplete final result')
            return result
        try:
            print('FACTORY_DATA_VALIDATION',flush=True)
            market,days,_,_,_,provenance=load_data(directory=directory)
            signal_days,x,y,data=load_panel(directory)
            if not np.array_equal(days,signal_days):raise ValueError('unaligned mechanism and execution days')
            p,fits=forecasts(days,x,y)
            targets,updates,frozen=policy_targets(days,p,y,x,fits,market['start']-5*MINUTE)
            evaluation=(days+DAY+5*MINUTE>=market['start'])&(days+DAY+5*MINUTE<market['end'])
            if any(any(values[i] is None for i in np.flatnonzero(evaluation)) for values in targets.values()):
                raise ValueError('missing evaluation prediction')
            if digest(snapshot(directory))!=identity['input_hash']:raise ValueError('input changed during read')
        except Exception as exc:
            # Preflight failure is terminal, including every unused planned case.
            for case,record in zip(plan['cases'],plan['trials']):
                if case['trial_id'] in existing:continue
                if ledger.load(record['trial_id']).status=='PLANNED':_event(ledger,record,'RUNNING')
                artifact=seal({'identity':identity,'case':case,'status':'FAILED','error':str(exc)})
                atomic_write_json(str(dest/'cases'/f"{case['trial_id']}.json"),artifact)
                _event(ledger,record,'FAILED')
            atomic_write_json(str(dest/'failure.json'),{'error':str(exc),'identity':identity})
            raise ValueError('factory input validation failed: '+str(exc)) from exc
        for case,record in zip(plan['cases'],plan['trials']):
            if case['trial_id'] in existing:continue
            if ledger.load(record['trial_id']).status=='PLANNED':_event(ledger,record,'RUNNING')
            def decide(at,held):
                i=int(np.searchsorted(days,at-5*MINUTE-DAY))
                if i>=len(days) or days[i]!=at-5*MINUTE-DAY:raise ValueError('missing signal day')
                return targets[case['policy']][i]
            try:
                result=replay(**market,decision=decide,slip_bps=case['slip_bps'],stop=case['stop'],taker_fee_bps=D('3.6'))
                stats=metrics(result)
                stats.update(reconciliation_residual=result['reconciliation']['residual'],
                             halted_at=result['halted_at'],order_reasons=dict(Counter(r['status'] for r in result['orders'])))
                artifact=seal({'identity':identity,'case':case,'status':'PASSED','metrics':stats,'replay':result})
            except Exception as exc:
                artifact=seal({'identity':identity,'case':case,'status':'FAILED','error':str(exc)})
            atomic_write_json(str(dest/'cases'/f"{case['trial_id']}.json"),artifact)
            _event(ledger,record,artifact['status']);existing[case['trial_id']]=artifact
            print('FACTORY_CASE',case['policy'],case['slip_bps'],case['stop'],artifact['status'],flush=True)
        summaries=[]
        for case in plan['cases']:
            artifact=existing[case['trial_id']]
            row={**case,'status':artifact['status'],'artifact_hash':artifact['result_hash']}
            if artifact['status']=='PASSED':
                row['metrics']={k:v for k,v in artifact['metrics'].items() if k not in ('daily_returns','daily_pnl')}
                row['equity']=artifact['replay']['daily']
            else:row['error']=artifact['error']
            summaries.append(row)
        result=seal({'schema':SCHEMA,'identity':identity,'cases':summaries,
            'selection_updates':[r for r in updates if market['start']-5*MINUTE<=r['decision_ms']<market['end']],
            'frozen_price_fit':frozen,'fits':fits,'data':{'market':provenance,'features':data},
            'trial_ledger_verified':ledger.verify(),'status':'TECHNICAL_VALIDATION_ONLY',
            'authoritative':False,'live_eligible':False,
            'limitations':['已查看历史；不构成独立OOS或前向证据。',
                '月度选择只用成熟预测误差；成本后的收益来自连续的唯一永续账本。',
                '历史接收时间UNKNOWN；费用3.6bp和当前场所规则为显式研究情景。',
                'IM=100%/MM=50%技术模型，不能认证真实强平/盘口队列或限价成交。']})
        atomic_write_json(str(dest/'result.json'),result,indent=2)
        return result
