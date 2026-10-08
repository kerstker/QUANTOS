"""Replay all native Gold signal decisions against strictly available originals.

This audits original signal calculations, not OOS sealing, search-selection
leakage, model maturity, independent source/time, or trade authorization.
Future probe bars are adversarial inputs only, never market or P&L evidence.
"""
from dataclasses import asdict
from decimal import Decimal
import re

from quantos.contracts.decimal_arithmetic import ledger_decimal_context
from quantos.contracts.gold_causality_policy import validate, policy_sha256, REVIEW_SCHEMA, SCOPE
from quantos.streaming_identity import StreamArray, exact_stream_identity_hash as digest
from services.research import gold_bar_candidates as signals

SCHEMA = REVIEW_SCHEMA


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_CANDIDATE_CAUSALITY_' + reason)


def items(value):
    return value.items() if isinstance(value, StreamArray) else iter(value)


def bar(value):
    if type(value) is signals.Bar:
        value.validate()
        return value
    require(type(value) is dict and set(value) == set(signals.Bar.__dataclass_fields__), 'BAR_FIELDS')
    require(all(type(value[k]) is str for k in ('open','high','low','close')), 'BAR_PRICES')
    result = signals.Bar(**{k:Decimal(v) if k in ('open','high','low','close') else v for k,v in value.items()})
    result.validate()
    return result


def bar_identity(value):
    return {k:str(v) if type(v) is Decimal else v for k,v in asdict(value).items()}


@ledger_decimal_context()
def assess_candidate(*, bars, decision_rows, candidate_id, policy, subject, native_result_hash,
                     market_history_policy):
    validate(policy)
    require(candidate_id in (*signals.CANDIDATES, 'net_portfolio'), 'CANDIDATE')
    require(market_history_policy == 'GOLD_ORIGINAL_RECEIPT_AVAILABLE_BARS_V1', 'BAR_SOURCE')
    identity = {k:subject[k] for k in ('invocation_id','native_trial_id','config_hash')}
    require(all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in (*identity.values(),native_result_hash)), 'IDENTITY')
    budget = policy['budget']
    originals = []
    for value in items(bars):
        require(len(originals) < budget['maximum_bars'], 'BAR_BUDGET')
        row = bar(value)
        require(not originals or row.open_ms > originals[-1].open_ms, 'BAR_ORDER')
        originals.append(row)
    original_bars = tuple(originals)
    decision_count = 0
    first = last = None
    def rows():
        previous = None
        for index,row in enumerate(items(decision_rows)):
            require(index < budget['maximum_decisions'] and type(row) is dict, 'DECISION_BUDGET_OR_FIELDS')
            t = row['timestamp']
            require(type(t) is int and 0 <= t < 2**63 and (previous is None or previous < t), 'DECISION_CLOCK')
            previous = t
            yield row
    def original_axis():
        nonlocal decision_count,first,last
        for row in rows():
            t=row['timestamp']
            first=t if first is None else first
            last=t
            decision_count+=1
            yield row
    axis_hash=digest(StreamArray(original_axis))
    components = signals.CANDIDATES if candidate_id == 'net_portfolio' else (candidate_id,)
    monotone = signals.causal_receipts(original_bars)
    result=dict(schema=SCHEMA, **identity, candidate_id=candidate_id, native_result_hash=native_result_hash,
        policy_sha256=policy_sha256(policy), declared_sample=dict(policy['sample']),
        original_bars_sha256=digest(StreamArray(lambda: (bar_identity(b) for b in original_bars))),
        original_decisions_sha256=axis_hash, original_bar_count=len(original_bars),
        decision_count=decision_count, actual_first_decision_ms=first, actual_last_decision_ms=last,
        component_ids=list(components), runtime_path='ORIGINAL_CAUSAL_EVALUATOR' if monotone else 'ORIGINAL_FULL_HISTORY',
        status='INSUFFICIENT', leakage_flag=None, assessed_decisions=0, component_evaluations=0,
        failed_components=0, unassessed_components=0, fallback_bar_reads=0,
        reasons=[], proof_sha256=None, synthetic_future_probes_are_market_evidence=False,
        scope=SCOPE,
        independent_verification=False, limitations=[
            'SAME_ORIGINAL_SIGNAL_MATH_NOT_INDEPENDENT_REVIEW',
            'ACTUAL_DECISION_COVERAGE_DOES_NOT_PROVE_FULL_DECLARED_RUN_COVERAGE',
            'CAUSALITY_REPLAY_IS_NOT_OOS_SEAL_OR_SEARCH_SELECTION_LEAKAGE_ASSESSMENT'],
        economic_authority=False,submission_enabled=False)
    if not originals or not decision_count:
        result['reasons']=['ORIGINAL_BARS_OR_NATIVE_DECISIONS_UNAVAILABLE']
    else:
        future_open=originals[-1].close_ms
        future_close=future_open+signals.INTERVAL_MS
        future_available=max(last+1,max(b.available_ms for b in originals)+1,future_close)
        require(future_available < 2**63, 'FUTURE_PROBE_TIME')
        probes=tuple(signals.Bar(future_open,future_close,future_available,p,p,p,p)
                     for p in (originals[-1].close+Decimal(1000),originals[-1].close*10+1))
        variants=(original_bars,*(original_bars+(p,) for p in probes))
        engines=tuple(signals.causal_evaluator(v) for v in variants) if monotone else None
        prefix_engine=signals.CausalGoldBarEvaluator(()) if monotone else None
        offset=0
        reasons=set()
        def proofs():
            nonlocal offset
            for row in rows():
                t=row['timestamp']
                known=tuple(b for b in original_bars if b.available_ms <= t) if not monotone else ()
                if monotone:
                    end=offset
                    while end < len(original_bars) and original_bars[end].available_ms <= t:
                        end+=1
                    if end > offset:
                        prefix_engine.append_bars(original_bars[offset:end])
                        offset=end
                else:
                    cost=(sum(len(v) for v in variants)+len(known))*len(components)
                    result['fallback_bar_reads']+=cost
                    require(result['fallback_bar_reads'] <= budget['maximum_fallback_bar_reads'], 'FALLBACK_BUDGET')
                complete=True
                proof=[]
                for candidate in components:
                    result['component_evaluations']+=1
                    try:
                        outputs=[signals.evaluate(engine if monotone else values,candidate=candidate,as_of_ms=t)
                                 for engine,values in zip(engines if monotone else (None,)*3,variants)]
                        reference=signals.evaluate(prefix_engine if monotone else known,candidate=candidate,as_of_ms=t)
                        mismatch=any(value != reference for value in outputs)
                        observed_mismatch=candidate_id != 'net_portfolio' and any(
                            k in row and row[k] != outputs[0][k] for k in ('status','entry_side'))
                        if mismatch or observed_mismatch:
                            result['failed_components']+=1
                            reasons.add('UNAVAILABLE_FUTURE_CHANGED_NATIVE_SIGNAL' if mismatch else 'ORIGINAL_SIGNAL_OBSERVATION_MISMATCH')
                        proof.append(dict(candidate=candidate,reference=digest(reference),
                            originals_and_probes=[digest(value) for value in outputs],
                            status='FAIL' if mismatch or observed_mismatch else 'PASS'))
                    except (ArithmeticError,ValueError,TypeError,KeyError,IndexError):
                        complete=False
                        result['unassessed_components']+=1
                        reasons.add('NATIVE_SIGNAL_COMPONENT_UNASSESSABLE')
                        proof.append(dict(candidate=candidate,status='INSUFFICIENT'))
                result['assessed_decisions']+=complete
                yield dict(original_decision=row,components=proof)
        result['proof_sha256']=digest(StreamArray(proofs))
        if result['failed_components']:
            result.update(status='FAIL',leakage_flag=True)
        elif result['unassessed_components'] == 0 and result['assessed_decisions'] == decision_count:
            result.update(status='PASS',leakage_flag=False)
        result['reasons']=sorted(reasons)
    result['review_sha256']=digest(result)
    return result
