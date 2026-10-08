"""Feature domain entities (blueprint §6.2).

Feature definitions, values (bitemporal — no lookahead), registry, and the
results of feature validation / leakage detection.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .temporal import BitemporalRecord


class FeatureDType(str, Enum):
    FLOAT = "FLOAT"
    INT = "INT"
    CATEGORICAL = "CATEGORICAL"
    BOOL = "BOOL"


class FeatureStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    DEPRECATED = "DEPRECATED"


class FactorFamily(str, Enum):
    """727 §6.4 因子家族(最少支持的 13 个;不为凑数而加无经济解释的家族)。"""
    PRICE_TREND = "PRICE_TREND"
    PRICE_MEAN_REVERSION = "PRICE_MEAN_REVERSION"
    VOLATILITY = "VOLATILITY"
    VOLUME = "VOLUME"
    LIQUIDITY = "LIQUIDITY"
    ORDER_FLOW = "ORDER_FLOW"
    POSITIONING_CROWDING = "POSITIONING_CROWDING"
    OPEN_INTEREST = "OPEN_INTEREST"
    FUNDING_CARRY = "FUNDING_CARRY"
    BASIS = "BASIS"
    CROSS_VENUE = "CROSS_VENUE"
    REGIME = "REGIME"
    DEFENSIVE_OVERLAY = "DEFENSIVE_OVERLAY"


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    dtype: FeatureDType = FeatureDType.FLOAT
    formula: str = ""
    inputs: tuple[str, ...] = ()   # upstream feature/data ids

    # --- 727 §6.1 Factor Garden fields (F1, 2026-07-27) --------------------
    # All OPTIONAL with defaults: the pre-F1 constructor shape is unchanged and
    # tests/contracts/test_data_feature.py pins the old behavior byte-for-byte.
    factor_family_id: Optional[str] = None        # FactorFamily value when set
    expression_hash: str = ""                     # alpha_id() of the canonical DSL expr
    economic_hypothesis: str = ""                 # §8.1: a factor without a story is forbidden
    required_datasets: tuple[str, ...] = ()
    minimum_lag: Optional[int] = None             # periods the expression looks back
    frequency: str = ""
    universe: str = ""
    normalization: str = ""
    neutralization: str = ""
    winsorization: str = ""
    missing_policy: str = ""
    expected_turnover: Optional[float] = None
    known_failure_regimes: tuple[str, ...] = ()
    creator_actor: str = ""
    creator_model: str = ""
    prompt_hash: str = ""
    created_at_ms: int = 0                        # caller-supplied; NEVER a wall-clock default

    @field_validator("factor_family_id")
    @classmethod
    def _factor_family_known(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in {f.value for f in FactorFamily}:
            raise ValueError(f"unknown factor family: {value!r}")
        return value


class FeatureValue(BitemporalRecord):
    feature_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    value: float | None = None


class FeatureSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    set_id: str = Field(min_length=1)
    feature_ids: tuple[str, ...] = ()
    version: str = "v1"


class FeatureRegistryItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    definition: FeatureDefinition
    version: str = "v1"
    owner: str = ""
    status: FeatureStatus = FeatureStatus.DRAFT


class LeakageCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_id: str = Field(min_length=1)
    passed: bool
    detail: str = ""


class FeatureValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_id: str = Field(min_length=1)
    passed: bool
    leakage: LeakageCheckResult | None = None
    notes: str = ""
