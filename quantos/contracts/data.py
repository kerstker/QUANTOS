"""Data domain entities (blueprint §6.1).

Market data primitives + dataset versioning. Price bars and ticks are
bitemporal (no-lookahead by construction). All models are frozen and validated.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .temporal import BitemporalRecord


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FX = "FX"
    CRYPTO = "CRYPTO"
    INDEX = "INDEX"


class VolumeUnit(str, Enum):
    BASE_ASSET = "BASE_ASSET"
    QUOTE_USD = "QUOTE_USD"
    SHARES = "SHARES"
    CONTRACTS = "CONTRACTS"


class VolumeSemantics(str, Enum):
    PER_BAR = "PER_BAR"
    ROLLING_24H = "ROLLING_24H"


class CorporateActionType(str, Enum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    MERGER = "MERGER"
    SPINOFF = "SPINOFF"
    RIGHTS = "RIGHTS"


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    asset_class: AssetClass
    exchange: str = ""
    currency: str = "CNY"
    multiplier: float = Field(default=1.0, gt=0)
    is_active: bool = True


class TradingCalendar(BaseModel):
    model_config = ConfigDict(frozen=True)

    calendar_id: str = Field(min_length=1)
    timezone: str = "Asia/Shanghai"
    trading_days: tuple[str, ...] = ()  # ISO dates (YYYY-MM-DD)

    def is_trading_day(self, iso_date: str) -> bool:
        return iso_date in self.trading_days


class Bar(BitemporalRecord):
    instrument_id: str = Field(min_length=1)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _check_ohlc(self) -> "Bar":
        if self.high < self.low:
            raise ValueError("high < low")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("high/low do not bound open/close")
        return self


class Tick(BitemporalRecord):
    instrument_id: str = Field(min_length=1)
    price: float = Field(gt=0)
    size: float = Field(default=0.0, ge=0)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)


class CorporateAction(BaseModel):
    """A forward-looking announcement (NOT a bitemporal observation): the
    announcement (`knowledge_time`) typically PRECEDES the ex/effective moment
    (`event_time`), so the knowledge>=event invariant deliberately does not
    apply here. PIT-correctness is enforced at adjustment time by only applying
    actions whose knowledge_time <= the query's as_of."""
    model_config = ConfigDict(frozen=True)

    instrument_id: str = Field(min_length=1)
    action_type: CorporateActionType
    knowledge_time: int = Field(ge=0)   # when the action became knowable (announcement)
    event_time: int = Field(ge=0)       # ex / effective moment
    ex_date: str                        # ISO date the action takes effect
    ratio: float = Field(default=1.0, gt=0)   # e.g. split ratio
    amount: float = Field(default=0.0, ge=0)  # e.g. dividend per share


class DatasetVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    created_at: int = Field(ge=0)
    row_count: int = Field(default=0, ge=0)
    content_hash: str = ""
    source: str = ""
    point_in_time: bool = True  # survivorship-bias-free / as-of correct


class DataQualityCheck(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    passed: bool
    detail: str = ""


class DataQualityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(min_length=1)
    version: str = ""
    missing_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    out_of_order_count: int = Field(default=0, ge=0)
    checks: tuple[DataQualityCheck, ...] = ()

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)
