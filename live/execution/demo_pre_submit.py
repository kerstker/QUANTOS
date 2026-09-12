"""Verify and persist a first Demo batch before any possible network effect.

No transport or credentials live here. INTENTS_PERSISTED is not an order receipt
or permission to bypass a final executor check. A failure after reservation never
refunds the grant; original client IDs remain available for query recovery.
The deployment must supply executor-controlled stores and independent issuance.
"""
from dataclasses import asdict
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path

from live.contracts import LiveOrderRequest
from live.execution import demo_authority
from live.execution.demo_current_kill import require_expected as require_current_kill
from live.execution.demo_batch_risk import screen, _digest, _json_value, _units, UNIT_SCALE
from live.governance.mandate import Mandate, AUTO, evaluate_mandate
from live.governance.validation_attestation import require_validation_attestation
from quantos.contracts.trading import OrderSide
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.research.demo_execution_origin import assess as assess_origin, ACCOUNT_TERMS_SCHEMA
from services.research.demo_origin_contract import contract_from_origin
from services.validation.demo_submission import batch_sha256, registry_sha256, validate_orders
from services.risk.pre_trade.context import order_fingerprint


class DemoPreparationRejected(ValueError):
    pass


@ledger_decimal_context()
def _venue_account_cap(orders,origin):
    if origin['schema']!=ACCOUNT_TERMS_SCHEMA:
        raise DemoPreparationRejected('DEMO_VENUE_ACCOUNT_TERMS_REQUIRED')
    data={r['kind']:r['data'] for r in origin['reads']}
    try:
        # Reuse the same checked finite bracket table as the receipt ledger;
        # a plausible last cap alone does not validate gaps or rate continuity.
        spec,_=contract_from_origin(origin)
        cap=Decimal(spec['new_risk_notional_cap'])
    except (ValueError,KeyError,TypeError,ArithmeticError):
        raise DemoPreparationRejected('DEMO_VENUE_ACCOUNT_TERMS_UNSUPPORTED') from None
    # Flat initial book; either side can fill alone. Opposite pending orders
    # cannot offset the possible exposure. Use the higher frozen valuation.
    mark=Decimal(data['mark']['markPrice'])
    exposure={side:sum((Decimal(o['quantity'])*max(Decimal(o['price']),mark)
        for o in orders if o['side']==side),Decimal(0)) for side in ('BUY','SELL')}
    if max(exposure.values())>cap:raise DemoPreparationRejected('DEMO_VENUE_NOTIONAL_CAP')


@ledger_decimal_context()
def _venue_rules(orders,origin):
    data={r['kind']:r['data'] for r in origin['reads']};rules=data['rules']
    count_kinds={'MAX_NUM_ORDERS','MAX_NUM_ALGO_ORDERS'}
    known={'LOT_SIZE','MARKET_LOT_SIZE','PRICE_FILTER','MIN_NOTIONAL','PERCENT_PRICE','POSITION_RISK_CONTROL'}|count_kinds
    if not set(rules['filter_types'])<=known:raise DemoPreparationRejected('DEMO_VENUE_FILTERS_INCOMPLETE')
    caps=rules.get('order_count_limits',{})
    if set(rules['filter_types']) & count_kinds != set(caps):raise DemoPreparationRejected('DEMO_VENUE_FILTERS_INCOMPLETE')
    if 'MAX_NUM_ORDERS' in caps and len(orders)>caps['MAX_NUM_ORDERS']:raise DemoPreparationRejected('DEMO_VENUE_ORDER_COUNT')
    if 'venue_filters' in rules:
        expected={'LOT_SIZE':'filterType minQty maxQty stepSize','MARKET_LOT_SIZE':'filterType minQty maxQty stepSize',
            'PRICE_FILTER':'filterType minPrice maxPrice tickSize','MIN_NOTIONAL':'filterType notional',
            'PERCENT_PRICE':'filterType multiplierDown multiplierUp multiplierDecimal',
            'MAX_NUM_ORDERS':'filterType limit','MAX_NUM_ALGO_ORDERS':'filterType limit',
            'POSITION_RISK_CONTROL':'filterType positionControlSide'}
        for term in rules['venue_filters']:
            if term['filterType'] not in expected or set(term)!=set(expected[term['filterType']].split()):
                raise DemoPreparationRejected('DEMO_VENUE_FILTERS_INCOMPLETE')
    if 'POSITION_RISK_CONTROL' in rules['filter_types']:
        control=[f for f in rules.get('venue_filters',[]) if f['filterType']=='POSITION_RISK_CONTROL']
        # Narrow engineering policy: only the explicitly observed neutral token.
        # Active, missing or unfamiliar controls require separate implementation.
        if len(control)!=1 or control[0].get('positionControlSide')!='NONE':
            raise DemoPreparationRejected('DEMO_VENUE_POSITION_CONTROL')
    filters={f['filterType']:f for f in rules['terms']['filters']}
    if len(filters)!=len(rules['terms']['filters']) or not {'LOT_SIZE','PRICE_FILTER','MIN_NOTIONAL'}<=set(filters):
        raise DemoPreparationRejected('DEMO_VENUE_FILTERS_INCOMPLETE')
    for order in orders:
        if order['type'] not in rules['order_types'] or order['timeInForce'] not in rules['time_in_forces']:
            raise DemoPreparationRejected('DEMO_VENUE_ORDER_SCOPE')
        quantity,price=Decimal(order['quantity']),Decimal(order['price'])
        lot,tick=filters['LOT_SIZE'],filters['PRICE_FILTER']
        if (not Decimal(lot['minQty'])<=quantity<=Decimal(lot['maxQty']) or (quantity-Decimal(lot['minQty']))%Decimal(lot['stepSize']) or
            not Decimal(tick['minPrice'])<=price<=Decimal(tick['maxPrice']) or (price-Decimal(tick['minPrice']))%Decimal(tick['tickSize']) or
            quantity*price<Decimal(filters['MIN_NOTIONAL']['notional'])):
            raise DemoPreparationRejected('DEMO_VENUE_ORDER_FILTER')
        band=filters.get('PERCENT_PRICE')
        if band:
            mark=Decimal(data['mark']['markPrice'])
            if not mark*Decimal(band['multiplierDown'])<=price<=mark*Decimal(band['multiplierUp']):
                raise DemoPreparationRejected('DEMO_VENUE_PRICE_BAND')


def _state(dispatch,recovery,oms):
    reserved=dispatch.snapshot();records=recovery.latest_records();orders=oms.orders()
    if any(r.state.value not in ('RESOLVED',) for r in records):raise DemoPreparationRejected('DEMO_RECOVERY_BLOCKED')
    if any(o['status'] not in ('FILLED','CANCELED','REJECTED','EXPIRED') for o in orders):
        raise DemoPreparationRejected('DEMO_OMS_NOT_TERMINAL')
    # Every committed reservation must be represented in recovery, including a
    # crash before the first OMS/recovery write. Missing does not mean unspent.
    recovered={r.client_order_id:r for r in records}
    for row in reserved:
        for order in row['payload']['orders']:
            if order['newClientOrderId'] not in recovered:raise DemoPreparationRejected('DEMO_RESERVED_ORDER_UNRECOVERED')
    return {'reservations':reserved,'recovery':[_json_value(asdict(r)) for r in records],'oms':orders,'wire':dispatch.wire_snapshot()}


def _risk_with_history(risk_inputs,history,now_ms):
    if any(type(o['submitted_at']) is not int or not 0<=o['submitted_at']<=now_ms for o in history):
        raise DemoPreparationRejected('DEMO_OMS_HISTORY_TIME')
    recent=[o for o in history if now_ms-o['submitted_at']<=risk_inputs['native_limits'].rate_window_ms]
    return dict(risk_inputs,
        recent_order_fingerprints=tuple(order_fingerprint(instrument_id=o['instrument_id'],side=o['side'],
            quantity=_units(str(o['quantity'])),limit_price=None if o['limit_price'] is None else o['limit_price']/UNIT_SCALE,
            strategy_id=o['strategy_id']) for o in recent),
        recent_timestamps=tuple(o['submitted_at'] for o in recent),
        recent_order_statuses=tuple(o['status'] for o in recent))


def proposal(*,orders,origin,risk_inputs,mandate,attestations,dispatch,recovery,oms,now_ms):
    """Build exact content for the independently controlled issuer to review."""
    try:
        registry=demo_authority.load_registry_for_executor()
        require_current_kill(risk_inputs['kill_switch'].state, now_ms=now_ms)
        orders=json.loads(json.dumps(validate_orders(orders),allow_nan=False))
        origin=json.loads(json.dumps(origin,allow_nan=False))
        origin_report=assess_origin(origin,as_of_ms=now_ms)
        if origin_report['status']!='OBSERVED_FLAT_QUOTE':raise DemoPreparationRejected('DEMO_ORIGIN_NOT_READY')
        if orders[0]['symbol']!=origin['symbol']:raise DemoPreparationRejected('DEMO_ORIGIN_ORDER_SCOPE')
        _venue_account_cap(orders,origin)
        _venue_rules(orders,origin)
        source={r['kind']:r['data'] for r in origin['reads']}
        account=risk_inputs['account'];market=risk_inputs['market']
        wallet=next(a for a in source['account_after']['assets'] if a['asset']==origin_report['quote_asset'])
        if (risk_inputs['now_ms']!=now_ms or account['account_ref_sha256']!=origin['account_ref_sha256'] or
            account['quote_asset']!=origin_report['quote_asset'] or account['positions'] or
            Decimal(account['cash'])!=Decimal(wallet['availableBalance']) or
            Decimal(account['equity'])!=Decimal(wallet['marginBalance'])):
            raise DemoPreparationRejected('DEMO_ORIGIN_RISK_ACCOUNT_BINDING')
        if (market['symbol']!=origin['symbol'] or Decimal(market['reference_price'])!=Decimal(source['mark']['markPrice']) or
            market['observed_at_ms']!=min(source['mark']['time'],source['book']['time'])):
            raise DemoPreparationRejected('DEMO_ORIGIN_RISK_MARKET_BINDING')
        state=_state(dispatch,recovery,oms)
        actual_risk_inputs=_risk_with_history(risk_inputs,state['oms'],now_ms)
        risk=screen(orders,**actual_risk_inputs)
        if risk['status']!='RISK_CHECKS_PASSED':raise DemoPreparationRejected('DEMO_BATCH_RISK_REJECTED')
        sid=risk_inputs['strategy'].strategy_id
        reason=require_validation_attestation(sid,attestations)
        if reason or attestations[sid].attested_at>now_ms:raise DemoPreparationRejected('DEMO_VALIDATION_MISSING')
        if (type(mandate) is not Mandate or not mandate.approved_by or
            any(name.upper() in ('AUTO','AI','SYSTEM') for name in mandate.approved_by) or
            not mandate.issued_at<=now_ms or mandate.expires_at is None or now_ms>=mandate.expires_at):
            raise DemoPreparationRejected('DEMO_MANDATE_MISSING')
        requests=[LiveOrderRequest(instrument_id=o['symbol'],side=OrderSide(o['side']),quantity=float(Decimal(o['quantity'])),
            client_order_id=o['newClientOrderId'],order_type='LIMIT',limit_price=float(Decimal(o['price']))) for o in orders]
        decision=evaluate_mandate(requests,{origin['symbol']:float(Decimal(market['reference_price']))},mandate,
                                  venue='testnet',exchange='binance',now=now_ms,current_positions={})
        if decision.tier!=AUTO:raise DemoPreparationRejected('DEMO_MANDATE_LIMIT')
        old_ids={o['newClientOrderId'] for r in state['reservations'] for o in r['payload']['orders']}
        if any(o['newClientOrderId'] in old_ids for o in orders):raise DemoPreparationRejected('DEMO_CLIENT_ID_SPENT')
        context={'deployment_sha256':registry['deployment_sha256'],'executor_boot_sha256':demo_authority.executor_boot_sha256(),
            'account_ref_sha256':origin['account_ref_sha256'],'origin_sha256':_digest(origin),
            'market_sha256':_digest([r for r in origin['reads'] if r['kind'] in ('mark','book','rules','fees','brackets')]),
            'risk_sha256':risk['risk_sha256'],'recovery_sha256':_digest(state),'batch_sha256':batch_sha256(orders),
            'mandate_sha256':_digest(asdict(mandate)),
            'validation_sha256':_digest({sid:asdict(attestations[sid])}),'registry_sha256':registry_sha256(registry)}
        return {'schema':'DEMO_PRE_SUBMISSION_PROPOSAL_V1','context':context,'orders':orders,'origin':origin,
                'risk':risk,'mandate':_json_value(asdict(mandate)),'validation':{sid:asdict(attestations[sid])},
                'state':state,'strategy_id':sid,'submission_enabled':False,'economic_authority':False}
    except DemoPreparationRejected:
        raise
    except Exception:
        raise DemoPreparationRejected('DEMO_PREPARATION_INPUT_INVALID') from None


def _retain(directory,proposal):
    directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    data=(json.dumps(proposal,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    if len(data)>16_000_000:raise DemoPreparationRejected('DEMO_PREPARATION_EVIDENCE_BUDGET')
    path=directory/(_digest(proposal)+'.json')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if path.is_symlink() or path.read_bytes()!=data:raise DemoPreparationRejected('DEMO_PREPARATION_EVIDENCE_CONFLICT') from None
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return path


@contextmanager
def _preparation_lease(dispatch):
    """Serialize state checks and all intent barriers across executor processes.

    A separate open description per call makes flock effective for threads too.
    Deployment custody must prevent replacement of the state inode. Reservation
    and recovery still block all subsequent preparations when this lease ends.
    """
    fd=os.open(dispatch.path,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise DemoPreparationRejected('DEMO_EXECUTOR_BUSY') from None
        yield
    finally:
        os.close(fd)


def prepare(envelope_bytes, *,evidence_directory,verification_now_ms=None,**kwargs):
    """Persist every possible client ID before returning; never sends or refunds."""
    try:
        with _preparation_lease(kwargs['dispatch']):
            return _prepare_locked(envelope_bytes,evidence_directory=evidence_directory,
                verification_now_ms=verification_now_ms,**kwargs)
    except DemoPreparationRejected:
        raise
    except Exception:
        raise DemoPreparationRejected('DEMO_PREPARATION_STOPPED_QUERY_ORIGINAL_IDS') from None


def _prepare_locked(envelope_bytes, *,evidence_directory,verification_now_ms=None,**kwargs):
    try:
        decision_at=kwargs['now_ms']
        verified_at=decision_at if verification_now_ms is None else verification_now_ms
        if type(verified_at) is not int or not 0<=verified_at-decision_at<=30000:
            raise DemoPreparationRejected('DEMO_PREPARATION_VERIFICATION_TIME')
        frozen=proposal(**kwargs)
        require_current_kill(kwargs['risk_inputs']['kill_switch'].state,now_ms=verified_at)
        if assess_origin(frozen['origin'],as_of_ms=verified_at)['status']!='OBSERVED_FLAT_QUOTE':
            raise DemoPreparationRejected('DEMO_PREPARATION_ORIGIN_STALE')
        demo_authority.verify_for_executor(envelope_bytes,expected_context=frozen['context'],orders=frozen['orders'],now_ms=verified_at)
        path=_retain(evidence_directory,frozen)
        reservation=kwargs['dispatch'].reserve(envelope_bytes,expected_context=frozen['context'],orders=frozen['orders'],now_ms=verified_at)
        for order in frozen['orders']:
            kwargs['recovery'].register_unknown(client_order_id=order['newClientOrderId'],
                deployment_id=frozen['context']['deployment_sha256'],instrument_id=order['symbol'],side=order['side'],
                quantity=float(Decimal(order['quantity'])),now_ms=verified_at,reason='DEMO_INTENT_PERSISTED_BEFORE_NETWORK')
            kwargs['oms'].record_submission(order['newClientOrderId'],order['symbol'],OrderSide(order['side']),float(Decimal(order['quantity'])),
                strategy_id=frozen['strategy_id'],submitted_at=verified_at,status='PENDING_NEW',
                order_type='LIMIT',limit_price=float(Decimal(order['price'])))
        return {'schema':'DEMO_PREPARED_BATCH_V1','status':'INTENTS_PERSISTED','reservation_sha256':reservation['record_hash'],
                'proposal_sha256':_digest(frozen),'proposal_path':str(path),'orders':frozen['orders'],
                'context':frozen['context'],'submission_enabled':False,'economic_authority':False}
    except DemoPreparationRejected:
        raise
    except Exception:
        raise DemoPreparationRejected('DEMO_PREPARATION_STOPPED_QUERY_ORIGINAL_IDS') from None
