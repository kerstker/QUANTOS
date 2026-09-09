"""ResearchRun record contract + deterministic run id (research-ops console source of truth).

A ResearchRun is one backtest/research run: its kind, params, status, headline metrics, chartable series,
verdict and mandatory caveats. Pure stdlib; the id binds a unique invocation identity. `ALLOWED_KINDS`
is the research-only whitelist — the registry refuses anything outside it, so an order/trade kind can never
be persisted here (kept in sync with research_runner.run_kinds.RUN_KINDS)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Optional

VALID_STATUS = {"queued", "running", "done", "error", "cancelled"}
RUN_OWNER_LEGACY_MIRROR = "LEGACY_MIRROR"
RUN_OWNER_RUNNER_INVOCATION = "RUNNER_INVOCATION"
RUNNER_RESTART_INTERRUPTED = "RUNNER_RESTART_INTERRUPTED"
VALID_RUN_OWNERS = {RUN_OWNER_LEGACY_MIRROR, RUN_OWNER_RUNNER_INVOCATION}
ALLOWED_KINDS = {"carry_strategy", "oi_research", "combo_book", "survivorship",
                 "factory_orthogonal", "factory_loop", "data_refresh",
                 "ic_panel", "wide_gauntlet", "paper_cycle",
                 "ashare_alpha", "ashare_paper_track",
                 "ashare_technical_search", "ashare_forward_observation", "perpetual_replay", "perpetual_alpha", "orderblock_research", "btc_mechanism", "btc_factory", "btc_forward", "ai_factory", "ai_factory_forward", "ai_factory_assessment", "ai_factory_portfolio", "ai_factory_portfolio_forward", "ai_factory_verification", "ai_factory_pipeline", "ai_factory_comparison", "profitability_study", "profitability_input", "profitability_forward", "profitability_information"}

_INVOCATION_ID_MAX_LENGTH = 128
_REQUEST_IDENTITY_SCHEMA = "quantos.research_runner.request.v1"
_RUN_IDENTITY_SCHEMA = "quantos.research_runner.identity.v1"


class InvocationConflictError(ValueError):
    """An invocation id was reused for a different canonical request."""


class RunIdentityError(ValueError):
    """A runner-owned row no longer matches its immutable persisted identity."""


def _canonical_hash(value: dict) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"run identity is not canonical JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def normalize_invocation_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invocation_id must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > _INVOCATION_ID_MAX_LENGTH:
        raise ValueError(
            f"invocation_id must contain 1-{_INVOCATION_ID_MAX_LENGTH} non-space characters"
        )
    return normalized


def make_request_hash(kind: str, params: dict, operator: Optional[str]) -> str:
    """Canonical caller request bound to an invocation id (submission time excluded)."""
    return _canonical_hash(
        {
            "schema": _REQUEST_IDENTITY_SCHEMA,
            "kind": kind,
            "params": params,
            "operator": operator,
        }
    )


def make_run_identity_hash(
    *,
    run_id: str,
    kind: str,
    params: dict,
    created_ts: int,
    operator: Optional[str],
    code_version: Optional[str],
    invocation_id: str,
    owner: str,
) -> str:
    """Hash every field that a terminal result is forbidden to rewrite."""
    return _canonical_hash(
        {
            "schema": _RUN_IDENTITY_SCHEMA,
            "run_id": run_id,
            "kind": kind,
            "params": params,
            "created_ts": created_ts,
            "operator": operator,
            "code_version": code_version,
            "invocation_id": normalize_invocation_id(invocation_id),
            "owner": owner,
        }
    )


def make_run_id(
    kind: str,
    params: dict,
    created_ts: int,
    *,
    invocation_id: str | None = None,
) -> str:
    """Return a stable invocation id, retaining the timestamp form for legacy mirrors.

    A runner invocation is an idempotency identity in its own right: retries may arrive in a later
    second and must resolve to the same run. Legacy indexed mirrors have no invocation id and keep
    their historical ``kind + params + created_ts`` identity.
    """
    if invocation_id is None:
        blob = (
            kind
            + "|"
            + json.dumps(params, sort_keys=True, separators=(",", ":"))
            + "|"
            + str(created_ts)
        )
        return "run_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    normalized = normalize_invocation_id(invocation_id)
    blob = f"quantos.research_runner.invocation.v1|{normalized}"
    return "run_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    kind: str
    title: str
    params: dict
    status: str
    created_ts: int
    finished_ts: Optional[int]
    metrics: dict           # headline numbers -> KPI tiles
    series: dict            # named arrays -> charts (e.g. {"per_year": [...]})
    verdict: str
    caveats: list           # mandatory honesty discounts
    provenance: str
    error: Optional[str] = None
    log: list = field(default_factory=list)     # live progress / log lines (observability)
    code_version: Optional[str] = None          # git sha at run time (auditable history)
    operator: Optional[str] = None              # self-asserted accountability, not authentication
    invocation_id: Optional[str] = None         # unique invocation; prevents same-second run aliasing
    owner: str = RUN_OWNER_LEGACY_MIRROR        # isolates indexed compatibility mirrors from runner rows
    request_hash: Optional[str] = None           # kind + params + operator, independent of submit time
    identity_hash: Optional[str] = None          # immutable runner-row identity used by terminal CAS

    def to_dict(self) -> dict:
        return asdict(self)
