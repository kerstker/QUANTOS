"""Read-only Demo observations. A receipt is never execution authority."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEMO_REST_BASE = "https://demo-fapi.binance.com"
DEMO_WEBSOCKET_BASE = "wss://demo-fstream.binance.com"
DEMO_SYMBOLS = ("BTCUSDT", "BTCUSDC")
MAX_AGE_MS = 120_000
MAX_RESPONSE_BYTES = 2_000_000
DecimalText = Annotated[str, Field(max_length=64, pattern=r"^-?\d{1,30}(\.\d{1,24})?$")]
Timestamp = Annotated[int, Field(ge=1)]
Reason = Literal["PUBLIC_API_REACHABLE", "ACCOUNT_READ_SUCCEEDED", "ACCOUNT_NOT_REQUESTED",
                 "CREDENTIALS_MISSING", "PUBLIC_CHECK_FAILED", "REQUEST_FAILED",
                 "RESPONSE_INVALID", "CLOCK_UNRELIABLE", "SYMBOL_RULES_INVALID",
                 "RATE_LIMITED", "AUTHENTICATION_REJECTED", "REDIRECT_REFUSED",
                 "RESPONSE_TOO_LARGE", "REQUEST_TIMEOUT"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DemoSymbol(StrictModel):
    symbol: Literal["BTCUSDT", "BTCUSDC"]
    quote_asset: Literal["USDT", "USDC"]
    margin_asset: Literal["USDT", "USDC"]
    contract_type: Literal["PERPETUAL"]
    status: Literal["TRADING"]
    tick_size: DecimalText
    step_size: DecimalText
    min_qty: DecimalText
    min_notional: DecimalText

    @model_validator(mode="after")
    def consistent_quote(self):
        from decimal import Decimal
        if self.symbol != "BTC" + self.quote_asset or self.margin_asset != self.quote_asset:
            raise ValueError("symbol/quote/margin mismatch")
        if any(Decimal(v) <= 0 for v in (self.tick_size, self.step_size, self.min_qty, self.min_notional)):
            raise ValueError("invalid Demo rules")
        return self


class DemoAsset(StrictModel):
    asset: Annotated[str, Field(pattern=r"^[A-Z0-9]{1,16}$")]
    wallet_balance: DecimalText
    available_balance: DecimalText
    unrealized_profit: DecimalText


class DemoPosition(StrictModel):
    symbol: Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,32}$")]
    position_side: Literal["BOTH", "LONG", "SHORT"]
    quantity: DecimalText
    unrealized_profit: DecimalText


class PublicObservation(StrictModel):
    status: Literal["READY", "FAILED"]
    reason: Reason
    server_time_ms: Timestamp | None = None
    round_trip_ms: Annotated[int, Field(ge=0)] | None = None
    clock_offset_ms: int | None = None
    symbols: list[DemoSymbol] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def check_ready(self):
        if self.status == "READY":
            if (self.reason != "PUBLIC_API_REACHABLE" or self.server_time_ms is None
                or self.round_trip_ms is None or self.round_trip_ms > 5000
                or self.clock_offset_ms is None or abs(self.clock_offset_ms) > 2000
                or [s.symbol for s in self.symbols] != list(DEMO_SYMBOLS)):
                raise ValueError("incomplete public observation")
        elif self.symbols or self.reason == "PUBLIC_API_REACHABLE":
            raise ValueError("failed observation cannot claim readiness")
        return self


class AccountObservation(StrictModel):
    status: Literal["NOT_CHECKED", "READY", "FAILED", "SKIPPED"]
    reason: Reason
    observed_at_ms: Timestamp | None = None
    assets: list[DemoAsset] = Field(default_factory=list, max_length=500)
    positions: list[DemoPosition] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def check_ready(self):
        if self.status == "NOT_CHECKED" and self.reason != "ACCOUNT_NOT_REQUESTED":
            raise ValueError("unchecked account reason mismatch")
        if self.status == "SKIPPED" and self.reason != "PUBLIC_CHECK_FAILED":
            raise ValueError("skipped account reason mismatch")
        if self.status == "READY":
            if self.reason != "ACCOUNT_READ_SUCCEEDED" or self.observed_at_ms is None:
                raise ValueError("missing account observation")
            if len({a.asset for a in self.assets}) != len(self.assets):
                raise ValueError("duplicate assets")
            if len({(p.symbol, p.position_side) for p in self.positions}) != len(self.positions):
                raise ValueError("duplicate positions")
        elif self.assets or self.positions or self.observed_at_ms is not None:
            raise ValueError("unchecked account cannot contain balances")
        return self


class DemoReceipt(StrictModel):
    schema_version: Literal["binance-demo-readiness.v1"] = "binance-demo-readiness.v1"
    environment: Literal["BINANCE_USDM_DEMO"] = "BINANCE_USDM_DEMO"
    rest_base: Literal["https://demo-fapi.binance.com"] = DEMO_REST_BASE
    observed_at_ms: Timestamp
    completed_at_ms: Timestamp
    public: PublicObservation
    account: AccountObservation
    submission_enabled: Literal[False] = False
    authoritative: Literal[False] = False

    @model_validator(mode="after")
    def check_time(self):
        if not 0 <= self.completed_at_ms - self.observed_at_ms <= 60_000:
            raise ValueError("invalid capture interval")
        if self.account.status == "READY":
            if (self.public.status != "READY" or self.account.observed_at_ms is None
                or not self.observed_at_ms <= self.account.observed_at_ms <= self.completed_at_ms):
                raise ValueError("unbound account observation")
        return self
