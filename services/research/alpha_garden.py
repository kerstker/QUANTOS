"""Bounded, deterministic candidate generation for institutional alpha research.

The garden owns orchestration and admission policy only.  Generators remain thin
callables supplied by a registry; no dynamic imports, code strings, network, or
expensive searches are performed here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import random
import re
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from services.research.alpha_family import AlphaCandidate, family_id
from services.research.alpha_identity import AlphaDefinition

GenerateFn = Callable[[Any, random.Random], Iterable[Any] | Any]
AdaptFn = Callable[[Any], AlphaDefinition]


@dataclass(frozen=True, slots=True)
class GeneratorEntry:
    name: str
    version: str
    family: str
    generate: GenerateFn
    max_share: float = 1.0
    adapter: AdaptFn | None = None

    @property
    def entry_id(self) -> str:
        return f"{self.name}@{self.version}"

    def __post_init__(self) -> None:
        for field in ("name", "version", "family"):
            value = getattr(self, field)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        # The public audit key is deliberately name@version.  Reject separators
        # rather than silently creating ambiguous keys (for example ``a@b@c``).
        if "@" in self.name or "@" in self.version:
            raise ValueError("name and version must not contain '@'")
        if not re.fullmatch(r"[0-9a-f]{64}", self.family):
            raise ValueError("family must be a canonical family_id")
        if not callable(self.generate):
            raise TypeError("generate must be callable")
        if self.adapter is None:
            object.__setattr__(self, "adapter", adapt_alpha_definition)
        elif not callable(self.adapter):
            raise TypeError("adapter must be callable")
        if type(self.max_share) not in (int, float) or not math.isfinite(self.max_share):
            raise ValueError("max_share must be a finite number")
        if not 0 <= self.max_share <= 1:
            raise ValueError("max_share must be in [0, 1]")


class GeneratorRegistry:
    """Immutable registry with a stable, name/version ordered invocation order."""

    __slots__ = ("_entries",)

    def __init__(self, entries: Iterable[GeneratorEntry] = ()) -> None:
        values = tuple(entries)
        if any(not isinstance(entry, GeneratorEntry) for entry in values):
            raise TypeError("registry entries must be GeneratorEntry values")
        keys = [(entry.name, entry.version) for entry in values]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate generator name/version")
        self._entries = tuple(sorted(values, key=lambda e: (e.name, e.version, e.family)))

    @property
    def entries(self) -> tuple[GeneratorEntry, ...]:
        return self._entries

    def __iter__(self):
        return iter(self._entries)


@dataclass(frozen=True, slots=True)
class GenerationBudget:
    max_candidates: int
    max_family_candidates: int
    seed: int

    def __post_init__(self) -> None:
        for name in ("max_candidates", "max_family_candidates"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if type(self.seed) is not int:
            raise TypeError("seed must be an int")


@dataclass(frozen=True, slots=True)
class Rejection:
    reason: str
    generator: str
    alpha_id: str | None = None
    family_id: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    candidates: tuple[AlphaCandidate, ...]
    rejections: tuple[Rejection, ...]
    audit: Mapping[str, int]
    generator_counts: Mapping[str, int]
    family_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit", MappingProxyType(dict(self.audit)))
        object.__setattr__(self, "generator_counts", MappingProxyType(dict(self.generator_counts)))
        object.__setattr__(self, "family_counts", MappingProxyType(dict(self.family_counts)))


def adapt_alpha_definition(value: Any) -> AlphaDefinition:
    """Canonical adapter for existing generators; it deliberately does no search."""
    if isinstance(value, AlphaDefinition):
        return value
    if isinstance(value, Mapping):
        try:
            return AlphaDefinition(**dict(value))
        except (TypeError, ValueError) as exc:
            raise TypeError("mapping is not a registered AlphaDefinition payload") from exc
    raise TypeError("generator output is not a registered AlphaDefinition")


# Legacy engines do not all emit canonical definitions.  These bridges require
# the caller to provide both the complete identity and an explicit expression
# extractor/parser; no name, score, metric, or string is guessed as an alpha.
def _legacy_adapter(value: Any, source: str, identity: Mapping[str, Any],
                    expression_extractor: Callable[[Any], Any]) -> AlphaDefinition:
    if isinstance(value, AlphaDefinition):
        return value
    if not isinstance(identity, Mapping) or not callable(expression_extractor):
        raise TypeError(f"{source} adapter requires identity and expression_extractor")
    expression = expression_extractor(value)
    if isinstance(expression, str):
        raise TypeError(f"{source} extractor must return canonical expression, not text")
    return make_identity_adapter(identity)({"expression": expression})


def _make_legacy_adapter(source: str, identity: Mapping[str, Any],
                         expression_extractor: Callable[[Any], Any]) -> AdaptFn:
    # Validate metadata and extractor when wiring the registry, not on first item.
    snapshot = make_identity_adapter(identity)
    if not callable(expression_extractor):
        raise TypeError(f"{source} expression_extractor must be callable")
    def adapt(value: Any) -> AlphaDefinition:
        if isinstance(value, AlphaDefinition):
            return value
        expression = expression_extractor(value)
        if isinstance(expression, str):
            raise TypeError(f"{source} extractor must return canonical expression, not text")
        return snapshot({"expression": expression})
    return adapt


def adapt_alpha_factory_output(value: Any, *, identity: Mapping[str, Any] | None = None,
                               expression_extractor: Callable[[Any], Any] | None = None) -> AlphaDefinition:
    return _legacy_adapter(value, "alpha_factory", identity, expression_extractor)


def adapt_gp_output(value: Any, *, identity: Mapping[str, Any] | None = None,
                    expression_extractor: Callable[[Any], Any] | None = None) -> AlphaDefinition:
    return _legacy_adapter(value, "gp", identity, expression_extractor)


def adapt_mcts_output(value: Any, *, identity: Mapping[str, Any] | None = None,
                      expression_extractor: Callable[[Any], Any] | None = None) -> AlphaDefinition:
    return _legacy_adapter(value, "mcts", identity, expression_extractor)


def adapt_ml_template_output(value: Any, *, identity: Mapping[str, Any] | None = None,
                             expression_extractor: Callable[[Any], Any] | None = None) -> AlphaDefinition:
    return _legacy_adapter(value, "ml_template", identity, expression_extractor)


def make_alpha_factory_adapter(identity: Mapping[str, Any], expression_extractor: Callable[[Any], Any]) -> AdaptFn:
    return _make_legacy_adapter("alpha_factory", identity, expression_extractor)


def make_gp_adapter(identity: Mapping[str, Any], expression_extractor: Callable[[Any], Any]) -> AdaptFn:
    return _make_legacy_adapter("gp", identity, expression_extractor)


def make_mcts_adapter(identity: Mapping[str, Any], expression_extractor: Callable[[Any], Any]) -> AdaptFn:
    return _make_legacy_adapter("mcts", identity, expression_extractor)


def make_evolutionary_adapter(identity: Mapping[str, Any], expression_extractor: Callable[[Any], Any]) -> AdaptFn:
    return _make_legacy_adapter("evolutionary", identity, expression_extractor)


def make_ml_template_adapter(identity: Mapping[str, Any], expression_extractor: Callable[[Any], Any]) -> AdaptFn:
    return _make_legacy_adapter("ml_template", identity, expression_extractor)


def _snapshot_metadata(value: Any, path: str = "identity") -> Any:
    """Copy only the JSON-shaped values accepted by AlphaDefinition._freeze."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} mapping keys must be strings")
            result[key] = _snapshot_metadata(item, f"{path}.{key}")
        return MappingProxyType(result)
    if type(value) in (list, tuple):
        return tuple(_snapshot_metadata(item, f"{path}[{i}]") for i, item in enumerate(value))
    raise TypeError(f"{path} contains unsupported value {value!r}")


def make_identity_adapter(identity: Mapping[str, Any]) -> AdaptFn:
    """Return an adapter with a deep, immutable caller-supplied identity snapshot."""
    if not isinstance(identity, Mapping):
        raise TypeError("identity must be a mapping")
    required = {"feature_contract", "data_schema", "universe_policy", "horizon",
                "cost_model", "validation_policy", "model", "model_version"}
    if not required.issubset(identity):
        raise ValueError("identity metadata is incomplete")
    frozen_identity = {key: _snapshot_metadata(identity[key], key) for key in required}

    def adapt(value: Any) -> AlphaDefinition:
        if isinstance(value, AlphaDefinition):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("legacy output must be a mapping")
        expression = value.get("definition", value.get("expression"))
        if expression is None:
            raise TypeError("legacy output lacks expression")
        return AlphaDefinition(definition=expression, **frozen_identity)

    return adapt


def _is_singleton_output(value: Any) -> bool:
    # Mappings, text and bytes are scalar payloads, not batches of their keys/chars.
    return isinstance(value, (AlphaDefinition, Mapping, str, bytes, bytearray))


def adapt_existing_generator(generate: Callable[..., Iterable[Any] | Any], adapter: AdaptFn = adapt_alpha_definition) -> GenerateFn:
    """Wrap an existing generator without importing or duplicating its algorithm."""
    if not callable(generate) or not callable(adapter):
        raise TypeError("generate and adapter must be callable")

    def wrapped(context: Any, rng: random.Random) -> Iterable[AlphaDefinition] | AlphaDefinition:
        output = generate(context, rng)
        if _is_singleton_output(output):
            return adapter(output)
        try:
            iterator = iter(output)
        except TypeError:
            return adapter(output)
        return (adapter(item) for item in iterator)

    return wrapped


def _entry_attempt_cap(budget: GenerationBudget) -> int:
    """Bound each entry's pulls; total work is entries times this budget."""
    return max(1, budget.max_candidates)


def generate_batch(registry: GeneratorRegistry, budget: GenerationBudget, context: Any) -> CandidateBatch:
    if not isinstance(registry, GeneratorRegistry):
        raise TypeError("registry must be GeneratorRegistry")
    if not isinstance(budget, GenerationBudget):
        raise TypeError("budget must be GenerationBudget")

    generator_counts: Counter[str] = Counter({entry.entry_id: 0 for entry in registry})
    family_counts: Counter[str] = Counter()
    if budget.max_candidates == 0 or budget.max_family_candidates == 0:
        return CandidateBatch((), (), {"accepted": 0, "rejected": 0},
                              generator_counts, family_counts)

    rng = random.Random(budget.seed)
    accepted: list[AlphaCandidate] = []
    rejections: list[Rejection] = []
    seen: set[str] = set()
    entry_caps = {entry.entry_id: math.floor(budget.max_candidates * entry.max_share)
                  for entry in registry}

    for entry in registry:
        # These checks happen before calling generate: caps are admission and
        # invocation limits, not merely post-output rejection reasons.
        entry_id = entry.entry_id
        cap = entry_caps[entry_id]
        if len(accepted) >= budget.max_candidates or cap <= generator_counts[entry_id]:
            continue
        if family_counts[entry.family] >= budget.max_family_candidates:
            rejections.append(Rejection("family_cap", entry_id, family_id=entry.family))
            continue
        attempt_limit = _entry_attempt_cap(budget)
        attempts = 0
        exhausted = False
        try:
            produced = entry.generate(context, rng)
            singleton = _is_singleton_output(produced)
            if singleton:
                iterator = iter((produced,))
            else:
                try:
                    iterator = iter(produced)
                except TypeError:
                    iterator = iter((produced,))
        except Exception:
            rejections.append(Rejection("generator_error", entry_id))
            continue

        while (len(accepted) < budget.max_candidates
               and generator_counts[entry_id] < cap
               and family_counts[entry.family] < budget.max_family_candidates
               and attempts < attempt_limit):
            try:
                raw = next(iterator)
            except StopIteration:
                exhausted = True
                break
            except Exception:
                rejections.append(Rejection("generator_error", entry_id))
                break
            attempts += 1
            if singleton:
                exhausted = True
            try:
                definition = entry.adapter(raw)
                candidate = AlphaCandidate(definition)
                aid = candidate.alpha_id
                fid = family_id(definition)
            except Exception:
                rejections.append(Rejection("adapter_error", entry_id))
                continue
            if fid != entry.family:
                rejections.append(Rejection("family_mismatch", entry_id, aid, fid))
                continue
            if aid in seen:
                rejections.append(Rejection("duplicate_alpha_id", entry_id, aid, fid))
                continue
            if family_counts[fid] >= budget.max_family_candidates:
                rejections.append(Rejection("family_cap", entry_id, aid, fid))
                continue
            if generator_counts[entry_id] >= cap:
                rejections.append(Rejection("generator_cap", entry_id, aid, fid))
                continue
            seen.add(aid)
            accepted.append(candidate)
            generator_counts[entry_id] += 1
            family_counts[fid] += 1

        # Reaching an admission cap or a global cap is not an attempt-cap
        # event.  Only an actual attempt limit with unknown remaining output is.
        if (not exhausted and attempts >= attempt_limit
                and len(accepted) < budget.max_candidates
                and generator_counts[entry_id] < cap
                and family_counts[entry.family] < budget.max_family_candidates):
            rejections.append(Rejection("generator_attempt_cap", entry_id))

    counts = Counter(item.reason for item in rejections)
    counts["accepted"] = len(accepted)
    counts["rejected"] = len(rejections)
    return CandidateBatch(tuple(accepted), tuple(rejections), counts,
                          generator_counts, family_counts)
