"""Bounded, exact Demo order-query observations; never submission authority."""
from decimal import Decimal
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator
from quantos.contracts.binance_demo import StrictModel, DecimalText, DEMO_REST_BASE

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Time = Annotated[int, Field(ge=0, le=8_640_000_000_000_000)]
OrderId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")]
VenueId = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")]
ClientId = Annotated[str, Field(pattern=r"^[.A-Z:/a-z0-9_-]{1,36}$")]
QueryReason = Literal['ORDER_QUERY_SUCCEEDED', 'ACCOUNT_NOT_REQUESTED', 'CREDENTIALS_MISSING',
    'REQUEST_FAILED', 'RESPONSE_INVALID', 'CLOCK_UNRELIABLE', 'RATE_LIMITED', 'AUTHENTICATION_REJECTED',
    'REDIRECT_REFUSED', 'RESPONSE_TOO_LARGE', 'REQUEST_TIMEOUT', 'PREVIOUS_QUERY_FAILED',
    'ORDER_NOT_FOUND_UNCONFIRMED', 'IDENTITY_MISMATCH']


def fixed_order_bindings(actions, bindings):
    by_id = {b['order_id']:b for b in bindings}
    return [dict(order_id=a['order_id'], client_order_id=by_id[a['order_id']]['client_order_id'],
                 venue_order_id=by_id[a['order_id']]['venue_order_id'], side=a['side'],
                 quantity=a['quantity'], price=a['price'], submitted_at_ms=a['timestamp'])
            for a in actions if a['order_id'] in by_id]


def query_binding_hash(orders, account_ref_sha256):
    body = {'orders':orders, 'account_ref_sha256':account_ref_sha256}
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class FixedQueryOrder(StrictModel):
    order_id: OrderId
    client_order_id: ClientId
    venue_order_id: VenueId
    side: Literal['BUY', 'SELL']
    quantity: DecimalText
    price: DecimalText
    submitted_at_ms: Time

    @model_validator(mode='after')
    def amounts(self):
        if Decimal(self.quantity) <= 0 or Decimal(self.price) <= 0 or int(self.venue_order_id) > 2**63-1:
            raise ValueError('invalid fixed order')
        return self


class QueryPlan(StrictModel):
    environment: Literal['BINANCE_USDM_DEMO'] = 'BINANCE_USDM_DEMO'
    manifest_hash: Hash
    journal_tail: Hash
    account_ref_sha256: Hash
    symbol: Literal['BTCUSDT', 'BTCUSDC']
    receipt_observed_at_ms: Time | None
    orders: list[FixedQueryOrder] = Field(min_length=1, max_length=10)

    @model_validator(mode='after')
    def unique(self):
        if any(len({getattr(o, key) for o in self.orders}) != len(self.orders)
               for key in ('order_id', 'client_order_id', 'venue_order_id')):
            raise ValueError('duplicate fixed query identity')
        return self


class OrderObservation(StrictModel):
    status: Literal['NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH']
    executed_quantity: DecimalText
    created_at_ms: Time
    updated_at_ms: Time

    @model_validator(mode='after')
    def causal(self):
        if Decimal(self.executed_quantity) < 0 or self.updated_at_ms < self.created_at_ms:
            raise ValueError('invalid order observation')
        return self


class QueryResult(StrictModel):
    order_id: OrderId
    outcome: Literal['FOUND', 'UNKNOWN', 'NOT_REQUESTED', 'SKIPPED']
    reason: QueryReason
    requested_at_ms: Time
    received_at_ms: Time
    observation: OrderObservation | None = None
    response_sha256: Hash | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.received_at_ms < self.requested_at_ms:
            raise ValueError('invalid query interval')
        if self.outcome == 'FOUND':
            if self.observation is None or self.response_sha256 is None or self.reason != 'ORDER_QUERY_SUCCEEDED':
                raise ValueError('found order requires bound observation')
        elif self.observation is not None or self.response_sha256 is not None or self.reason == 'ORDER_QUERY_SUCCEEDED':
            raise ValueError('unknown query cannot assert an observation')
        return self


class QueryCapture(StrictModel):
    schema_version: Literal['BINANCE_DEMO_ORDER_QUERIES_V1'] = 'BINANCE_DEMO_ORDER_QUERIES_V1'
    rest_base: Literal['https://demo-fapi.binance.com'] = DEMO_REST_BASE
    source_verified: Literal[False] = False
    submission_enabled: Literal[False] = False
    provenance: Literal['OPERATOR_DEMO_REST', 'SYNTHETIC_TEST_FIXTURE']
    plan: QueryPlan
    started_at_ms: Time
    completed_at_ms: Time
    clock_offset_ms: Annotated[int, Field(ge=-2000, le=2000)] | None = None
    queries: list[QueryResult] = Field(min_length=1, max_length=10)

    @model_validator(mode='after')
    def bound(self):
        if not 0 <= self.completed_at_ms-self.started_at_ms <= 60_000:
            raise ValueError('invalid query batch interval')
        if [q.order_id for q in self.queries] != [o.order_id for o in self.plan.orders]:
            raise ValueError('queries must exactly cover fixed plan')
        for fixed, query in zip(self.plan.orders, self.queries):
            if not self.started_at_ms <= query.requested_at_ms <= query.received_at_ms <= self.completed_at_ms:
                raise ValueError('query outside batch')
            if query.observation is not None:
                obs=query.observation
                qty=Decimal(obs.executed_quantity); target=Decimal(fixed.quantity)
                if (self.clock_offset_ms is None or not fixed.submitted_at_ms <= obs.created_at_ms <= obs.updated_at_ms <= query.received_at_ms+self.clock_offset_ms
                    or qty > target or obs.status=='FILLED' and qty!=target
                    or obs.status=='PARTIALLY_FILLED' and not 0 < qty < target
                    or obs.status=='NEW' and qty!=0):
                    raise ValueError('invalid fixed order state or clock')
        return self
