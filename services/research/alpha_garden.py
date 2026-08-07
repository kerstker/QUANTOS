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

    def __post_init__(self) -> None:
        for field in ("name", "version", "family"):
            value = getattr(self, field)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if not callable(self.generate):
            raise TypeError("generate must be callable")
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


# Named thin adapters make provenance explicit while sharing the canonical Task 4/5 checks.
def adapt_alpha_factory_output(value: Any) -> AlphaDefinition:
    return adapt_alpha_definition(value)


def adapt_gp_output(value: Any) -> AlphaDefinition:
    return adapt_alpha_definition(value)


def adapt_mcts_output(value: Any) -> AlphaDefinition:
    return adapt_alpha_definition(value)


def adapt_ml_template_output(value: Any) -> AlphaDefinition:
    return adapt_alpha_definition(value)


def adapt_existing_generator(generate: Callable[..., Iterable[Any] | Any], adapter: AdaptFn = adapt_alpha_definition) -> GenerateFn:
    """Wrap an existing generator without importing or duplicating its algorithm."""
    if not callable(generate) or not callable(adapter):
        raise TypeError("generate and adapter must be callable")

    def wrapped(context: Any, rng: random.Random) -> Iterable[AlphaDefinition] | AlphaDefinition:
        output = generate(context, rng)
        if isinstance(output, AlphaDefinition):
            return adapter(output)
        return (adapter(item) for item in output)

    return wrapped


def generate_batch(registry: GeneratorRegistry, budget: GenerationBudget, context: Any) -> CandidateBatch:
    if not isinstance(registry, GeneratorRegistry):
        raise TypeError("registry must be GeneratorRegistry")
    if not isinstance(budget, GenerationBudget):
        raise TypeError("budget must be GenerationBudget")
    if budget.max_candidates == 0 or budget.max_family_candidates == 0:
        return CandidateBatch((), (), {"accepted": 0, "rejected": 0}, {}, {})

    rng = random.Random(budget.seed)
    accepted: list[AlphaCandidate] = []
    rejections: list[Rejection] = []
    seen: set[str] = set()
    generator_counts: Counter[str] = Counter({entry.name: 0 for entry in registry})
    family_counts: Counter[str] = Counter()
    attempts = Counter[str]()
    entry_caps = {
        (entry.name, entry.version): math.floor(budget.max_candidates * entry.max_share)
        for entry in registry
    }

    for entry in registry:
        cap = entry_caps[(entry.name, entry.version)]
        if cap <= 0:
            continue
        try:
            produced = entry.generate(context, rng)
            iterator = iter((produced,) if isinstance(produced, AlphaDefinition) else produced)
        except Exception:
            rejections.append(Rejection("generator_error", entry.name))
            continue
        for raw in iterator:
            if len(accepted) >= budget.max_candidates:
                break
            # Keep a hard attempt bound: a broken/infinite generator cannot defeat the garden bound.
            attempts[entry.name] += 1
            if attempts[entry.name] > budget.max_candidates:
                rejections.append(Rejection("generator_attempt_cap", entry.name))
                break
            try:
                definition = adapt_alpha_definition(raw)
                candidate = AlphaCandidate(definition)
                aid = candidate.alpha_id
                fid = family_id(definition)
            except Exception:
                rejections.append(Rejection("invalid_alpha_definition", entry.name))
                continue
            if aid in seen:
                rejections.append(Rejection("duplicate_alpha_id", entry.name, aid, fid))
                continue
            if family_counts[fid] >= budget.max_family_candidates:
                rejections.append(Rejection("family_cap", entry.name, aid, fid))
                continue
            if generator_counts[entry.name] >= cap:
                rejections.append(Rejection("generator_cap", entry.name, aid, fid))
                continue
            seen.add(aid)
            accepted.append(candidate)
            generator_counts[entry.name] += 1
            family_counts[fid] += 1

    counts = Counter(item.reason for item in rejections)
    counts["accepted"] = len(accepted)
    counts["rejected"] = len(rejections)
    return CandidateBatch(tuple(accepted), tuple(rejections), counts, generator_counts, family_counts)
