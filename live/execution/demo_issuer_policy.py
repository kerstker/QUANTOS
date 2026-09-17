"""Review a deployment-owned Demo handoff against an installed finite policy.

Pure checks over declared inputs. The runtime establishes custody and a private
root-owned handoff; this module cannot authenticate a caller, approve a human
mandate, sign, or replace the executor's fresh original-store checks.
"""
from decimal import Decimal
import json
import re

from live.execution.demo_batch_risk import _digest, _fields, screen
from live.execution.demo_session_inputs import _limits, _mandate
from live.execution.demo_pre_submit import _require_origin, _venue_rules, _venue_account_cap
from live.execution import demo_position_context
from live.governance.validation_attestation import ValidationAttestation, require_validation_attestation
from quantos.contracts.killswitch import KillSwitchState
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.risk.pre_trade.context import StrategyApproval
from services.risk.pre_trade.kill_switch import KillSwitch
from services.validation import demo_submission as submission, demo_cancellation as cancellation


class IssuerRejected(ValueError):
    pass


def require(condition,reason):
    if not condition:raise IssuerRejected(reason)


def amount(value):
    require(type(value) is str and re.fullmatch(r'\d{1,20}(\.\d{1,18})?',value) is not None,
            'ISSUER_EXACT_AMOUNT')
    result=Decimal(value)
    require(result>0,'ISSUER_POSITIVE_AMOUNT')
    return result


def validate_policy(policy):
    _fields(policy,'schema environment policy_id deployment_sha256 registry_sha256 executor_release_commit '
        'account_ref_sha256 authority_id key_id valid_from_ms valid_until_ms grant_ttl_ms max_grants '
        'max_orders max_total_notional max_cancel_grants max_cancel_orders scope bindings')
    require(policy['schema']=='DEMO_ISSUER_POLICY_V1' and policy['environment']=='BINANCE_USDM_DEMO','ISSUER_POLICY_SCOPE')
    for name in ('policy_id','authority_id','key_id'):submission._identifier(policy[name])
    for name in ('deployment_sha256','registry_sha256','account_ref_sha256'):submission._digest(policy[name])
    require(type(policy['executor_release_commit']) is str and re.fullmatch('[a-f0-9]{40}',policy['executor_release_commit']) is not None,'ISSUER_POLICY_RELEASE')
    for name in ('valid_from_ms','valid_until_ms','grant_ttl_ms','max_grants','max_orders','max_cancel_grants','max_cancel_orders'):
        submission._integer(policy[name])
    require(0<policy['valid_until_ms']-policy['valid_from_ms']<=7*86400000 and
            0<policy['grant_ttl_ms']<=60000,'ISSUER_POLICY_TIME')
    for name in ('max_grants','max_cancel_grants'):require(0<policy[name]<=4096,'ISSUER_POLICY_BUDGET')
    for name in ('max_orders','max_cancel_orders'):require(0<policy[name]<=50000,'ISSUER_POLICY_BUDGET')
    amount(policy['max_total_notional'])
    scope=policy['scope'];_fields(scope,'symbol sides time_in_force allow_opening max_quantity_per_order max_notional_per_order max_notional_per_grant')
    require(scope['symbol'] in ('BTCUSDC','BTCUSDT') and type(scope['allow_opening']) is bool,'ISSUER_POLICY_ORDER_SCOPE')
    for name,allowed in (('sides',{'BUY','SELL'}),('time_in_force',{'GTX','IOC'})):
        value=scope[name]
        require(type(value) is list and 1<=len(value)<=2 and all(type(v) is str for v in value) and
                len(set(value))==len(value) and set(value)<=allowed,'ISSUER_POLICY_ORDER_SCOPE')
    for name in ('max_quantity_per_order','max_notional_per_order','max_notional_per_grant'):amount(scope[name])
    bindings=policy['bindings'];_fields(bindings,'strategy_id mandate_sha256 validation_sha256 native_limits_sha256 strategy_approval_sha256')
    submission._identifier(bindings['strategy_id'])
    for name in set(bindings)-{'strategy_id'}:submission._digest(bindings[name])
    submission._canonical(policy)
    return policy


def current_key(policy,registry,now_ms):
    validate_policy(policy);submission.validate_registry(registry);submission._integer(now_ms)
    require(policy['valid_from_ms']<=now_ms<policy['valid_until_ms'],'ISSUER_POLICY_EXPIRED')
    require(policy['deployment_sha256']==registry['deployment_sha256'] and
            policy['registry_sha256']==submission.registry_sha256(registry),'ISSUER_POLICY_REGISTRY')
    keys=[k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(policy['authority_id'],policy['key_id'])]
    require(len(keys)==1 and keys[0]['status']=='ACTIVE','ISSUER_KEY_UNAVAILABLE')
    key=keys[0]
    require(key['valid_from_ms']<=now_ms<key['valid_until_ms'],'ISSUER_KEY_EXPIRED')
    return key


def historical_policy_binding(policy,registry):
    """Identity checks for read-only recovery; no active key or send authority."""
    validate_policy(policy);submission.validate_registry(registry)
    require(policy['deployment_sha256']==registry['deployment_sha256'] and
        policy['registry_sha256']==submission.registry_sha256(registry),'ISSUER_POLICY_REGISTRY')
    keys=[k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(policy['authority_id'],policy['key_id'])]
    require(len(keys)==1,'ISSUER_KEY_UNAVAILABLE')


def handoff_proposal(handoff,purpose):
    _fields(handoff,'schema proposal_sha256 proposal_path proposal status submission_enabled')
    prefix='DEMO_EXECUTION_SESSION' if purpose=='submission' else 'DEMO_CANCELLATION_SESSION'
    require(handoff['schema']==prefix+'_PROPOSAL_V1' and handoff['status']=='WAITING_INDEPENDENT_GRANT' and
            handoff['submission_enabled'] is False,'ISSUER_HANDOFF_SCOPE')
    submission._digest(handoff['proposal_sha256'])
    # A path printed by the executor is metadata, never a file opened by issuer.
    require(type(handoff['proposal_path']) is str and 0<len(handoff['proposal_path'])<=4096,'ISSUER_HANDOFF_PATH')
    require(_digest(handoff['proposal'])==handoff['proposal_sha256'],'ISSUER_HANDOFF_DIGEST')
    return handoff['proposal']


def common_context(context,policy):
    for name in ('deployment_sha256','registry_sha256','account_ref_sha256'):
        require(context[name]==policy[name],'ISSUER_CONTEXT_POLICY')


def _risk_replay(proposal,policy,now_ms):
    risk=proposal['risk'];binding=risk['input_snapshot'];context=proposal['context']
    require(risk['schema']=='DEMO_EXACT_BATCH_RISK_V1' and risk['status']=='RISK_CHECKS_PASSED' and
            not risk['reasons'] and all(risk[k] is False for k in ('source_verified','submission_enabled','economic_authority')),'ISSUER_RISK_REJECTED')
    require(_digest(binding)==risk['risk_sha256']==context['risk_sha256'],'ISSUER_RISK_DIGEST')
    declared=binding['context'];at=declared['now']
    submission._integer(at)
    require(0<=now_ms-at<=30000 and binding['orders']==proposal['orders'],'ISSUER_RISK_TIME_OR_ORDERS')
    pinned=policy['bindings']
    require(_digest(binding['native_limits'])==pinned['native_limits_sha256'] and
            _digest(binding['strategy'])==pinned['strategy_approval_sha256'],'ISSUER_RISK_POLICY')
    strategy=StrategyApproval.model_validate(binding['strategy'],strict=True)
    require(strategy.strategy_id==proposal['strategy_id']==pinned['strategy_id'] and strategy.approved,'ISSUER_STRATEGY_SCOPE')
    stop=KillSwitchState.model_validate_json(json.dumps(binding['kill_switch']),strict=True)
    replay=screen(proposal['orders'],account=binding['account'],market=binding['market'],
        native_limits=_limits(binding['native_limits']),strategy=strategy,kill_switch=KillSwitch(stop),now_ms=at,
        **{name:declared[name] for name in ('recent_order_fingerprints','recent_timestamps','recent_order_statuses','plan_turnover')})
    for name in ('status','risk_sha256','input_snapshot','checks','subset_checks','reasons','batch_turnover_fraction'):
        require(replay[name]==risk[name],'ISSUER_RISK_REPLAY_MISMATCH')
    # The original state remains owned by executor; its final check reads it
    # again. Here its supplied bytes, origin and market must be exactly bound.
    require(_digest(proposal['state'])==context['recovery_sha256'],'ISSUER_RECOVERY_DIGEST')
    origin=proposal['origin'];position=proposal['state'].get('position_context')
    require(_digest(origin)==context['origin_sha256'] and origin['account_ref_sha256']==context['account_ref_sha256'],'ISSUER_ORIGIN_DIGEST')
    require(_digest([r for r in origin['reads'] if r['kind'] in ('mark','book','rules','fees','brackets')])==context['market_sha256'],'ISSUER_MARKET_DIGEST')
    report=_require_origin(origin,now_ms=now_ms,position_context=position)
    _venue_rules(proposal['orders'],origin);_venue_account_cap(proposal['orders'],origin,position)
    data={r['kind']:r['data'] for r in origin['reads']}
    wallet=next(a for a in data['account_after']['assets'] if a['asset']==report['quote_asset'])
    account,market=binding['account'],binding['market']
    require(account['account_ref_sha256']==context['account_ref_sha256'] and account['quote_asset']==report['quote_asset'] and
            Decimal(account['cash'])==Decimal(wallet['availableBalance']) and Decimal(account['equity'])==Decimal(wallet['marginBalance']),
            'ISSUER_ACCOUNT_BINDING')
    if position is None:
        require(account['positions']==[],'ISSUER_POSITION_BINDING')
    else:
        expected=demo_position_context.validate_origin(origin,position,as_of_ms=now_ms)
        require(account['positions']==expected['positions'] and Decimal(account['day_pnl'])==Decimal(expected['day_pnl']),
                'ISSUER_POSITION_BINDING')
        require(Decimal(account['peak_equity'])>=max(Decimal(position['initial_equity']),Decimal(account['equity']),
                Decimal(position['peak_equity_floor'])),'ISSUER_PEAK_EQUITY_BINDING')
    require(market['symbol']==origin['symbol'] and Decimal(market['reference_price'])==Decimal(data['mark']['markPrice']) and
            market['observed_at_ms']==min(data['mark']['time'],data['book']['time']),'ISSUER_MARKET_BINDING')
    return at,min(origin['observed_at_ms'],market['observed_at_ms'])+30000


@ledger_decimal_context()
def review_submission(handoff,*,policy,registry,now_ms):
    key=current_key(policy,registry,now_ms);p=handoff_proposal(handoff,'submission')
    _fields(p,'schema context orders origin risk mandate validation state strategy_id submission_enabled economic_authority')
    require(p['schema']=='DEMO_PRE_SUBMISSION_PROPOSAL_V1' and p['submission_enabled'] is False and p['economic_authority'] is False,'ISSUER_PROPOSAL_SCOPE')
    context=submission.validate_context(p['context']);orders=submission.validate_orders(p['orders']);common_context(context,policy)
    require(context['batch_sha256']==submission.batch_sha256(orders),'ISSUER_BATCH_DIGEST')
    for name in ('mandate','validation'):
        require(_digest(p[name])==context[name+'_sha256']==policy['bindings'][name+'_sha256'],'ISSUER_APPROVED_BINDING')
    mandate=_mandate(p['mandate'])
    require(mandate.venue=='testnet' and mandate.exchange=='binance' and mandate.approved_by and
            all(name.upper() not in ('AUTO','AI','SYSTEM') for name in mandate.approved_by) and
            mandate.issued_at<=now_ms<mandate.expires_at,'ISSUER_MANDATE_SCOPE')
    require(set(p['validation'])=={p['strategy_id']},'ISSUER_VALIDATION_SCOPE')
    attestations={sid:ValidationAttestation(**value) for sid,value in p['validation'].items()}
    require(not require_validation_attestation(p['strategy_id'],attestations) and
            attestations[p['strategy_id']].attested_at<=now_ms,'ISSUER_VALIDATION_SCOPE')
    _,fresh_until=_risk_replay(p,policy,now_ms)
    scope=policy['scope'];total=Decimal(0)
    for order in orders:
        require(order['symbol']==scope['symbol'] and order['side'] in scope['sides'] and
                order['timeInForce'] in scope['time_in_force'] and (scope['allow_opening'] or order['reduceOnly']),'ISSUER_ORDER_SCOPE')
        quantity,price=amount(order['quantity']),amount(order['price']);notional=quantity*price
        require(quantity<=amount(scope['max_quantity_per_order']) and notional<=amount(scope['max_notional_per_order']),'ISSUER_ORDER_BUDGET')
        total+=notional
    require(total<=amount(scope['max_notional_per_grant']),'ISSUER_GRANT_BUDGET')
    expiry=min(now_ms+policy['grant_ttl_ms'],policy['valid_until_ms'],key['valid_until_ms'],mandate.expires_at,fresh_until)
    require(expiry>now_ms,'ISSUER_GRANT_EXPIRED')
    return {'purpose':'submission','proposal_sha256':handoff['proposal_sha256'],'context':context,
        'orders':orders,'commands':[],'issued_at_ms':now_ms,'expires_at_ms':expiry,'notional':format(total,'f')}


def review_cancellation(handoff,*,policy,registry,history,now_ms):
    key=current_key(policy,registry,now_ms);p=handoff_proposal(handoff,'cancellation')
    _fields(p,'schema context commands query_record_hashes new_order_submission_enabled economic_authority')
    require(p['schema']=='DEMO_CANCEL_PROPOSAL_V1' and p['new_order_submission_enabled'] is False and
            p['economic_authority'] is False,'ISSUER_CANCEL_SCOPE')
    context=cancellation.validate_context(p['context']);commands=cancellation.validate_commands(p['commands'])
    common_context(context,policy)
    require(context['cancel_batch_sha256']==cancellation.batch_sha256(commands),'ISSUER_CANCEL_BATCH')
    queries=p['query_record_hashes']
    require(type(queries) is list and len(queries)==len(commands) and len(set(queries))==len(queries),'ISSUER_CANCEL_QUERY_BINDING')
    for digest in queries:submission._digest(digest)
    signed={r['proposal_sha256'] for r in history if r['kind']=='SIGNED'}
    originals=[r['payload']['plan'] for r in history if r['kind']=='RESERVED' and r['proposal_sha256'] in signed and
        r['payload']['plan']['purpose']=='submission' and
        r['payload']['plan']['context']['batch_sha256']==context['original_batch_sha256'] and
        r['payload']['policy']['account_ref_sha256']==policy['account_ref_sha256'] and
        r['payload']['policy']['deployment_sha256']==policy['deployment_sha256']]
    require(len(originals)==1,'ISSUER_CANCEL_ORIGINAL_NOT_ISSUED')
    original={o['newClientOrderId']:o for o in originals[0]['orders']}
    for command in commands:
        order=original.get(command['origClientOrderId'])
        require(order is not None and order['symbol']==command['symbol']==policy['scope']['symbol'],'ISSUER_CANCEL_ORIGINAL_SCOPE')
    expiry=min(now_ms+policy['grant_ttl_ms'],policy['valid_until_ms'],key['valid_until_ms'])
    require(expiry>now_ms,'ISSUER_GRANT_EXPIRED')
    # Query freshness/venue IDs come from the deployment-owned executor pipe;
    # original executor checks them again immediately before its single DELETE.
    return {'purpose':'cancellation','proposal_sha256':handoff['proposal_sha256'],'context':context,
        'orders':[],'commands':commands,'issued_at_ms':now_ms,'expires_at_ms':expiry,'notional':'0'}
