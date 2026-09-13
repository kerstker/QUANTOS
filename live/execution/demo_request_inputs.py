"""Build an original Demo request from observed inputs and installed declarations.

This is input preparation, not issuance. Existing actor labels and validation
materials are copied exactly; no approval, risk reset or network action occurs.
"""
from decimal import Decimal
import json

from live.execution import demo_pre_submit,demo_position_context
from live.execution.demo_batch_risk import _digest,_fields
from live.execution.demo_issuer_policy import validate_policy,require,amount
from live.execution.demo_session_inputs import decode
from quantos.contracts.binance_demo_account import _decimal
from quantos.contracts.killswitch import KillSwitchState
from services.execution.perpetual_ledger_v2 import ledger_decimal_context,canonical_decimal
from services.research import demo_market_inputs
from services.research.demo_execution_origin import assess as assess_origin
from services.validation.demo_submission import validate_orders
from services.research.demo_oms_status import _time


def profile_check(profile,policy,market,now_ms):
    validate_policy(policy)
    _fields(profile,'schema environment symbol market_source_sha256 volatility_policy native_limits strategy mandate attestations '
        'initial_day_pnl initial_peak_equity minimum_plan_turnover')
    require(profile['schema']=='DEMO_REQUEST_PROFILE_V1' and profile['environment']=='BINANCE_USDM_DEMO' and
        profile['symbol']==policy['scope']['symbol']==market['symbol'],'REQUEST_PROFILE_SCOPE')
    require(policy['valid_from_ms']<=now_ms<policy['valid_until_ms'],'REQUEST_POLICY_EXPIRED')
    require(profile['market_source_sha256']==market['source_sha256'] and
        profile['volatility_policy']==demo_market_inputs.VOLATILITY_POLICY,'REQUEST_MARKET_SOURCE_OR_METHOD')
    bindings=policy['bindings']
    for name,key in (('native_limits','native_limits_sha256'),('strategy','strategy_approval_sha256'),
                     ('mandate','mandate_sha256'),('attestations','validation_sha256')):
        require(_digest(profile[name])==bindings[key],'REQUEST_POLICY_BINDING')
    require(profile['strategy']['strategy_id']==bindings['strategy_id'],'REQUEST_STRATEGY_BINDING')
    _decimal(profile['initial_day_pnl'])
    require(Decimal(_decimal(profile['initial_peak_equity']))>=0,'REQUEST_INITIAL_PEAK')


@ledger_decimal_context()
def order_scope(orders,policy):
    validate_orders(orders)
    scope=policy['scope'];total=Decimal(0)
    for order in orders:
        require(order['symbol']==scope['symbol'] and order['side'] in scope['sides'] and
            order['timeInForce'] in scope['time_in_force'] and (scope['allow_opening'] or order['reduceOnly']),'REQUEST_ORDER_SCOPE')
        qty,price=amount(order['quantity']),amount(order['price']);total+=qty*price
        require(qty<=amount(scope['max_quantity_per_order']) and qty*price<=amount(scope['max_notional_per_order']),'REQUEST_ORDER_CAP')
    require(total<=amount(scope['max_notional_per_grant']),'REQUEST_BATCH_CAP')


def task_check(task,*,policy,source_commit,now_ms):
    _fields(task,'schema source_commit valid_from_ms valid_until_ms issuer_policy_sha256 profile orders kill_switch')
    require(task['schema']=='DEMO_REQUEST_TASK_V1' and task['source_commit']==source_commit==policy['executor_release_commit'],
        'REQUEST_TASK_SOURCE')
    for key in ('valid_from_ms','valid_until_ms'):_time(task[key])
    require(policy['valid_from_ms']<=task['valid_from_ms']<=now_ms<task['valid_until_ms']<=policy['valid_until_ms'],
        'REQUEST_TASK_WINDOW')
    require(task['issuer_policy_sha256']==_digest(policy),'REQUEST_TASK_POLICY')
    profile=task['profile']
    profile_check(profile,policy,{'source_sha256':profile['market_source_sha256'],'symbol':policy['scope']['symbol']},now_ms)
    order_scope(task['orders'],policy)
    # An explicit expected snapshot is checked against the installed log before
    # capture and again by the original preparation/sending checks. No defaults.
    _fields(task['kill_switch'],' '.join(KillSwitchState.model_fields))
    KillSwitchState.model_validate_json(json.dumps(task['kill_switch']),strict=True)
    return task


@ledger_decimal_context()
def build(*,orders,origin,market_inputs,profile,issuer_policy,kill_state,dispatch,recovery,oms,now_ms):
    profile_check(profile,issuer_policy,market_inputs,now_ms)
    market=demo_market_inputs.assess(market_inputs,as_of_ms=now_ms)
    require(market['status']=='OBSERVED','REQUEST_MARKET_UNAVAILABLE')
    report=assess_origin(origin,as_of_ms=now_ms)
    require(origin['symbol']==profile['symbol'] and origin['account_ref_sha256']==issuer_policy['account_ref_sha256'],
            'REQUEST_ACCOUNT_SCOPE')
    order_scope(orders,issuer_policy)
    require(type(kill_state) is KillSwitchState,'REQUEST_KILL_STATE')
    state=demo_pre_submit._state(dispatch,recovery,oms)
    source={r['kind']:r['data'] for r in origin['reads']}
    wallet=next(a for a in source['account_after']['assets'] if a['asset']==report['quote_asset'])
    account={'account_ref_sha256':origin['account_ref_sha256'],'quote_asset':report['quote_asset'],
        'cash':wallet['availableBalance'],'equity':wallet['marginBalance'],'positions':[],
        'day_pnl':profile['initial_day_pnl'],
        'peak_equity':canonical_decimal(max(Decimal(profile['initial_peak_equity']),Decimal(wallet['marginBalance'])))}
    position=None
    if state['reservations'] or report['status']!='OBSERVED_FLAT_QUOTE':
        position=demo_position_context.load_context(state,symbol=origin['symbol'],account_ref=origin['account_ref_sha256'],as_of_ms=now_ms)
        actual=demo_position_context.validate_origin(origin,position,as_of_ms=now_ms)
        account.update(actual)
        account['peak_equity']=canonical_decimal(max(Decimal(position['initial_equity']),Decimal(position['peak_equity_floor']),Decimal(account['equity'])))
    # The existing executor independently reloads the original history and
    # enforces accumulated turnover, cash and held-position restrictions again.
    request={'schema':'DEMO_EXECUTION_SESSION_INPUT_V1','orders':orders,'origin':origin,
        'risk':{'account':account,'market':{'symbol':origin['symbol'],
            'reference_price':source['mark']['markPrice'],'observed_at_ms':min(source['mark']['time'],source['book']['time']),
            'daily_volume_base':market['daily_volume_base'],'instrument_vol':market['instrument_vol'],
            'known':True,'market_open':True},'native_limits':profile['native_limits'],'strategy':profile['strategy'],
            'kill_switch':kill_state.model_dump(mode='json'),'plan_turnover':profile['minimum_plan_turnover']},
        'mandate':profile['mandate'],'attestations':profile['attestations']}
    raw=json.dumps(request,separators=(',',':'),allow_nan=False).encode()
    parsed=decode(raw,now_ms=now_ms)
    check=demo_pre_submit.proposal(**parsed,dispatch=dispatch,recovery=recovery,oms=oms)
    # Own serialized copy prevents callers mutating a returned request after its
    # digest was calculated. No precheck proposal is sent as a live grant.
    request=json.loads(raw)
    return {'schema':'DEMO_REQUEST_BUILD_V1','status':'PREPARED_INPUT_ONLY','observed_at_ms':now_ms,
        'request':request,'request_sha256':_digest(request),'origin_sha256':_digest(origin),
        'market_inputs_sha256':_digest(market_inputs),'profile_sha256':_digest(profile),
        'issuer_policy_sha256':_digest(issuer_policy),'precheck_status':check['risk']['status'],
        'position_context_sha256':None if position is None else _digest(position),
        'submission_enabled':False,'economic_authority':False}
