"""Pinned execution review readiness, not performance labels or authority."""
from decimal import Decimal,InvalidOperation
import re
from services.research.demo_oms_status import resolution_digest as digest
from services.research.gold_execution_identity import validate_identity


def is_completed_episode(feedback):
    """A flat roundtrip or a terminal, entirely unfilled single entry.

    Single filled batches remain useful manual reviews but cannot advance an
    automatic campaign before its position has been closed and reconciled.
    This structural predicate is used alongside the original digest/identity
    and availability checks, never as an independent receipt verifier.
    """
    if type(feedback) is not dict: return False
    if feedback.get('schema')=='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1':
        _roundtrip(feedback)
        return True
    if feedback.get('schema')!='DEMO_EXECUTION_RESEARCH_FEEDBACK_V1': return False
    try:
        orders=feedback['orders'];positions=feedback['positions']
        fields=('reservation_sha256','proposal_sha256','resolution_plan_sha256','manifest_hash','journal_tail')
        return (feedback.get('position_flat') is True and 'batches' not in feedback
            and all(type(feedback.get(k)) is str and re.fullmatch('[a-f0-9]{64}',feedback[k]) for k in fields)
            and type(orders) is list and len(orders)==1
            and type(orders[0]) is dict and type(orders[0].get('filled_quantity')) is str
            and orders[0]['terminal_status'] in ('EXPIRED','CANCELED','REJECTED')
            and Decimal(orders[0]['filled_quantity'])==0
            and type(positions) is dict and not set(positions)-{'XAUUSDT'}
            and all(type(p) is dict and type(p.get('quantity')) is str
                    and Decimal(p['quantity'])==0 for p in positions.values())
            and type(feedback['wallet_residual']) is str and Decimal(feedback['wallet_residual'])==0)
    except (InvalidOperation,KeyError,TypeError,ValueError):
        return False


def research_trigger(config,feedback,*,now):
    if feedback is None:return {'ready':False,'reason':'WAITING_EXECUTION_REVIEW','key':None}
    if (type(feedback) is not dict or type(now) is not int or now<0 or
        feedback.get('result_hash')!=digest({k:v for k,v in feedback.items() if k!='result_hash'}) or
        feedback.get('result_hash')!=config.get('execution_feedback_sha256') or
        feedback.get('schema') not in ('DEMO_EXECUTION_RESEARCH_FEEDBACK_V1','DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1') or
        feedback.get('environment')!='BINANCE_USDM_DEMO' or feedback.get('symbol')!='XAUUSDT' or
        feedback.get('scope')!='TECHNICAL_REVIEW_ONLY' or
        feedback.get('candidate_attribution')!='CONTENT_BOUND_TO_SIGNED_STRATEGY' or
        feedback.get('strategy_pnl') is not None or
        any(feedback.get(k) is not False for k in ('research_trigger_enabled','source_verified',
            'coverage_verified','economic_eligible','submission_enabled'))):
        raise ValueError('GOLD_FEEDBACK_SCOPE_OR_DIGEST')
    if feedback['schema']=='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1':
        _roundtrip(feedback)
    at=feedback.get('available_at_ms')
    if type(at) is not int or not 0<at<=8640000000000000:
        raise ValueError('GOLD_FEEDBACK_TIME')
    identity=validate_identity(feedback.get('research_identity'),as_of_ms=at,strategy_id=feedback.get('strategy_id'))
    if identity['base_input_sha256']!=config['input_sha256']:
        raise ValueError('GOLD_FEEDBACK_INPUT_MISMATCH')
    if at>now:return {'ready':False,'reason':'WAITING_EXECUTION_REVIEW_TIME','key':None}
    return {'ready':True,'reason':'EXECUTION_REVIEW_AVAILABLE','key':feedback['result_hash']}


def _roundtrip(feedback):
    fields={'reservation_sha256','proposal_sha256','resolution_plan_sha256','manifest_hash','journal_tail'}
    batches=feedback.get('batches')
    if (type(batches) is not list or not 2<=len(batches)<=32 or
        any(type(b) is not dict or set(b)!=fields or
            any(type(v) is not str or re.fullmatch('[a-f0-9]{64}',v) is None for v in b.values()) for b in batches) or
        any(len({b[k] for b in batches})!=len(batches) for k in fields) or
        any(k in feedback for k in fields) or feedback.get('position_flat') is not True):
        raise ValueError('GOLD_FEEDBACK_ROUNDTRIP_BINDING')
    try:
        positions=feedback['positions']
        if (type(positions) is not dict or set(positions)-{'XAUUSDT'} or
            any(Decimal(p['quantity'])!=0 for p in positions.values()) or
            Decimal(feedback['wallet_residual'])!=0):
            raise ValueError('GOLD_FEEDBACK_ROUNDTRIP_CASH')
    except (InvalidOperation,KeyError,TypeError):
        raise ValueError('GOLD_FEEDBACK_ROUNDTRIP_CASH') from None
