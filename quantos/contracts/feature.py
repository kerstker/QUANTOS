"""Feature domain entities (blueprint §6.2).

Feature definitions, values (bitemporal — no lookahead), registry, and the
results of feature validation / leakage detection.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

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


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    dtype: FeatureDType = FeatureDType.FLOAT
    formula: str = ""
    inputs: tuple[str, ...] = ()   # upstream feature/data ids


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
