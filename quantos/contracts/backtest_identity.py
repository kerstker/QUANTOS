"""Exact causal backtest identities shared by producers and offline verifiers.

These pure contracts preserve the V2 byte-level identity algorithm. They grant
no authority and import no execution, risk, broker or live implementation.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from enum import Enum
from typing import Any

from quantos.contracts.json_integrity import ascii_json_string_size

RUN_MANIFEST_SCHEMA_VERSION = 2
CANONICAL_ENGINE_SEMANTICS = "LINEAR_PERPETUAL_EVENT_V2"


def _canonical_identity(value: Any, *, name: str) -> Any:
    """Encode authority inputs without lossy float rounding or ``default=str``.

    The platform-wide ``stable_hash`` deliberately rounds floats for portable
    business objects.  A run manifest has a stricter contract: every exact
    load-bearing input must alter the run identity.  Float values are therefore
    represented by their lossless IEEE-754 hexadecimal form and unsupported
    object types fail closed instead of inheriting a potentially unstable
    string representation.
    """

    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} contains a non-finite float")
        return ["float64", value.hex()]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{name} contains a non-finite Decimal")
        return ["decimal", str(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, Enum):
        return [
            "enum",
            f"{type(value).__module__}.{type(value).__qualname__}",
            _canonical_identity(value.value, name=name),
        ]
    if isinstance(value, Mapping):
        entries = [
            [
                _canonical_identity(key, name=f"{name}.key"),
                _canonical_identity(item, name=f"{name}[{key!r}]"),
            ]
            for key, item in value.items()
        ]
        entries.sort(
            key=lambda item: json.dumps(
                item[0], ensure_ascii=False, separators=(",", ":")
            )
        )
        return ["mapping", entries]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_identity(item, name=name) for item in value]
        items.sort(
            key=lambda item: json.dumps(
                item, ensure_ascii=False, separators=(",", ":")
            )
        )
        return ["set", items]
    if isinstance(value, Sequence):
        return [
            "sequence",
            [_canonical_identity(item, name=name) for item in value],
        ]
    raise TypeError(
        f"{name} contains unsupported authority identity type "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def _authority_hash(value: Any, *, name: str) -> str:
    canonical = _canonical_identity(value, name=name)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def exact_identity_hash(value: Any, *, name: str = "authority_payload") -> str:
    """Public exact hash for V2 input and immutable-result evidence."""

    if not isinstance(name, str) or not name.strip():
        raise ValueError("identity hash name must be non-empty")
    return _authority_hash(value, name=name)


def _json_string_size(value: str) -> int:
    """Count ensure_ascii=False JSON bytes without expanding a large string."""
    if len(value) > 16_000_000:
        raise ValueError("identity transport byte bound")
    ascii_size = ascii_json_string_size(value, maximum_bytes=16_000_000)
    if ascii_size is not None:
        if ascii_size > 16_000_000:
            raise ValueError("identity transport byte bound")
        return ascii_size
    size = 2
    for character in value:
        number = ord(character)
        if character in ('"', '\\', '\b', '\f', '\n', '\r', '\t'):
            size += 2
        elif number < 32:
            size += 6
        elif number < 128:
            size += 1
        elif number < 2048:
            size += 2
        elif 0xD800 <= number <= 0xDFFF:
            raise ValueError("identity transport unicode")
        elif number < 65536:
            size += 3
        else:
            size += 4
        if size > 16_000_000:
            raise ValueError("identity transport byte bound")
    return size


def _transport_size(tree: Any) -> None:
    """Bound the expanded tagged tree before decoding or JSON serialization."""
    if type(tree) is list and tree and type(tree[0]) is str:
        tag = tree[0]
        if tag == "none" and len(tree) == 1:
            return  # Eight JSON bytes, two nodes, depth one.
        if len(tree) == 2 and tag in ("bool", "int", "float64", "decimal", "str", "bytes"):
            value = tree[1]
            if type(value) is str:
                size = len(tag) + 5 + _json_string_size(value)
            elif type(value) is bool:
                size = len(tag) + 5 + (4 if value else 5)
            else:
                size = None
            if size is not None:
                # These fixed scalar shapes have three nodes and depth one.
                # The strict decoder still checks the tag's value type and
                # canonical spelling; only the generic bound traversal goes.
                if size > 16_000_000:
                    raise ValueError("identity transport byte bound")
                return
    pending = [(tree, 0)]
    size = nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 256 or nodes > 1_000_000:
            raise ValueError("identity transport structural bound")
        if type(value) is str:
            size += _json_string_size(value)
        elif type(value) is bool:
            size += 4 if value else 5
        elif type(value) is list:
            if len(value) > 100_000:
                raise ValueError("identity transport structural bound")
            size += 2 + max(0, len(value) - 1)
            pending.extend((child, depth + 1) for child in value)
        else:
            raise ValueError("identity transport JSON type")
        if size > 16_000_000:
            raise ValueError("identity transport byte bound")


def _transport_bytes(tree: Any) -> bytes:
    _transport_size(tree)
    try:
        raw = json.dumps(tree, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid identity transport") from exc
    if len(raw) > 16_000_000:
        raise ValueError("identity transport byte bound")
    return raw


def encode_identity_payload(value: Any) -> Any:
    """Transport a bounded native hash input with its original type tags.

    The original identity algorithm is unchanged. This transport covers its
    primitive/container subset; arbitrary enum classes are never imported or
    reconstructed. Original sequence/set distinctions already normalized by
    that algorithm are retained as its exact canonical representation.
    """
    if type(value) in (type(None), bool, int, float, Decimal, str, bytes):
        # Exact builtin scalars produce a fixed canonical shape. Bound that
        # shape directly rather than serializing and decoding our own output.
        # Subclasses, enums and containers retain the complete path below.
        if type(value) in (str, bytes) and len(value) > 16_000_000:
            raise ValueError("identity transport scalar bound")
        if type(value) is bytes and 2 * len(value) > 16_000_000:
            raise ValueError("identity transport byte bound")
        tree = _canonical_identity(value, name="identity_transport")
        if type(value) in (int, float, Decimal) and len(tree[1]) > 4096:
            raise ValueError("identity transport numeric scalar bound")
        _transport_size(tree)
        return tree
    count = size = 0

    def bounded(item: Any, depth: int) -> None:
        nonlocal count, size
        count += 1
        if depth > 64 or count > 100_000:
            raise ValueError("identity transport structural bound")
        if isinstance(item, Enum) and not isinstance(item, (str, int)):
            raise ValueError("identity transport enum unsupported")
        if isinstance(item, (str, bytes)) and len(item) > 16_000_000:
            raise ValueError("identity transport scalar bound")
        if isinstance(item, str):
            size += _json_string_size(item)
        elif isinstance(item, bytes):
            size += 2 * len(item)
        elif isinstance(item, (int, float, Decimal)) and not isinstance(item, bool):
            scalar = item.hex() if isinstance(item, float) else str(item)
            if len(scalar) > 4096:
                raise ValueError("identity transport numeric scalar bound")
            size += len(scalar)
        if size > 16_000_000:
            raise ValueError("identity transport byte bound")
        if isinstance(item, Mapping):
            for key, child in item.items():
                bounded(key, depth + 1)
                bounded(child, depth + 1)
        elif isinstance(item, (set, frozenset)) or (
            isinstance(item, Sequence) and not isinstance(item, (str, bytes))
        ):
            for child in item:
                bounded(child, depth + 1)

    bounded(value, 0)
    tree = _canonical_identity(value, name="identity_transport")
    # Bound the actual emitted tree without allocating a second JSON string
    # and UTF-8 buffer. This is the same expanded-byte/structure check used
    # before public decoding; the complete strict round-trip remains below.
    _transport_size(tree)
    # Verify that the public transport can reconstruct this exact canonical
    # representation; unsupported objects cannot silently become strings.
    decode_identity_payload(tree)
    return tree


def decode_identity_payload(tree: Any) -> Any:
    """Decode strict original tags, rejecting aliases and noncanonical encodings."""
    _transport_size(tree)
    count = 0

    def read(node: Any, depth: int) -> Any:
        nonlocal count
        count += 1
        if depth > 64 or count > 100_000:
            raise ValueError("identity transport structural bound")
        if type(node) is not list or not node or type(node[0]) is not str:
            raise ValueError("identity transport tag")
        tag = node[0]
        if tag == "none" and len(node) == 1:
            return None
        if len(node) != 2:
            raise ValueError("identity transport fields")
        value = node[1]
        if tag == "bool" and type(value) is bool:
            return value
        if tag == "str" and type(value) is str and len(value) <= 16_000_000:
            return value
        if tag in ("int", "float64", "decimal", "bytes"):
            if type(value) is not str or len(value) > (16_000_000 if tag == "bytes" else 4096):
                raise ValueError("identity transport scalar")
            if tag == "int":
                return int(value)
            if tag == "float64":
                return float.fromhex(value)
            if tag == "decimal":
                return Decimal(value)
            return bytes.fromhex(value)
        if tag in ("sequence", "set", "mapping"):
            if type(value) is not list or len(value) > 100_000:
                raise ValueError("identity transport container")
            if tag == "sequence":
                return tuple(read(child, depth + 1) for child in value)
            if tag == "set":
                return frozenset(read(child, depth + 1) for child in value)
            result = {}
            for entry in value:
                if type(entry) is not list or len(entry) != 2:
                    raise ValueError("identity transport mapping entry")
                key = read(entry[0], depth + 1)
                if key in result:
                    raise ValueError("identity transport duplicate or aliased key")
                result[key] = read(entry[1], depth + 1)
            return result
        raise ValueError("identity transport unsupported tag or value")

    try:
        value = read(tree, 0)
        canonical = _canonical_identity(value, name="identity_transport")
        # The complete transport bound only admits exact builtin list/str/bool
        # nodes. Comparing their tagged canonical trees therefore preserves
        # compact JSON byte equality, including container ordering, aliases
        # and scalar spelling, without allocating two serialized copies.
        # Comparing decoded values instead would lose those distinctions.
        if canonical != tree:
            raise ValueError("noncanonical identity transport")
        return value
    except (TypeError, ArithmeticError, OverflowError, UnicodeError) as exc:
        raise ValueError("invalid identity transport") from exc


def _digest(value: Any, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} must be provided")
    if isinstance(value, (str, bytes, Sequence, Mapping)) and len(value) == 0:
        raise ValueError(f"{name} must be non-empty")
    return _authority_hash(value, name=name)


def _event_time(event: Any, *, name: str) -> int:
    if isinstance(event, Mapping):
        time_keys = (
            "t",
            "timestamp",
            "event_time",
            "event_time_ms",
            "decision_time_ms",
            "execution_time_ms",
        )
        present_times = [(key, event[key]) for key in time_keys if key in event]
        if not present_times:
            raise ValueError(f"{name} has no event time")
        for key, candidate in present_times:
            if (
                isinstance(candidate, bool)
                or not isinstance(candidate, int)
                or candidate < 0
            ):
                raise ValueError(
                    f"{name}.{key} time must be a non-negative integer"
                )
        value = present_times[0][1]
        if any(candidate != value for _, candidate in present_times[1:]):
            raise ValueError(f"{name} has conflicting event times")
    else:
        value = event
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} time must be a non-negative integer")
    return value


@dataclass(frozen=True)
class RunManifestV2:
    """Canonical identity for one causal linear-perpetual backtest run."""

    release_hash: str
    data_hash: str
    universe_hash: str
    alpha_hash: str
    periods_hash: str
    decision_events_hash: str
    execution_events_hash: str
    causal_timeline_hash: str
    weights_hash: str
    prices_hash: str
    cost_model_hash: str
    funding_policy_hash: str
    venue_rules_hash: str
    risk_limits_hash: str
    filters_hash: str
    impact_model_hash: str
    adv_hash: str
    parameters_hash: str
    random_seed: int
    schema_version: int = RUN_MANIFEST_SCHEMA_VERSION
    engine_semantics: str = CANONICAL_ENGINE_SEMANTICS

    def __post_init__(self) -> None:
        if self.schema_version != RUN_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported run manifest schema_version")
        if self.engine_semantics != CANONICAL_ENGINE_SEMANTICS:
            raise ValueError("unsupported engine_semantics")
        if (
            isinstance(self.random_seed, bool)
            or not isinstance(self.random_seed, int)
            or not 0 <= self.random_seed < (1 << 63)
        ):
            raise ValueError("random_seed must be a signed-SQLite-safe non-negative integer")
        for item in fields(self):
            if not item.name.endswith("_hash"):
                continue
            value = getattr(self, item.name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{item.name} must be a lowercase sha256 digest")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine_semantics": self.engine_semantics,
            "release_hash": self.release_hash,
            "data_hash": self.data_hash,
            "universe_hash": self.universe_hash,
            "alpha_hash": self.alpha_hash,
            "periods_hash": self.periods_hash,
            "decision_events_hash": self.decision_events_hash,
            "execution_events_hash": self.execution_events_hash,
            "causal_timeline_hash": self.causal_timeline_hash,
            "weights_hash": self.weights_hash,
            "prices_hash": self.prices_hash,
            "cost_model_hash": self.cost_model_hash,
            "funding_policy_hash": self.funding_policy_hash,
            "venue_rules_hash": self.venue_rules_hash,
            "risk_limits_hash": self.risk_limits_hash,
            "filters_hash": self.filters_hash,
            "impact_model_hash": self.impact_model_hash,
            "adv_hash": self.adv_hash,
            "parameters_hash": self.parameters_hash,
            "random_seed": self.random_seed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunManifestV2":
        if not isinstance(value, Mapping):
            raise TypeError("run manifest must be a mapping")
        expected = {item.name for item in fields(cls)}
        if set(value) != expected:
            missing = sorted(expected - set(value))
            extra = sorted(set(value) - expected)
            raise ValueError(f"run manifest fields mismatch: missing={missing}, extra={extra}")
        return cls(**dict(value))

    @property
    def manifest_hash(self) -> str:
        return _authority_hash(self.as_dict(), name="run_manifest_v2")

    @property
    def run_id(self) -> str:
        # The input identity is the run identity; no mutable output is included.
        return self.manifest_hash


def build_run_manifest_v2(
    *,
    release_identity: Any,
    data_identity: Any,
    universe: Any,
    alpha_definition: Any,
    periods: Sequence[Any],
    decision_events: Sequence[Any],
    execution_events: Sequence[Any],
    weights_by_period: Any,
    prices_by_period: Any,
    cost_model: Any,
    funding_policy: Any,
    venue_rules: Any,
    risk_limits: Any,
    filters: Any,
    impact_model: Any,
    adv_by_period: Any,
    parameters: Any,
    random_seed: int,
) -> RunManifestV2:
    """Build a manifest after enforcing a strictly causal execution timeline."""

    if not periods:
        raise ValueError("periods must be non-empty")
    period_count = len(periods)
    if (
        not decision_events
        or period_count != len(decision_events)
        or period_count != len(execution_events)
    ):
        raise ValueError(
            "periods, decision events, and execution events must be non-empty "
            "and one-to-one aligned"
        )
    try:
        weights_count = len(weights_by_period)
        prices_count = len(prices_by_period)
    except TypeError as exc:
        raise ValueError(
            "weights_by_period and prices_by_period must be sized one-to-one records"
        ) from exc
    if weights_count != period_count or prices_count != period_count:
        raise ValueError(
            "periods, weights_by_period, and prices_by_period must be one-to-one aligned"
        )
    decisions = [
        _event_time(event, name=f"decision_events[{index}]")
        for index, event in enumerate(decision_events)
    ]
    executions = [
        _event_time(event, name=f"execution_events[{index}]")
        for index, event in enumerate(execution_events)
    ]
    for decision_time, execution_time in zip(decisions, executions, strict=True):
        if execution_time <= decision_time:
            raise ValueError("execution event must be strictly after decision event")
    if decisions != sorted(decisions) or len(set(decisions)) != len(decisions):
        raise ValueError("decision events must be strictly increasing")
    if executions != sorted(executions) or len(set(executions)) != len(executions):
        raise ValueError("execution events must be strictly increasing")

    return RunManifestV2(
        release_hash=_digest(release_identity, "release_identity"),
        data_hash=_digest(data_identity, "data_identity"),
        universe_hash=_digest(universe, "universe"),
        alpha_hash=_digest(alpha_definition, "alpha_definition"),
        periods_hash=_digest(periods, "periods"),
        decision_events_hash=_digest(decision_events, "decision_events"),
        execution_events_hash=_digest(execution_events, "execution_events"),
        causal_timeline_hash=_authority_hash(
            tuple(zip(decisions, executions, strict=True)),
            name="causal_timeline",
        ),
        weights_hash=_digest(weights_by_period, "weights_by_period"),
        prices_hash=_digest(prices_by_period, "prices_by_period"),
        cost_model_hash=_digest(cost_model, "cost_model"),
        funding_policy_hash=_digest(funding_policy, "funding_policy"),
        venue_rules_hash=_digest(venue_rules, "venue_rules"),
        risk_limits_hash=_digest(risk_limits, "risk_limits"),
        filters_hash=_digest(filters, "filters"),
        impact_model_hash=_digest(impact_model, "impact_model"),
        adv_hash=_digest(adv_by_period, "adv_by_period"),
        parameters_hash=_digest(parameters, "parameters"),
        random_seed=random_seed,
    )


__all__ = [
    "RUN_MANIFEST_SCHEMA_VERSION",
    "CANONICAL_ENGINE_SEMANTICS",
    "RunManifestV2",
    "build_run_manifest_v2",
    "exact_identity_hash",
]
