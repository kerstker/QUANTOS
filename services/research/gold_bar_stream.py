"""Causal closed-bar observations; no order, fill or profitability authority."""
from dataclasses import replace

from quantos.contracts.decimal_strings import nonnegative_decimal_string as _amount
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.gold_bar_candidates import Bar, CANDIDATES, evaluate


class GoldBarStream:
    def __init__(self, warmup):
        self.bars = list(warmup)
        evaluate(self.bars, candidate='channel', as_of_ms=0)
        self.last_received = max((b.available_ms for b in self.bars), default=0)
        self.blocked = False

    def ingest(self, raw, *, received_at_ms):
        if self.blocked:
            raise ValueError('BAR_STREAM_BLOCKED')
        try:
            return self._ingest(raw, received_at_ms)
        except (ValueError, TypeError, KeyError):
            self.blocked = True
            raise

    def _ingest(self, raw, now):
        if (type(raw) is not str or len(raw) > 131072 or type(now) is not int
                or now < self.last_received or now > 2**63 - 1):
            raise ValueError('INVALID_BAR_STREAM_RECEIPT')
        document = load_json_without_duplicate_keys(raw.encode())
        if (type(document) is not dict or set(document) != {'stream', 'data'}
                or document['stream'] != 'xauusdt@kline_15m'):
            raise ValueError('WRONG_BAR_STREAM')
        data = document['data']
        if (type(data) is not dict or data.get('e') != 'kline' or data.get('s') != 'XAUUSDT'
                or type(data.get('E')) is not int or not 0 <= now - data['E'] <= 30000):
            raise ValueError('INVALID_BAR_EVENT_CLOCK_OR_SYMBOL')
        k = data['k']
        if (type(k) is not dict or k.get('s') != 'XAUUSDT' or k.get('i') != '15m'
                or type(k.get('x')) is not bool):
            raise ValueError('INVALID_BAR_EVENT_FINALITY')
        self.last_received = now
        if not k['x']:
            return dict(status='OPEN_BAR', received_at_ms=now)
        if type(k.get('T')) is not int or k['T'] + 1 > data['E']:
            raise ValueError('PREMATURE_CLOSED_BAR')
        bar = Bar(k['t'], k['T'] + 1, now,
                  *(_amount(k[field], positive=True) for field in ('o', 'h', 'l', 'c')))
        bar.validate()
        for previous in reversed(self.bars):
            if previous.open_ms == bar.open_ms:
                if previous != replace(bar, available_ms=previous.available_ms):
                    raise ValueError('CONFLICTING_CLOSED_BAR')
                return dict(status='DUPLICATE_CLOSED_BAR', received_at_ms=now)
        if self.bars and bar.open_ms <= self.bars[-1].open_ms:
            raise ValueError('OUT_OF_ORDER_CLOSED_BAR')
        if len(self.bars) >= 20000:
            raise ValueError('BAR_OBSERVATION_BUDGET')
        self.bars.append(bar)
        decisions = [evaluate(self.bars, candidate=c, as_of_ms=now) for c in CANDIDATES]
        return dict(schema='GOLD_CLOSED_BAR_OBSERVATION_V1', status='CLOSED_BAR',
                    close_ms=bar.close_ms, event_ms=data['E'], received_at_ms=now,
                    decisions=decisions, submission_enabled=False, authoritative=False)
