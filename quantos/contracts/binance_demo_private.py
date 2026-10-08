"""Private connection evidence is separate from orders and cash reconciliation."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from quantos.contracts.binance_demo import StrictModel, Timestamp


class PrivateCheckStep(StrictModel):
    name: Literal['credentials', 'public', 'account', 'BTCUSDT_orders', 'BTCUSDT_trades',
                  'BTCUSDC_orders', 'BTCUSDC_trades', 'listen_key', 'websocket', 'worker']
    status: Literal['PASSED', 'FAILED']
    reason: Literal['CHECK_SUCCEEDED', 'REQUEST_FAILED', 'RESPONSE_INVALID',
                    'CREDENTIAL_FILE_INVALID', 'CREDENTIAL_FILE_PERMISSIONS',
                    'HISTORY_PAGE_INCOMPLETE', 'CLOCK_UNRELIABLE', 'SYMBOL_RULES_INVALID',
                    'AUTHENTICATION_REJECTED', 'RATE_LIMITED', 'REDIRECT_REFUSED',
                    'RESPONSE_TOO_LARGE', 'REQUEST_TIMEOUT', 'ENDPOINT_NOT_ALLOWED',
                    'WEBSOCKET_CHECK_FAILED']
    rows: Annotated[int, Field(ge=0, le=1000)] | None = None

    @model_validator(mode='after')
    def reason_matches_status(self):
        if (self.status == 'PASSED') != (self.reason == 'CHECK_SUCCEEDED'):
            raise ValueError('step reason contradicts result')
        return self


class DemoPrivateCheck(StrictModel):
    schema_version: Literal['binance-demo-private-check.v1'] = 'binance-demo-private-check.v1'
    environment: Literal['BINANCE_USDM_DEMO'] = 'BINANCE_USDM_DEMO'
    observed_at_ms: Timestamp
    completed_at_ms: Timestamp
    connectivity_status: Literal['NOT_REQUESTED', 'CHECKS_PASSED', 'FAILED']
    steps: list[PrivateCheckStep] = Field(default_factory=list, max_length=10)
    websocket_connections: Annotated[int, Field(ge=0, le=2)] = 0
    websocket_pongs: Annotated[int, Field(ge=0, le=2)] = 0
    websocket_events: Annotated[int, Field(ge=0, le=200)] = 0
    event_delivery_status: Literal['UNKNOWN', 'EVENTS_OBSERVED'] = 'UNKNOWN'
    submission_status: Literal['BLOCKED'] = 'BLOCKED'
    submission_reason: Literal['LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE'] = 'LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE'
    ledger_status: Literal['UNKNOWN'] = 'UNKNOWN'
    authoritative: Literal[False] = False
    source_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]

    @model_validator(mode='after')
    def consistent(self):
        if not 0 <= self.completed_at_ms - self.observed_at_ms <= 130_000:
            raise ValueError('invalid private check window')
        names = [s.name for s in self.steps]
        if len(set(names)) != len(names): raise ValueError('duplicate check stage')
        if self.websocket_pongs > self.websocket_connections:
            raise ValueError('pong without a connection')
        if self.connectivity_status == 'NOT_REQUESTED' and (
                self.steps or self.websocket_connections or self.websocket_pongs or self.websocket_events):
            raise ValueError('unrequested checks contain observations')
        if self.connectivity_status == 'CHECKS_PASSED':
            if (names != ['credentials', 'public', 'account', 'BTCUSDT_orders', 'BTCUSDT_trades',
                          'BTCUSDC_orders', 'BTCUSDC_trades', 'listen_key', 'websocket']
                    or any(s.status != 'PASSED' for s in self.steps)
                    or self.websocket_connections != 2 or self.websocket_pongs != 2):
                raise ValueError('incomplete connectivity checks')
        if self.connectivity_status == 'FAILED' and not any(s.status == 'FAILED' for s in self.steps):
            raise ValueError('missing failed check')
        if (self.websocket_events > 0) != (self.event_delivery_status == 'EVENTS_OBSERVED'):
            raise ValueError('event evidence mismatch')
        return self
