"""Observed historical settlements, not forecasts or authenticated schedules."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re

from quantos.contracts.decimal_strings import nonnegative_decimal_string as _amount
from services.execution.perpetual_ledger_v2 import FundingEvent
from services.governance.json_integrity import load_json_without_duplicate_keys


def load_funding(raw, *, start_ms, end_ms, requested_at_ms, received_at_ms):
    if (type(raw) is not bytes or len(raw) > 2_000_000
            or any(type(t) is not int or not 0 <= t <= 2**63 - 1
                   for t in (start_ms, end_ms, requested_at_ms, received_at_ms))
            or not start_ms < end_ms <= requested_at_ms <= received_at_ms):
        raise ValueError('INVALID_FUNDING_RECEIPT')
    rows = load_json_without_duplicate_keys(raw)
    # A full page may be truncated. Require an explicit pagination path instead.
    if type(rows) is not list or len(rows) >= 1000:
        raise ValueError('FUNDING_PAGE_INCOMPLETE_OR_INVALID')
    events = []
    for row in rows:
        if (type(row) is not dict
                or set(row) - {'rateType'} != {'symbol', 'fundingTime', 'fundingRate', 'markPrice'}
                or row.get('rateType', 'Regular') != 'Regular'
                or row['symbol'] != 'XAUUSDT' or type(row['fundingTime']) is not int
                or not start_ms <= row['fundingTime'] < end_ms
                or type(row['fundingRate']) is not str or len(row['fundingRate']) > 64
                or re.fullmatch(r'-?(?:0|[1-9][0-9]*)?\.[0-9]+|-?(?:0|[1-9][0-9]*)',
                                row['fundingRate']) is None):
            raise ValueError('INVALID_FUNDING_ROW')
        event = FundingEvent(row['fundingTime'], 'XAUUSDT', Decimal(row['fundingRate']),
                             _amount(row['markPrice'], positive=True))
        if events and event.timestamp <= events[-1].timestamp:
            raise ValueError('FUNDING_DUPLICATE_OR_ORDER')
        events.append(event)
    return tuple(events)


@dataclass(frozen=True)
class ObservedFundingSettlement:
    """Public settlement fact with its actual receipt clock, not account cash."""
    event: FundingEvent
    requested_at_ms: int
    available_at_ms: int
    source_sha256: str

    def __post_init__(self):
        if (type(self.event) is not FundingEvent
                or any(type(t) is not int or not 0 <= t <= 2**63 - 1
                       for t in (self.requested_at_ms, self.available_at_ms))
                or not self.event.timestamp <= self.requested_at_ms <= self.available_at_ms
                or type(self.source_sha256) is not str
                or re.fullmatch('[a-f0-9]{64}', self.source_sha256) is None):
            raise ValueError('INVALID_OBSERVED_FUNDING_CLOCK_OR_SOURCE')

    def simulation_frame(self):
        """Preserve both clocks for the explicit cancel-and-pause research policy.

        Callers must bind the raw receipt in dataset_identity and independently
        supply a policy covering the actual settlement schedule; this does not
        manufacture schedule knowledge or certify account cash.
        """
        from services.execution.perpetual_simulation import SimulationFrame
        return SimulationFrame(self.event.timestamp, market_events=(self.event,),
                               funding_available_at=self.available_at_ms)

    def as_document(self):
        return dict(schema='GOLD_OBSERVED_FUNDING_SETTLEMENT_V1',
            instrument_id=self.event.instrument_id, settlement_at_ms=self.event.timestamp,
            requested_at_ms=self.requested_at_ms, available_at_ms=self.available_at_ms,
            funding_rate=str(self.event.funding_rate), mark_price=str(self.event.mark_price),
            source_sha256=self.source_sha256, account_cash_verified=False,
            historical_publication_time_known=False)


def load_funding_observations(raw, *, start_ms, end_ms, requested_at_ms, received_at_ms):
    events = load_funding(raw, start_ms=start_ms, end_ms=end_ms,
        requested_at_ms=requested_at_ms, received_at_ms=received_at_ms)
    digest = hashlib.sha256(raw).hexdigest()
    return tuple(ObservedFundingSettlement(event, requested_at_ms, received_at_ms, digest)
                 for event in events)
