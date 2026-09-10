"""Fixed-action passive replay through the canonical order/risk/Decimal account.

The venue book and account evolve on venue time. Public receipt time is retained
separately, and no adaptive strategy callback can inspect the venue-only state.
Queue admission/fill acknowledgements come from the same account used by the
aggressive simulator, rather than a second PnL calculator or a post-hoc filter.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import re

from services.execution.backtest_authority import exact_identity_hash
from services.execution.passive_execution_input import ExecutionTape, BookUpdate
from services.execution.passive_queue import Action, Depth, Trade, AccountingFill, replay_queue
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.execution.perpetual_simulation import (
    ZERO, OrderIntent, OrderState, CancelOrder, SimulationFrame, SimulationCostModel,
    SimulationLimits, _SimulationAccount, _validate_market_environment, _canonical,
)

SCHEMA = 'PASSIVE_LINEAR_PERPETUAL_ACCOUNT_V1'


@dataclass(frozen=True)
class VenueBookQuote:
    """Modeled exchange book; deliberately not a client ExecutableQuote."""
    instrument_id: str
    event_time: int
    bid_price: Decimal
    ask_price: Decimal
    bid_quantity: Decimal
    ask_quantity: Decimal
    venue_id: str
    public_available_time: int
    source_sequence: int
    source_kind: str
    information_scope: str = 'EXCHANGE_INTERNAL_FIXED_ACTIONS_ONLY'


class _QueueLedger:
    def __init__(self, account, frames, intents, symbol):
        self.account,self.frames,self.intents,self.symbol=account,frames,intents,symbol
        self.cursor=0
        self.sequence=0

    def advance(self, now):
        while self.cursor<len(self.frames) and self.frames[self.cursor].timestamp<=now:
            self.account.apply_market(self.frames[self.cursor]);self.cursor+=1

    def observe_book(self, now, bids, asks, event):
        self.sequence+=1
        if not bids or not asks:
            self.account.quotes.pop(self.symbol,None)
            return
        bid,ask=max(bids),min(asks)
        if bid>=ask:raise ValueError('crossed venue quote')
        available=event.public_available_ms if isinstance(event,Depth) else event.received_ms
        if available is None or available<now:raise ValueError('missing public receipt time')
        q=VenueBookQuote(self.symbol,now,bid,ask,bids[bid],asks[ask],
            self.account.policies[self.symbol].venue_id,available,self.sequence,
            'DEPTH' if isinstance(event,Depth) else 'TRADE_DEPLETION_MODEL')
        self.account.quotes[self.symbol]=q

    def admit(self, order_id, now):
        self.account.apply_actions(now,(self.intents[order_id],))
        return self.account.orders[order_id].reason

    def reject(self, order_id, now, reason):
        intent=self.intents[order_id]
        self.account.transition(OrderState(intent,now,intent.quantity_delta),now,'REJECTED',reason)

    def cancel(self, order_id, now):
        self.account.apply_actions(now,(CancelOrder(order_id),))

    def expire(self, order_id, now):
        state=self.account.pending[order_id]
        self.account.transition(state,now,'EXPIRED','TIME_IN_FORCE')

    def fill(self, order_id, now, price, quantity):
        before=self.account.pending[order_id]
        used=self.account.fill_order(before,now,price,quantity,liquidity_role='MAKER')
        after=self.account.orders[order_id]
        return AccountingFill(used,after.status,after.reason)


def _validate_tape(tape):
    if type(tape) is not ExecutionTape or tape.symbol not in ('BTCUSDT','BTCUSDC'):
        raise ValueError('one compiled BTC execution tape required')
    if (any(type(t) is not int for t in (tape.start_ms,tape.end_ms))
            or not 0<=tape.start_ms<tape.end_ms or tape.end_ms-tape.start_ms>150000
            or not isinstance(tape.input_hash,str) or not re.fullmatch('[0-9a-f]{64}',tape.input_hash)):
        raise ValueError('bounded frozen tape identity required')
    books,trades=tuple(tape.books),tuple(tape.trades)
    if not books or not trades or len(books)+len(trades)>40000:
        raise ValueError('bounded complete venue inputs required')
    if any(type(b) is not BookUpdate or type(b.available_ms) is not int or b.available_ms<b.transaction_ms for b in books):
        raise ValueError('invalid depth clocks')
    if books[0].available_ms>tape.start_ms or min(books[-1].transaction_ms,trades[-1].transaction_time_ms)<tape.end_ms:
        raise ValueError('tape does not cover the replay window')
    if any(t.symbol!=tape.symbol for t in trades):raise ValueError('mixed public trade contracts')
    if any(not isinstance(t.normal_quantity,Decimal) or not t.normal_quantity.is_finite()
            or not ZERO<=t.normal_quantity<=t.quantity for t in trades):
        raise ValueError('explicit normal trade quantity excluding RPI required')
    return books,trades


@ledger_decimal_context()
def run_passive_perpetual_simulation(*, release_identity, dataset_identity, tape, actions,
        contract_rules, funding_policies, initial_collateral, account_frames,
        order_rule_profiles, costs, limits=None, order_latency_ms=None,
        cancel_latency_ms=0, ack_latency_ms=0, risk_policy=None, risk_policy_identity=None):
    """Replay fixed client actions; no private orders, promotion or authority."""
    release_identity,dataset_identity,risk_policy_identity=copy.deepcopy(
        (release_identity,dataset_identity,risk_policy_identity))
    for name,value in (('release',release_identity),('dataset',dataset_identity)):
        if not isinstance(value,Mapping) or not value:raise ValueError('frozen identity required')
        exact_identity_hash(value,name=name)
    if (risk_policy is None)!=(risk_policy_identity is None):raise ValueError('risk policy identity required')
    if risk_policy is not None:
        if not callable(risk_policy) or not isinstance(risk_policy_identity,Mapping) or not risk_policy_identity:
            raise ValueError('typed risk policy and identity required')
        exact_identity_hash(risk_policy_identity,name='risk_policy')
    if not isinstance(costs,SimulationCostModel):raise ValueError('explicit cost model required')
    if costs.adverse_slippage_rate!=ZERO:
        raise ValueError('passive limit fills cannot embed adverse price slippage')
    limits=limits or SimulationLimits()
    if not isinstance(limits,SimulationLimits):raise ValueError('simulation limits required')
    if order_latency_ms is not None:limits=replace(limits,order_latency_ms=order_latency_ms)
    if order_rule_profiles is None:raise ValueError('complete order rule profiles required')
    profiles_tuple=tuple(order_rule_profiles)
    if not profiles_tuple:raise ValueError('complete order rule profiles required')
    books,trades=_validate_tape(tape)
    frame_items=tuple(account_frames)
    if not frame_items or any(type(f) is not SimulationFrame or f.quotes for f in frame_items):
        raise ValueError('venue mark/funding/capacity frames without client quotes required')
    times=tuple(f.timestamp for f in frame_items)
    if times!=tuple(sorted(set(times))) or times[0]>tape.start_ms or times[-1]>tape.end_ms:
        raise ValueError('account events must cover initialization and remain inside the tape')
    if times[-1]<tape.end_ms:frame_items+= (SimulationFrame(tape.end_ms),)
    rule_items,policy_items=tuple(contract_rules),tuple(funding_policies)
    rules,policies,profiles=_validate_market_environment(rule_items,policy_items,profiles_tuple,frame_items)
    if set(rules)!={tape.symbol}:raise ValueError('account and tape universe must match exactly')
    spec=rules[tape.symbol].spec;profile=profiles[tape.symbol]
    # Incompatible public quantities cannot be rounded to manufacture a fill.
    for b in books:
        for p,q in (*b.bids,*b.asks):
            if profile.price_reason(p) or q%spec.quantity_step!=ZERO:
                raise ValueError('public depth violates frozen venue rules')
    for t in trades:
        if profile.price_reason(t.price) or t.quantity%spec.quantity_step!=ZERO or t.normal_quantity%spec.quantity_step!=ZERO:
            raise ValueError('public trade violates frozen venue rules')
    action_items=tuple(actions)
    if len(action_items)>200:raise ValueError('at most 200 fixed actions required')
    queue_actions=[];intents={}
    for item in action_items:
        if not isinstance(item,(tuple,list)) or len(item)!=2:raise ValueError('timestamp/action pairs required')
        at,action=item
        if type(action) is OrderIntent:
            if action.instrument_id!=tape.symbol or action.limit_price is None:
                raise ValueError('fixed post-only limit intents in the declared contract required')
            if action.order_id in intents:raise ValueError('duplicate fixed order identity')
            tape.visible_book(at)
            intents[action.order_id]=action
            queue_actions.append(Action(at,'ORDER',action.order_id,'BUY' if action.quantity_delta>ZERO else 'SELL',
                abs(action.quantity_delta),action.limit_price,action.expires_at))
        elif type(action) is CancelOrder:queue_actions.append(Action(at,'CANCEL',action.order_id))
        else:raise ValueError('frozen intents or cancellations required')
    market=[Depth(b.transaction_ms,b.bids,b.asks,b.bootstrap,b.available_ms) for b in books]
    market.extend(Trade(t.transaction_time_ms,t.aggregate_id,t.price,t.normal_quantity,
        'BUY' if t.aggressor_side=='SELL' else 'SELL',t.received_at_ms) for t in trades if t.normal_quantity>ZERO)
    account=_SimulationAccount(rule_items=rule_items,rules=rules,policies=policies,profiles=profiles,
        initial_collateral=initial_collateral,costs=costs,limits=limits,risk_policy=risk_policy)
    queue=replay_queue(market,queue_actions,start_ms=tape.start_ms,end_ms=tape.end_ms,
        bid_floor=tape.bid_floor,ask_ceiling=tape.ask_ceiling,order_latency_ms=limits.order_latency_ms,
        cancel_latency_ms=cancel_latency_ms,ack_latency_ms=ack_latency_ms,
        max_book_age_ms=min(1000,limits.quote_max_age_ms),
        _accounting=_QueueLedger(account,frame_items,intents,tape.symbol))
    for order in queue['orders']:
        state=account.orders.get(order['order_id'])
        if state is None:
            if order['status']!='SENT':raise ValueError('missing canonical order')
        elif (state.status!=order['status'] or abs(state.filled_quantity)!=Decimal(order['filled_quantity'])
              or abs(state.remaining_quantity)!=Decimal(order['remaining_quantity'])):
            raise ValueError('queue and canonical ledger quantity/state mismatch')
    inputs={'schema':SCHEMA,'release':release_identity,'dataset':dataset_identity,'tape':asdict(tape),
        'actions':[(t,asdict(a)) for t,a in action_items],'initial_collateral':initial_collateral,
        'rules':[r.as_canonical_dict() for r in rule_items],'funding_policies':[p.as_canonical_dict() for p in policy_items],
        'order_rule_profiles':[asdict(p) for p in profiles_tuple],
        'account_frames':[{'timestamp':f.timestamp,'market_events':[asdict(e) for e in f.market_events],
            'capacity':[c.as_canonical_dict() for c in f.capacity_snapshots]} for f in frame_items],
        'limits':asdict(limits),'costs':asdict(costs),'cancel_latency_ms':cancel_latency_ms,
        'ack_latency_ms':ack_latency_ms,'risk_policy':risk_policy_identity,'queue_model':queue['model'],
        'tie_policy':queue['tie_policy']}
    result={'schema_version':SCHEMA,'input_hash':exact_identity_hash(inputs,name='passive_account_inputs'),
        'evidence_status':'TECHNICAL_VALIDATION_ONLY','execution_model':'PASSIVE_QUEUE_CANONICAL_LEDGER_V1',
        'authoritative':False,'economic_eligible':False,'ledger_applied':True,'queue_model_calibrated':False,
        'venue_receipts_verified':False,'actual_venue_fees':None,'full_research_risk_9_of_9':False,
        'risk_history_source':'LEDGER_EVENT_REPLAY','queue':queue,'order_rule_profiles':[asdict(p) for p in profiles_tuple],
        'authority_blockers':['EXTERNAL_SIGNED_RAW_ARCHIVE_AUTHORITY_MISSING','PRIVATE_VENUE_FILL_CALIBRATION_MISSING',
            'INDEPENDENT_ECONOMIC_VALIDATION_REQUIRED'],**account.snapshot()}
    result['trade_volume_policy']='BINANCE_NORMAL_QUANTITY_NQ_EXCLUDES_RPI_V1'
    result=_canonical(result)
    result['result_hash']=exact_identity_hash(result,name='passive_account_result')
    return result
