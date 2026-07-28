"""Stable contracts for retained, replayable multi-asset factor evidence."""

from __future__ import annotations

from dataclasses import dataclass


RETAINED_FX_KEYS = (
    "AUD",
    "BRL",
    "CAD",
    "CHF",
    "CZK",
    "EUR",
    "GBP",
    "HUF",
    "ILS",
    "JPY",
    "KRW",
    "MXN",
    "NOK",
    "NZD",
    "PLN",
    "SEK",
    "SGD",
    "ZAR",
)


@dataclass(frozen=True)
class RetainedFactorSpec:
    """A deliberately small factor definition that independent code can replay."""

    factor_id: str
    signal_kind: str
    window: int
    holding_periods: tuple[int, ...]
    thesis: str

    def __post_init__(self) -> None:
        if not self.factor_id or self.signal_kind not in {"momentum", "negative_volatility"}:
            raise ValueError("retained factor id and supported signal kind are required")
        if type(self.window) is not int or self.window < 2:
            raise ValueError("retained factor window must be an integer >= 2")
        if (
            not self.holding_periods
            or self.holding_periods != tuple(sorted(set(self.holding_periods)))
            or self.holding_periods[0] != 1
            or any(type(value) is not int or value < 1 for value in self.holding_periods)
        ):
            raise ValueError("holding periods must be sorted unique positive integers starting at 1")
        if not self.thesis:
            raise ValueError("retained factor thesis is required")

    def as_dict(self) -> dict[str, object]:
        return {
            "factor_id": self.factor_id,
            "holding_periods": list(self.holding_periods),
            "signal_kind": self.signal_kind,
            "thesis": self.thesis,
            "window": self.window,
        }


RETAINED_FX_FACTOR_SPECS = (
    RetainedFactorSpec(
        factor_id="fx_momentum_120",
        signal_kind="momentum",
        window=120,
        holding_periods=(1, 5, 20),
        thesis=(
            "FX cross-sectional 120-observation momentum on PIT Frankfurter/ECB "
            "reference rates; diagnostic only, with no tradable-edge claim"
        ),
    ),
    RetainedFactorSpec(
        factor_id="fx_low_vol_60",
        signal_kind="negative_volatility",
        window=60,
        holding_periods=(1, 5, 20),
        thesis=(
            "FX cross-sectional low-volatility diagnostic: negative trailing "
            "60-observation realized volatility on PIT reference rates"
        ),
    ),
)


__all__ = ["RETAINED_FX_FACTOR_SPECS", "RETAINED_FX_KEYS", "RetainedFactorSpec"]
