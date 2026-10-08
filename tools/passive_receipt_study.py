"""Three prefix fits, frozen selection, then held-out passive receipt diagnostics."""
from dataclasses import replace
from services.research.passive_execution_study import PROFILES,run_profile
from services.research.passive_receipt_calibration import (
    queue_receipts, compare_receipts, calibration_score, summary_report,
)
from services.research.ai_factory_store import digest
from tools.profitability_study import write_new

OBJECTIVE='UNMATCHED_ORDERS_FINAL_QUANTITY_PATH_EVENT_QUANTITY_UNMATCHED_EVENTS_RECEIVE_TIME_VENUE_TIME'


def write_simulation(path,result):
    artifact=dict(result)
    artifact['engine_result_hash']=artifact.pop('result_hash')
    write_new(path,artifact)


def fit_before_cutoff(spec,tape,actions,account,source,output):
    output.mkdir(exist_ok=False)
    cutoff=spec['cutoff_ms'];observed=spec['observed']
    plan={'schema':'PASSIVE_RECEIPT_FIT_PLAN_V1','source':source,'input_hash':digest(spec),
        'cutoff_ms':cutoff,'partition_clock':'ACTUAL_CLIENT_RECEIPT_TIME','profiles_ms':list(PROFILES),
        'objective':OBJECTIVE,'tie_policy':'NO_SELECTION_WHEN_EQUAL','failure_policy':'NO_SELECTION_IF_ANY_PREFIX_RUN_FAILED',
        'history':'RETROSPECTIVE_IMPORTED_TRACE_NOT_NEW_OOS','economic_eligible':False}
    write_new(output/'preregistration.json',plan)
    prefix_tape=replace(tape,end_ms=cutoff)
    prefix_actions=tuple(a for a in actions if a.timestamp<=cutoff)
    prefix_account={**account,'account_frames':tuple(f for f in account['account_frames'] if f.timestamp<=cutoff)}
    trials=[]
    for profile in PROFILES:
        write_new(output/f'fit-{profile}.started.json',{'profile_ms':profile,'scope':'PREFIX_FIT',
            'preregistration_hash':digest(plan),'economic_eligible':False})
        try:
            result=run_profile(prefix_tape,prefix_actions,profile,account=prefix_account,release_identity=source)
            write_simulation(output/f'fit-{profile}.simulation.json',result)
            simulated=queue_receipts(result['queue'],prefix_actions,observed)
            report=compare_receipts(observed,simulated,actions,start_ms=tape.start_ms,end_ms=cutoff)
            write_new(output/f'fit-{profile}.comparison.json',report)
            trial={'profile_ms':profile,'status':'COMPLETED','fit':summary_report(report)}
        except Exception as exc:
            trial={'profile_ms':profile,'status':'FAILED','reason':type(exc).__name__}
        write_new(output/f'fit-{profile}.result.json',trial);trials.append(trial)
    selected=None;equal=[]
    if any(t['status']=='FAILED' for t in trials):status='INCOMPLETE_PREFIX_FITS'
    elif any(t['fit']['coverage_gaps'] for t in trials):status='INCOMPLETE_PREFIX_RECEIPT_COVERAGE'
    elif not any(t['fit']['observed_events'] for t in trials):status='INSUFFICIENT_PREFIX_RECEIPTS'
    else:
        best=min(calibration_score(t['fit']) for t in trials)
        winners=[t for t in trials if calibration_score(t['fit'])==best]
        equal=[t['profile_ms'] for t in winners]
        if len(winners)>1:status='AMBIGUOUS_PREFIX_FIT'
        elif not winners[0]['fit']['matched_events']:status='INSUFFICIENT_MATCHED_PREFIX_EVENTS'
        else:selected=winners[0]['profile_ms'];status='PROFILE_FROZEN_BEFORE_HELDOUT'
    selection={'status':status,'selected_profile_ms':selected,'equally_best_profiles_ms':equal,
        'cutoff_ms':cutoff,'objective':OBJECTIVE,'trials_attempted':len(trials),
        'prefix_results_hash':digest(trials),'economic_eligible':False}
    # This file is written before the caller starts ANY whole-window replay.
    write_new(output/'selection.json',selection)
    return {**selection,'trials':trials,'observed_provenance':observed['provenance'],
        'window_start_ms':tape.start_ms,'window_end_ms':observed['coverage']['end_ms'],
        'partition_clock':'ACTUAL_CLIENT_RECEIPT_TIME','heldout':None,
        'venue_receipts_verified':False,'queue_model_calibrated':False,'calibration_complete':False,
        'history':'RETROSPECTIVE_HELDOUT_CONTINUATION_NOT_NEW_OOS'}


def finish_heldout(fit,spec,actions,simulations,output):
    selected=fit['selected_profile_ms']
    if selected is None:return fit
    if selected not in simulations:return {**fit,'status':'HELDOUT_REPLAY_FAILED'}
    observed=spec['observed']
    simulated=queue_receipts(simulations[selected]['queue'],actions,observed)
    report=compare_receipts(observed,simulated,actions,start_ms=spec['cutoff_ms'],end_ms=observed['coverage']['end_ms'])
    write_new(output/f'heldout-{selected}.comparison.json',report)
    if report['coverage_gaps']:status='INCOMPLETE_HELDOUT_RECEIPT_COVERAGE'
    elif not report['paired_orders'] or not report['matched_events']:status='INSUFFICIENT_HELDOUT_RECEIPTS'
    else:status='HELDOUT_ERRORS_RECORDED_NOT_VENUE_ACCEPTANCE'
    return {**fit,'status':status,'heldout':summary_report(report,rows=20)}
