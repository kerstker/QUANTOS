#!/usr/bin/env python3
"""One-shot, fail-closed worker for the Wave 2 Binance Futures Testnet inbox.

The command defaults to validation-only preview.  It creates a network-capable
adapter only when ``--testnet-submit`` is present, and that adapter is pinned to
``BinanceFuturesAdapter.TESTNET_BASE``.  Runtime risk numbers are supplied by a
versioned operator file; this tool does not invent limits or sign authority.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable

from live.exchange.binance_futures import BinanceFuturesAdapter
from live.execution.command_store import SqliteExecutionCommandStore
from live.execution.gate_context import build_gate_positions
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.execution.service import (
    ExecutionAuthoritySnapshot,
    TestnetExecutionService,
)
from live.governance.kill_switch_store import (
    load_kill_switch_state,
    open_authoritative_kill_switch_store,
)
from live.governance.mandate_store import load_mandate
from live.governance.testnet_mandate_proposal import (
    MandateProposalError,
    TestnetMandateCeiling,
    derive_testnet_mandate_ceiling,
    parse_runtime_risk_limits,
    validate_mandate_within_ceiling,
)
from live.governance.validation_attestation import load_attestations
from live.pipeline import UNIT_SCALE
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.mft_execution import TestnetExecutionIntent
from services.monitoring.audit_store import DurableAuditLog
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (
    AccountRiskState,
    MarketContext,
    RiskContext,
    StrategyApproval,
)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from services.risk.pre_trade.kill_switch_runtime import arm_and_record


_AUTHORITY_SCHEMA = "mft-testnet-authority.v1"
_RUNTIME_SCHEMA = "mft-testnet-runtime.v1"
_WORKER = "testnet-execution-service"
_SAFE_CODE = re.compile(r"[^A-Za-z0-9]+")


class CliConfigurationError(RuntimeError):
    """Configuration is unavailable or malformed; the message is a safe code."""


def _code(prefix: str, exc: BaseException | None = None) -> str:
    suffix = "" if exc is None else f"_{type(exc).__name__}"
    return _SAFE_CODE.sub("_", f"{prefix}{suffix}").strip("_").upper()[:128]


def _fail(code: str) -> CliConfigurationError:
    return CliConfigurationError(_SAFE_CODE.sub("_", code).strip("_").upper()[:128])


def _json_file(path: str, code: str) -> Mapping[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise _fail(_code(code, exc)) from None
    if not isinstance(value, Mapping):
        raise _fail(f"{code}_NOT_OBJECT")
    return value


def _finite(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"RUNTIME_{name}_INVALID")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise _fail(f"RUNTIME_{name}_INVALID")
    return number


def _nonnegative(value: object, name: str) -> float:
    number = _finite(value, name)
    if number < 0:
        raise _fail(f"RUNTIME_{name}_INVALID")
    return number


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"RUNTIME_{name}_INVALID")
    if value < 0 or (positive and value <= 0):
        raise _fail(f"RUNTIME_{name}_INVALID")
    return value


def _number_map(
    value: object, name: str, *, positive_values: bool = False
) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise _fail(f"RUNTIME_{name}_INVALID")
    output: dict[str, float] = {}
    for key, number in value.items():
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise _fail(f"RUNTIME_{name}_INVALID")
        output[key] = _finite(
            number, name, positive=positive_values
        )
    return output


def load_testnet_credentials(environ: Mapping[str, str]) -> tuple[str, str]:
    """Load only named Testnet variables; never fall back to files or mainnet."""

    key = environ.get("BINANCE_TESTNET_API_KEY", "").strip()
    secret = environ.get("BINANCE_TESTNET_API_SECRET", "").strip()
    if not key or not secret:
        raise _fail("CREDENTIALS_MISSING")
    return key, secret


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--mandate", required=True)
    parser.add_argument("--attestations", required=True)
    parser.add_argument("--kill-switch", required=True)
    parser.add_argument("--command-db", required=True)
    parser.add_argument("--recovery-db", required=True)
    parser.add_argument("--runtime-input")
    parser.add_argument("--ledger-db")
    parser.add_argument("--audit-db")
    parser.add_argument("--worker-id", default=_WORKER)
    parser.add_argument("--max-market-age-ms", type=int, default=15 * 60_000)
    parser.add_argument("--lease-ms", type=int, default=5 * 60_000)
    parser.add_argument(
        "--testnet-submit",
        action="store_true",
        help="Explicitly allow Binance Futures Testnet submission for this run.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.max_market_age_ms < 0 or args.lease_ms <= 0:
        raise _fail("SERVICE_TIMING_INVALID")
    if args.testnet_submit and not all(
        (args.runtime_input, args.ledger_db, args.audit_db)
    ):
        raise _fail("SUBMIT_RUNTIME_MISSING")


def _load_intent(path: str) -> TestnetExecutionIntent:
    try:
        return TestnetExecutionIntent.model_validate_json(
            Path(path).read_text(encoding="utf-8"), strict=True
        )
    except Exception as exc:
        raise _fail(_code("INTENT_INVALID", exc)) from None


def _load_authority_body(path: str) -> Mapping[str, object]:
    value = _json_file(path, "AUTHORITY_INVALID")
    if value.get("schema_version") != _AUTHORITY_SCHEMA:
        raise _fail("AUTHORITY_SCHEMA_INVALID")
    return value


def _require_mandate_within_system_policy(
    mandate: object,
    ceiling: TestnetMandateCeiling,
) -> None:
    try:
        validate_mandate_within_ceiling(mandate, ceiling)
    except (MandateProposalError, TypeError, ValueError):
        raise _fail("MANDATE_EXCEEDS_SYSTEM_POLICY") from None


def _authority_provider(
    args: argparse.Namespace,
    *,
    mandate_ceiling: TestnetMandateCeiling | None = None,
):
    def provide(intent: TestnetExecutionIntent, now_ms: int) -> ExecutionAuthoritySnapshot:
        del now_ms
        body = _load_authority_body(args.authority)
        mandate = load_mandate(args.mandate)
        if mandate_ceiling is not None:
            _require_mandate_within_system_policy(mandate, mandate_ceiling)
        attestations = load_attestations(args.attestations)
        state = load_kill_switch_state(args.kill_switch)
        contiguous = body.get("market_contiguous")
        if type(contiguous) is not bool:
            raise _fail("AUTHORITY_MARKET_CONTIGUOUS_INVALID")
        try:
            validation_raw = body["validation_artifact_hashes"]
            watermark = body["market_watermark_ms"]
            if isinstance(validation_raw, (str, bytes)):
                raise TypeError("validation hashes must be a collection")
            if isinstance(watermark, bool) or not isinstance(watermark, int):
                raise TypeError("market watermark must be an integer")
            validation_hashes = tuple(validation_raw)
            return ExecutionAuthoritySnapshot(
                workflow_receipt_hash=str(body["workflow_receipt_hash"]),
                validation_artifact_hashes=validation_hashes,
                market_snapshot_hash=str(body["market_snapshot_hash"]),
                market_watermark_ms=watermark,
                market_contiguous=contiguous,
                policy_version=str(body["policy_version"]),
                policy_hash=str(body["policy_hash"]),
                kill_switch_tripped=state.tripped,
                mandate=mandate,
                validation_attestation=attestations.get(intent.portfolio_id),
            )
        except CliConfigurationError:
            raise
        except Exception as exc:
            raise _fail(_code("AUTHORITY_FIELDS_INVALID", exc)) from None

    return provide


def _risk_limits(raw: object) -> RiskLimits:
    if not isinstance(raw, Mapping):
        raise _fail("RUNTIME_RISK_LIMITS_INVALID")
    if set(raw) != {field.name for field in fields(RiskLimits)}:
        raise _fail("RUNTIME_RISK_LIMIT_FIELDS_MISMATCH")
    try:
        return parse_runtime_risk_limits(raw)
    except Exception as exc:
        raise _fail(_code("RUNTIME_RISK_LIMITS_INVALID", exc)) from None


@dataclass(frozen=True, slots=True)
class _PreparedRuntime:
    prices: Mapping[str, float]
    aum: float
    current_positions: Mapping[str, float]
    account_positions: Mapping[str, float]
    mandate_ceiling: TestnetMandateCeiling
    gate: PreTradeRiskGate
    ctx_factory: Callable[[object], RiskContext]
    ledger: LiveLedger
    audit: DurableAuditLog

    def pipeline_inputs(self) -> dict[str, object]:
        return {
            "prices": self.prices,
            "aum": self.aum,
            "current_positions": self.current_positions,
            "account_positions": self.account_positions,
            "gate": self.gate,
            "ctx_factory": self.ctx_factory,
            "ledger": self.ledger,
            "audit": self.audit,
            "poll_sleep": lambda: time.sleep(1.0),
        }


def _prepare_runtime(
    args: argparse.Namespace,
    intent: TestnetExecutionIntent,
    now_ms: int,
) -> _PreparedRuntime:
    body = _json_file(args.runtime_input, "RUNTIME_INVALID")
    if body.get("schema_version") != _RUNTIME_SCHEMA:
        raise _fail("RUNTIME_SCHEMA_INVALID")
    if body.get("market_snapshot_hash") != intent.market_snapshot_hash:
        raise _fail("RUNTIME_MARKET_SNAPSHOT_MISMATCH")
    runtime_watermark = body.get("market_watermark_ms")
    if (
        isinstance(runtime_watermark, bool)
        or not isinstance(runtime_watermark, int)
        or runtime_watermark != intent.market_watermark_ms
    ):
        raise _fail("RUNTIME_MARKET_WATERMARK_MISMATCH")
    policy_version = body.get("risk_policy_version")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise _fail("RUNTIME_RISK_POLICY_VERSION_MISSING")
    prices = _number_map(body.get("prices"), "PRICES", positive_values=True)
    current_positions = _number_map(body.get("current_positions"), "POSITIONS")
    account_positions = _number_map(
        body.get("account_positions"), "ACCOUNT_POSITIONS"
    )
    aum = _finite(body.get("aum"), "AUM", positive=True)
    account = body.get("account")
    market = body.get("market")
    if not isinstance(account, Mapping) or not isinstance(market, Mapping):
        raise _fail("RUNTIME_CONTEXT_INVALID")
    account_id = account.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise _fail("RUNTIME_ACCOUNT_ID_INVALID")
    limits = _risk_limits(body.get("risk_limits"))
    if account_id not in limits.allowed_accounts:
        raise _fail("RUNTIME_ACCOUNT_NOT_ALLOWED")
    target_instruments = {target.instrument_id for target in intent.targets}
    if not target_instruments.issubset(prices):
        raise _fail("RUNTIME_TARGET_PRICE_MISSING")
    if not target_instruments.issubset(limits.allowed_instruments):
        raise _fail("RUNTIME_TARGET_NOT_ALLOWED")
    if not target_instruments.issubset(market):
        raise _fail("RUNTIME_TARGET_MARKET_MISSING")
    try:
        mandate_ceiling = derive_testnet_mandate_ceiling(intent, body)
    except MandateProposalError:
        raise _fail("RUNTIME_MANDATE_PROPOSAL_INVALID") from None
    max_staleness = _integer(
        body.get("gate_max_staleness_ms"), "GATE_MAX_STALENESS_MS", positive=True
    )
    account_as_of = _integer(
        account.get("as_of_ms"), "ACCOUNT_AS_OF_MS", positive=True
    )
    account_max_age = _integer(
        body.get("account_max_age_ms"), "ACCOUNT_MAX_AGE_MS", positive=True
    )
    if account_as_of > now_ms or now_ms - account_as_of > account_max_age:
        raise _fail("RUNTIME_ACCOUNT_SNAPSHOT_STALE")
    state = load_kill_switch_state(args.kill_switch)
    gate = PreTradeRiskGate(limits, kill_switch=KillSwitch(state=state))
    gate_positions = build_gate_positions(
        account_positions, prices, unit_scale=UNIT_SCALE
    )
    account_state = AccountRiskState(
        account_id=account_id,
        cash=_finite(account.get("cash"), "ACCOUNT_CASH"),
        equity=_finite(account.get("equity"), "ACCOUNT_EQUITY", positive=True),
        day_pnl=_finite(account.get("day_pnl"), "ACCOUNT_DAY_PNL"),
        peak_equity=_finite(
            account.get("peak_equity"), "ACCOUNT_PEAK_EQUITY", positive=True
        ),
        positions=gate_positions,
    )

    def ctx_factory(order) -> RiskContext:
        item = market.get(order.instrument_id)
        if not isinstance(item, Mapping):
            raise _fail("RUNTIME_ORDER_MARKET_MISSING")
        price_as_of = _integer(
            item.get("price_as_of_ms"), "PRICE_AS_OF_MS", positive=True
        )
        if price_as_of != intent.market_watermark_ms:
            raise _fail("RUNTIME_PRICE_WATERMARK_MISMATCH")
        market_open = item.get("market_open")
        if type(market_open) is not bool:
            raise _fail("RUNTIME_MARKET_OPEN_INVALID")
        return RiskContext(
            account=account_state,
            market=MarketContext(
                instrument_id=order.instrument_id,
                known=True,
                reference_price=prices[order.instrument_id] / UNIT_SCALE,
                market_open=market_open,
                price_as_of=price_as_of,
                est_daily_volume=_finite(
                    item.get("est_daily_volume_units"),
                    "EST_DAILY_VOLUME_UNITS",
                    positive=True,
                ),
                instrument_vol=_nonnegative(
                    item.get("instrument_vol"), "INSTRUMENT_VOL"
                ),
            ),
            strategy=StrategyApproval(
                strategy_id=intent.portfolio_id,
                approved=True,
                approval_id=policy_version,
            ),
            actor=Actor(actor_id=_WORKER, kind=ActorKind.SYSTEM),
            now=now_ms,
            max_staleness=max_staleness,
            recent_order_fingerprints=tuple(
                body.get("recent_order_fingerprints", ())
            ),
            recent_timestamps=tuple(body.get("recent_order_timestamps_ms", ())),
            recent_order_statuses=tuple(body.get("recent_order_statuses", ())),
            plan_turnover=_nonnegative(
                body.get("plan_turnover", 0.0), "PLAN_TURNOVER"
            ),
        )

    ledger = None
    try:
        ledger = LiveLedger(args.ledger_db, create=True)
        audit = DurableAuditLog(args.audit_db, create=True)
    except Exception as exc:
        if ledger is not None:
            ledger.close()
        raise _fail(_code("RUNTIME_STORE_OPEN_ERROR", exc)) from None
    return _PreparedRuntime(
        prices=prices,
        aum=aum,
        current_positions=current_positions,
        account_positions=account_positions,
        mandate_ceiling=mandate_ceiling,
        gate=gate,
        ctx_factory=ctx_factory,
        ledger=ledger,
        audit=audit,
    )


class _PreviewVenue:
    venue_mode = "testnet"
    venue_name = "binance"

    def get_order(self, instrument_id: str, client_order_id: str):
        del instrument_id, client_order_id
        raise RuntimeError("preview cannot query Testnet order recovery")


@dataclass(slots=True)
class ManagedRunner:
    service: TestnetExecutionService
    resources: tuple[object, ...]

    def close(self) -> None:
        for resource in reversed(self.resources):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _kill_switch_arm(path: str):
    def arm(reason: str, now_ms: int) -> None:
        store = open_authoritative_kill_switch_store(path)
        try:
            if store.load_state().tripped:
                return
            arm_and_record(
                store,
                Actor(actor_id=_WORKER, kind=ActorKind.SYSTEM),
                reason,
                at=now_ms,
            )
        finally:
            store.close()

    return arm


def build_runner(
    args: argparse.Namespace,
    intent: TestnetExecutionIntent,
    now_ms: int,
    environ: Mapping[str, str],
) -> ManagedRunner:
    resources: list[object] = []
    try:
        commands = SqliteExecutionCommandStore(args.command_db, create=True)
        resources.append(commands)
        recovery = SqliteOrderRecoveryStore(args.recovery_db, create=True)
        resources.append(recovery)
        prepared: _PreparedRuntime | None = None
        if args.testnet_submit:
            prepared = _prepare_runtime(args, intent, now_ms)
            resources.extend((prepared.ledger, prepared.audit))
            mandate = load_mandate(args.mandate)
            _require_mandate_within_system_policy(
                mandate, prepared.mandate_ceiling
            )
            api_key, api_secret = load_testnet_credentials(environ)
            venue = BinanceFuturesAdapter(
                api_key=api_key,
                api_secret=api_secret,
                base_url=BinanceFuturesAdapter.TESTNET_BASE,
            )
            pipeline_provider = lambda command, current_ms: prepared.pipeline_inputs()
        else:
            venue = _PreviewVenue()

            def pipeline_provider(command, current_ms):
                del command, current_ms
                raise RuntimeError("preview has no submission runtime")

        service = TestnetExecutionService(
            commands,
            recovery_store=recovery,
            venue=venue,
            authority_provider=_authority_provider(
                args,
                mandate_ceiling=(
                    None if prepared is None else prepared.mandate_ceiling
                ),
            ),
            pipeline_inputs_provider=pipeline_provider,
            max_market_age_ms=args.max_market_age_ms,
            lease_ms=args.lease_ms,
            kill_switch_arm=_kill_switch_arm(args.kill_switch),
        )
        return ManagedRunner(service=service, resources=tuple(resources))
    except Exception:
        ManagedRunner(service=None, resources=tuple(resources)).close()  # type: ignore[arg-type]
        raise


def _output(run: object, *, submit: bool) -> dict[str, object]:
    command = getattr(run, "command", None)
    state = getattr(command, "state", None)
    return {
        "status": str(getattr(run, "status", "ERROR")),
        "submission_mode": (
            "BINANCE_FUTURES_TESTNET" if submit else "PREVIEW"
        ),
        "command_state": getattr(state, "value", state),
        "reason_codes": list(getattr(command, "reason_codes", ())),
    }


def main(
    argv: list[str] | None = None,
    *,
    clock_ms: Callable[[], int] | None = None,
    runner_factory: Callable[..., ManagedRunner] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    mode = "BINANCE_FUTURES_TESTNET" if args.testnet_submit else "PREVIEW"
    runner: ManagedRunner | None = None
    try:
        validate_args(args)
        now_ms = (clock_ms or (lambda: int(time.time() * 1000)))()
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise _fail("CLOCK_INVALID")
        intent = _load_intent(args.intent)
        factory = runner_factory or build_runner
        runner = factory(args, intent, now_ms, environ or os.environ)
        runner.service.enqueue(intent, recorded_at_ms=now_ms)
        run = runner.service.run_once(
            now_ms=now_ms,
            worker_id=args.worker_id,
            submit_enabled=args.testnet_submit,
        )
        print(json.dumps(_output(run, submit=args.testnet_submit), sort_keys=True))
        return 0 if run.status in {"PREVIEW_READY", "COMPLETED", "NO_WORK"} else 2
    except CliConfigurationError as exc:
        print(
            json.dumps(
                {"status": "ERROR", "submission_mode": mode, "error_code": str(exc)},
                sort_keys=True,
            )
        )
        return 3
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "submission_mode": mode,
                    "error_code": _code("STARTUP_ERROR", exc),
                },
                sort_keys=True,
            )
        )
        return 3
    finally:
        if runner is not None:
            runner.close()


if __name__ == "__main__":
    sys.exit(main())
