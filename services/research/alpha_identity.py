"""Canonical, immutable identity for structured research alphas.

This module only validates and serializes definitions.  It never evaluates an
expression, imports a model dynamically, or touches data/network state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping

from services.research.alpha_expression import _CS, _CS2, _EW1, _EW2, _TS_PAIR, _TS_WINDOW

# Versions are intentionally explicit: an identity must not silently change
# when a caller supplies an arbitrary estimator name or implementation path.
MODEL_REGISTRY = {
    "gbm": frozenset({"1", "1.0"}),
    "ridge": frozenset({"1", "1.0"}),
    "xgb": frozenset({"1", "1.0"}),
}
_OPERATOR_ARITIES = {
    "field": 2,
    "const": 2,
    "signed_power": 3,
    **{name: 3 for name in _TS_WINDOW},
    **{name: 2 for name in _CS},
    **{name: 3 for name in _CS2},
    **{name: 2 for name in _EW1},
    **{name: 3 for name in _EW2},
    **{name: 4 for name in _TS_PAIR},
}


def _number(value: Any, label: str) -> None:
    if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{label} must be a finite int or float, not {value!r}")


def _freeze(value: Any, path: str = "value") -> Any:
    """Deep-freeze only JSON values; mappings become immutable mappings."""
    if value is None or type(value) is str or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if type(value) in (list, tuple):
        return tuple(_freeze(item, f"{path}[{i}]") for i, item in enumerate(value))
    raise TypeError(f"{path} contains unsupported value {value!r}")


def _validate_expression(expr: Any, path: str = "definition") -> None:
    if type(expr) not in (tuple, list) or not expr:
        raise TypeError(f"{path} must be a non-empty operator tuple/list")
    head = expr[0]
    if type(head) is not str or head not in _OPERATOR_ARITIES:
        raise ValueError(f"{path} has unregistered operator {head!r}")
    if len(expr) != _OPERATOR_ARITIES[head]:
        raise ValueError(f"{path} operator {head!r} has invalid arity")
    if head == "field":
        if type(expr[1]) is not str:
            raise TypeError(f"{path} field name must be a string")
        return
    if head == "const":
        _number(expr[1], f"{path} constant")
        return
    if head == "signed_power":
        _validate_expression(expr[1], f"{path}[1]")
        _number(expr[2], f"{path} exponent")
        return
    if head in _TS_WINDOW or head in _TS_PAIR:
        children = expr[1:-1]
        for i, child in enumerate(children, 1):
            _validate_expression(child, f"{path}[{i}]")
        window = expr[-1]
        if type(window) is not int or window < 1:
            raise ValueError(f"{path} window must be a positive strict int")
        return
    if head in _CS or head in _EW1:
        _validate_expression(expr[1], f"{path}[1]")
        return
    for i, child in enumerate(expr[1:], 1):
        _validate_expression(child, f"{path}[{i}]")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable(value[key]) for key in sorted(value)}
    if type(value) in (tuple, list):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class AlphaDefinition:
    definition: Any
    feature_contract: Any
    data_schema: Any
    universe_policy: Any
    horizon: int
    cost_model: Any
    validation_policy: Any
    model: str
    model_version: str

    def __post_init__(self) -> None:
        frozen_definition = _freeze(self.definition, "definition")
        _validate_expression(frozen_definition)
        object.__setattr__(self, "definition", frozen_definition)
        for name in ("feature_contract", "data_schema", "universe_policy", "cost_model", "validation_policy"):
            object.__setattr__(self, name, _freeze(getattr(self, name), name))
        if type(self.horizon) is not int or self.horizon < 1:
            raise ValueError("horizon must be a positive strict int")
        if type(self.model) is not str or self.model not in MODEL_REGISTRY:
            raise ValueError(f"model is not registered: {self.model!r}")
        if type(self.model_version) is not str or self.model_version not in MODEL_REGISTRY[self.model]:
            raise ValueError(f"unsupported version {self.model_version!r} for model {self.model!r}")


def _payload(definition: AlphaDefinition) -> dict[str, Any]:
    if not isinstance(definition, AlphaDefinition):
        raise TypeError("expected AlphaDefinition")
    return _jsonable({name: getattr(definition, name) for name in (
        "definition", "feature_contract", "data_schema", "universe_policy", "horizon",
        "cost_model", "validation_policy", "model", "model_version",
    )})


def canonical_alpha_json(definition: AlphaDefinition) -> str:
    return json.dumps(_payload(definition), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def alpha_id(definition: AlphaDefinition) -> str:
    return hashlib.sha256(canonical_alpha_json(definition).encode("utf-8")).hexdigest()
