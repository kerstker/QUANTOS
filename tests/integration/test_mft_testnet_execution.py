"""Wave 2 deterministic Testnet execution and recovery acceptance.

All venue behavior is local and deterministic.  No credential, network, or
mainnet adapter is used by this suite.
"""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from live.contracts import ExchangeFilters, VenueOrderResult
from live.execution.command_store import (
    ExecutionCommandState,
    SqliteExecutionCommandStore,
)
from live.execution.oms import OmsAuthority
from live.execution.order_event_store import OrderEventSource, SqliteOrderEventStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.execution.risk_supervisor import (
    RiskSupervisorSnapshot,
    RiskSupervisorVerdict,
    evaluate_risk_supervisor,
)
from live.execution.service import (
    ExecutionAuthoritySnapshot,
    TestnetExecutionService as ExecutionService,
)
from live.governance.mandate import Mandate
from live.governance.mandate_store import RiskSupervisorPolicy
from live.governance.validation_attestation import VALIDATION_PASS, ValidationAttestation
from live.pipeline import UNIT_SCALE
from quantos.contracts.hashing import stable_hash
from quantos.contracts.mft_execution import create_testnet_execution_intent
from quantos.contracts.portfolio import RebalancePlan, TargetPosition
from quantos.contracts.trading import OrderSide
from services.data.binance_stream_cursor import StreamCursorState, StreamCursorStore
from services.monitoring.audit_log import GlobalAuditLog
from services.monitoring.mft_parity import (
    MftParityPolicy,
    PaperDecisionLeg,
    TestnetFillObservation as FillObservation,
    TestnetOrderObservation as OrderObservation,
    build_mft_parity_report,
)
from services.monitoring.mft_read_model import (
    MFT_READ_MODEL_SECTIONS,
    MftFreshnessPolicy,
    MftReadModelStore,
)
from services.portfolio.mft_intent import (
    MftPortfolioEvidenceRef,
    MftSleeveRef,
    project_mft_portfolio_intent,
)
from services.portfolio.mft_intent_store import SqliteMftIntentOutbox
from services.portfolio.portfolio_constraints import PortfolioConstraintInputs
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import (
    AccountRiskState,
    MarketContext,
    RiskContext,
    StrategyApproval,
)
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch


NOW = 1_800_000_000_000
INSTRUMENT = "BTCUSDT"
PRICE = 50_000.0
PORTFOLIO = "hourly-paper"
ALPHA = "a" * 64
TRIAL = "b" * 64
WORKFLOW = "1" * 64
VALIDATION = "2" * 64
FORWARD = "3" * 64
PANEL = "4" * 64
POLICY_HASH = "5" * 64


class DeterministicTestnetVenue:
    venue_mode = "testnet"
    venue_name = "binance"

    def __init__(self, *, fill_ratio: float = 1.0, mode: str = "OK") -> None:
        self.fill_ratio = fill_ratio
        self.mode = mode
        self.place_calls = []
        self.query_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self._orders: dict[str, VenueOrderResult] = {}

    def exchange_filters(self):
        if self.mode == "429":
            raise RuntimeError("simulated 429")
        if self.mode == "TIMEOUT":
            raise TimeoutError("simulated timeout")
        return {
            INSTRUMENT: ExchangeFilters(
                instrument_id=INSTRUMENT,
                step_size=0.0001,
                min_qty=0.0001,
                min_notional=5.0,
            )
        }

    def place_order(self, order):
        self.place_calls.append(order)
        quantity = order.quantity * self.fill_ratio
        final_status = "FILLED" if self.fill_ratio == 1.0 else "PARTIALLY_FILLED"
        final = VenueOrderResult(
            venue_order_id=f"venue-{order.client_order_id}",
            client_order_id=order.client_order_id,
            instrument_id=order.instrument_id,
            status=final_status,
            filled_qty=quantity,
            avg_price=PRICE,
        )
        self._orders[order.client_order_id] = final
        if self.mode == "ACK_LOST":
            raise TimeoutError("simulated ack loss")
        return VenueOrderResult(
            venue_order_id=final.venue_order_id,
            client_order_id=order.client_order_id,
            instrument_id=order.instrument_id,
            status="NEW",
            filled_qty=0.0,
            avg_price=0.0,
        )

    def get_order(self, instrument_id: str, client_order_id: str):
        del instrument_id
        self.query_calls.append(client_order_id)
        if self.mode == "QUERY_TIMEOUT":
            raise TimeoutError("simulated query timeout")
        return self._orders.get(client_order_id)

    def cancel_order(self, instrument_id: str, client_order_id: str):
        self.cancel_calls.append(client_order_id)
        current = self._orders.get(client_order_id)
        if current is None:
            return None
        canceled = VenueOrderResult(
            venue_order_id=current.venue_order_id,
            client_order_id=client_order_id,
            instrument_id=instrument_id,
            status="CANCELED",
            filled_qty=current.filled_qty,
            avg_price=current.avg_price,
        )
        self._orders[client_order_id] = canceled
        return canceled

    def positions(self):
        return {}

    def account(self):
        return {}


class FailingAfterPlaceLedger:
    """Allow the durable pre-journal, then lose the post-venue projection."""

    def __init__(self, inner: LiveLedger) -> None:
        self._inner = inner

    def record_submission(self, *args, **kwargs):
        return self._inner.record_submission(*args, **kwargs)

    def update_status(self, *args, **kwargs):
        raise RuntimeError("simulated post-place ledger failure")

    def record_fill(self, *args, **kwargs):
        return self._inner.record_fill(*args, **kwargs)

    def position(self, instrument_id):
        return self._inner.position(instrument_id)

    def all_positions(self):
        return self._inner.all_positions()


def _risk_limits() -> RiskLimits:
    return RiskLimits(
        allowed_instruments=frozenset({INSTRUMENT}),
        allowed_accounts=frozenset({"testnet-account"}),
        default_max_position_qty=10**13,
        max_order_notional=1e12,
        fat_finger_qty=10**13,
        max_gross_exposure=1e12,
        max_net_exposure=1e12,
        max_leverage=1e6,
        max_single_name_pct=1.0,
        max_turnover=1e6,
        max_participation_rate=1.0,
        max_instrument_vol=5.0,
        daily_loss_limit=1e12,
        max_drawdown=1.0,
    )


def _gate_context(order) -> RiskContext:
    return RiskContext(
        account=AccountRiskState(
            account_id="testnet-account",
            cash=1_000_000.0,
            equity=1_000_000.0,
            day_pnl=0.0,
            peak_equity=1_000_000.0,
            positions=(),
        ),
        market=MarketContext(
            instrument_id=order.instrument_id,
            known=True,
            reference_price=PRICE / UNIT_SCALE,
            market_open=True,
            price_as_of=NOW,
            est_daily_volume=1e12,
            instrument_vol=0.5,
        ),
        strategy=StrategyApproval(
            strategy_id=PORTFOLIO,
            approved=True,
            approval_id="wave2-integration",
        ),
        now=NOW,
        max_staleness=1_000,
    )


def _mandate(*, expires_at: int = NOW + 60_000) -> Mandate:
    return Mandate(
        mandate_id="wave2-human-testnet-mandate",
        venue="testnet",
        exchange="binance",
        allowed_instruments=frozenset({INSTRUMENT}),
        max_single_order_notional=1_000_000.0,
        max_gross_notional=1_000_000.0,
        max_net_notional=1_000_000.0,
        approved_by=("human-test-operator",),
        issued_at=NOW - 1_000,
        expires_at=expires_at,
    )


def _direct_intent(*, suffix: str = "", expires_at_ms: int = NOW + 60_000):
    return create_testnet_execution_intent(
        portfolio_id=PORTFOLIO + suffix,
        alpha_ids=(ALPHA,),
        decided_at_ms=NOW - 10,
        expires_at_ms=expires_at_ms,
        targets=(TargetPosition(instrument_id=INSTRUMENT, target_weight=0.1),),
        market_watermark_ms=NOW - 50,
        market_snapshot_hash="9" * 64,
        workflow_receipt_hash=WORKFLOW,
        validation_artifact_hashes=(VALIDATION,),
        policy_version="mft-paper-intent.v1",
        policy_hash=POLICY_HASH,
        testnet_submission_requested=True,
    )


def _pipeline_inputs(ledger):
    return {
        "prices": {INSTRUMENT: PRICE},
        "aum": 100_000.0,
        "current_positions": {},
        "account_positions": {},
        "gate": PreTradeRiskGate(_risk_limits(), kill_switch=KillSwitch()),
        "ctx_factory": _gate_context,
        "ledger": ledger,
        "audit": GlobalAuditLog(),
        "max_confirm_polls": 1,
        "poll_sleep": lambda: None,
    }


def _market_authority(tmp_path):
    cursor = StreamCursorStore(str(tmp_path / "cursor.db"), create=True)
    cursor.observe(
        channel="depth",
        instrument_id=INSTRUMENT,
        first_update_id=100,
        final_update_id=110,
        previous_final_update_id=99,
        event_time_ms=NOW - 50,
        observed_at_ms=NOW - 40,
    )
    watermark = cursor.trading_watermark((("depth", INSTRUMENT),))
    assert watermark["publishable"] is True
    return cursor, watermark


def _project_intent(tmp_path, watermark):
    outbox = SqliteMftIntentOutbox(str(tmp_path / "intent-outbox.db"), create=True)
    sleeve = MftSleeveRef(
        alpha_id=ALPHA,
        trial_id=TRIAL,
        workflow_state="PAPER",
        workflow_receipt_hash=WORKFLOW,
        validation_artifact_hash=VALIDATION,
        forward_decision_hash=FORWARD,
        panel_digest=PANEL,
        policy_digest=POLICY_HASH,
        decided_at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    evidence = tuple(
        MftPortfolioEvidenceRef(
            kind=kind,
            status="PASS",
            evidence_hash=digest,
            as_of_ms=NOW - 50,
            expires_at_ms=NOW + 60_000,
        )
        for kind, digest in (
            ("CAPACITY", "6" * 64),
            ("CORRELATION", "7" * 64),
            ("COST", "8" * 64),
        )
    )
    plan = RebalancePlan(
        plan_id="wave2-plan",
        portfolio_id=PORTFOLIO,
        targets=(TargetPosition(instrument_id=INSTRUMENT, target_weight=0.1),),
        turnover=0.1,
    )
    projection = project_mft_portfolio_intent(
        outbox,
        cycle_id="wave2-cycle",
        portfolio_id=PORTFOLIO,
        sleeves=(sleeve,),
        plan=plan,
        current_weights={},
        constraints=PortfolioConstraintInputs(
            scenarios=((-0.01,), (0.01,)),
            scenario_symbols=(INSTRUMENT,),
            tail_budget=0.1,
        ),
        evidence_refs=evidence,
        market_watermark_ms=watermark["watermark_ms"],
        market_snapshot_hash=watermark["chain"]["head"],
        decided_at_ms=NOW,
        recorded_at_ms=NOW + 1,
        max_market_age_ms=1_000,
        policy_version="mft-paper-intent.v1",
        expected_workflow_receipt_hash=WORKFLOW,
        expected_validation_artifact_hashes=(VALIDATION,),
        expected_forward_decision_hashes=(FORWARD,),
        expected_panel_digest=PANEL,
    )
    assert projection.status == "READY"
    assert projection.intent is not None
    preview_intent = projection.intent
    submit_intent = create_testnet_execution_intent(
        portfolio_id=preview_intent.portfolio_id,
        alpha_ids=preview_intent.alpha_ids,
        decided_at_ms=preview_intent.decided_at_ms,
        expires_at_ms=preview_intent.expires_at_ms,
        targets=preview_intent.targets,
        market_watermark_ms=preview_intent.market_watermark_ms,
        market_snapshot_hash=preview_intent.market_snapshot_hash,
        workflow_receipt_hash=preview_intent.workflow_receipt_hash,
        validation_artifact_hashes=preview_intent.validation_artifact_hashes,
        policy_version=preview_intent.policy_version,
        policy_hash=preview_intent.policy_hash,
        testnet_submission_requested=True,
    )
    return outbox, projection, submit_intent


def _authority(intent, *, mandate: Mandate | None = None):
    return ExecutionAuthoritySnapshot(
        workflow_receipt_hash=intent.workflow_receipt_hash,
        validation_artifact_hashes=intent.validation_artifact_hashes,
        market_snapshot_hash=intent.market_snapshot_hash,
        market_watermark_ms=intent.market_watermark_ms,
        market_contiguous=True,
        policy_version=intent.policy_version,
        policy_hash=intent.policy_hash,
        kill_switch_tripped=False,
        mandate=_mandate() if mandate is None else mandate,
        validation_attestation=ValidationAttestation(
            strategy_id=PORTFOLIO,
            kind=VALIDATION_PASS,
            attested_by="human-validator",
            attested_at=NOW - 500,
            validation_result_hash=VALIDATION,
        ),
    )


def _read_sections(*, watermark, run, oms, ledger, risk, parity):
    oms_models = {
        key: asdict(value) for key, value in sorted(oms.replay().items())
    }
    return {
        "market_gap": {
            "status": "CONTIGUOUS",
            "watermark_ms": watermark["watermark_ms"],
            "chain_head": watermark["chain"]["head"],
        },
        "scheduler": {"status": "COMPLETED", "last_success_ms": NOW},
        "intent_funnel": {
            "received": 1,
            "validated": 1,
            "completed": 1,
            "blocked": 0,
        },
        "oms": {"status": "REPLAYED", "orders": oms_models},
        "fills": {"count": len(ledger.fills()), "items": ledger.fills()},
        "positions": {"status": "RECONCILED", "items": ledger.all_positions()},
        "risk": {
            "status": risk.verdict.value,
            "reasons": list(risk.reasons),
            "kill_switch_latched": risk.latch_kill_switch,
        },
        "reconciliation": risk.reconciliation,
        "tca": {
            "status": parity["status"],
            "report_id": parity["report_id"],
            "fill_ratio": parity["aggregate"]["fill_ratio"],
            "microstructure_scope": parity["microstructure_scope"],
        },
        "mandate_expiry": {
            "status": "VALID_TESTNET_ONLY",
            "expires_at_ms": _mandate().expires_at,
        },
    }


def test_fake_venue_full_flow_replays_exactly_and_is_economically_idempotent(tmp_path):
    cursor, watermark = _market_authority(tmp_path)
    outbox, projection, intent = _project_intent(tmp_path, watermark)
    venue = DeterministicTestnetVenue()
    commands = SqliteExecutionCommandStore(str(tmp_path / "commands.db"), create=True)
    recovery = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    pipeline_ledger = LiveLedger(str(tmp_path / "pipeline-ledger.db"), create=True)
    audit = GlobalAuditLog()
    authority = _authority(intent)
    service = ExecutionService(
        commands,
        recovery_store=recovery,
        venue=venue,
        authority_provider=lambda command, now_ms: authority,
        pipeline_inputs_provider=lambda command, now_ms: {
            "prices": {INSTRUMENT: PRICE},
            "aum": 100_000.0,
            "current_positions": {},
            "account_positions": {},
            "gate": PreTradeRiskGate(_risk_limits(), kill_switch=KillSwitch()),
            "ctx_factory": _gate_context,
            "ledger": pipeline_ledger,
            "audit": audit,
            "max_confirm_polls": 1,
            "poll_sleep": lambda: None,
        },
        max_market_age_ms=1_000,
        lease_ms=5_000,
    )
    service.enqueue(intent, recorded_at_ms=NOW)

    run = service.run_once(now_ms=NOW, worker_id="wave2-worker", submit_enabled=True)
    rerun = service.run_once(
        now_ms=NOW + 1, worker_id="wave2-worker-replay", submit_enabled=True
    )

    assert run.status == "COMPLETED"
    assert rerun.status == "NO_WORK"
    assert len(venue.place_calls) == 1
    assert len(pipeline_ledger.fills()) == 1
    assert pipeline_ledger.all_positions()[INSTRUMENT] == pytest.approx(0.2)
    assert audit.chain_valid()

    events = SqliteOrderEventStore(str(tmp_path / "order-events.db"), create=True)
    oms_ledger = LiveLedger(str(tmp_path / "oms-ledger.db"), create=True)
    oms_recovery = SqliteOrderRecoveryStore(
        str(tmp_path / "oms-recovery.db"), create=True
    )
    oms = OmsAuthority(events, oms_ledger, recovery_store=oms_recovery)
    order = venue.place_calls[0]
    final = venue.get_order(order.instrument_id, order.client_order_id)
    assert final is not None
    oms.register_submission(
        client_order_id=order.client_order_id,
        instrument_id=order.instrument_id,
        side=order.side,
        order_quantity=order.quantity,
        strategy_id=PORTFOLIO,
        order_type="MARKET",
        limit_price=None,
        observed_at_ms=NOW,
        command_event_id=f"command-{order.client_order_id}",
    )
    payload = {
        "status": final.status,
        "executedQty": final.filled_qty,
        "avgPrice": final.avg_price,
    }
    oms.ingest_venue_snapshot(
        client_order_id=order.client_order_id,
        source=OrderEventSource.REST_POLL,
        source_sequence=1,
        venue_event_id=f"fill-{order.client_order_id}",
        venue_order_id=final.venue_order_id,
        venue_status=final.status,
        cumulative_filled_qty=final.filled_qty,
        avg_fill_price=final.avg_price,
        observed_at_ms=NOW + 2,
        venue_payload_hash=stable_hash(payload),
    )
    event_count = events.row_count()
    fill_count = len(oms_ledger.fills())
    oms.ingest_venue_snapshot(
        client_order_id=order.client_order_id,
        source=OrderEventSource.REST_POLL,
        source_sequence=1,
        venue_event_id=f"fill-{order.client_order_id}",
        venue_order_id=final.venue_order_id,
        venue_status=final.status,
        cumulative_filled_qty=final.filled_qty,
        avg_fill_price=final.avg_price,
        observed_at_ms=NOW + 2,
        venue_payload_hash=stable_hash(payload),
    )
    assert events.row_count() == event_count
    assert len(oms_ledger.fills()) == fill_count == 1
    assert oms_ledger.all_positions() == pipeline_ledger.all_positions()

    supervisor_policy = RiskSupervisorPolicy(
        schema_version=1,
        policy_id="wave2-integration-risk",
        policy_version="1",
        max_market_age_ms=1_000,
        max_scheduler_age_ms=1_000,
        max_drawdown_pct=0.5,
        min_margin_ratio=1.0,
        max_single_name_fraction=1.0,
        max_participation_rate=1.0,
        reconciliation_tolerance=1e-9,
    )
    positions = oms_ledger.all_positions()
    risk = evaluate_risk_supervisor(
        RiskSupervisorSnapshot(
            now_ms=NOW + 3,
            execution_environment=intent.execution_environment,
            venue="testnet",
            exchange="binance",
            market_publishable=True,
            market_watermark_ms=watermark["watermark_ms"],
            scheduler_last_success_ms=NOW,
            mandate=_mandate(),
            account_equity=1_000_000.0,
            peak_equity=1_000_000.0,
            positions=positions,
            prices={INSTRUMENT: PRICE},
            maint_margin_balance=10_000.0,
            margin_maint_amount=100.0,
            participation_rates={INSTRUMENT: 0.01},
            ledger_positions=positions,
            unknown_order_count=0,
            kill_switch_tripped=False,
            previous_cycle_at_ms=NOW - 1,
        ),
        supervisor_policy,
    )
    assert risk.verdict is RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK

    paper_leg = PaperDecisionLeg(
        intent_id=intent.intent_hash,
        decision_id=projection.cycle_id,
        client_order_id=order.client_order_id,
        decided_at_ms=NOW,
        instrument_id=INSTRUMENT,
        side=OrderSide.BUY,
        target_signed_quantity=order.quantity,
        request_quantity=order.quantity,
        decision_price=PRICE,
    )
    testnet_order = OrderObservation(
        intent_id=intent.intent_hash,
        decision_id=projection.cycle_id,
        client_order_id=order.client_order_id,
        decided_at_ms=NOW,
        instrument_id=INSTRUMENT,
        side=OrderSide.BUY,
        target_signed_quantity=order.quantity,
        request_quantity=order.quantity,
        submitted_at_ms=NOW + 1,
        order_status=final.status,
        fills=(
            FillObservation(
                fill_id=f"fill-{order.client_order_id}",
                quantity=final.filled_qty,
                price=final.avg_price,
                filled_at_ms=NOW + 2,
            ),
        ),
    )
    parity = build_mft_parity_report(
        canonical_run_id=intent.command_id,
        paper_legs=(paper_leg,),
        testnet_orders=(testnet_order,),
        paper_positions=positions,
        testnet_positions=positions,
        paper_equity=1_000_000.0,
        testnet_equity=1_000_000.0,
        testnet_fee_total=1.0,
        testnet_funding_total=0.0,
        policy=MftParityPolicy(
            policy_id="wave2-integration-parity",
            policy_version="1",
            max_target_quantity_diff=1e-9,
            max_request_quantity_diff=1e-9,
            min_fill_ratio=1.0,
            max_abs_slippage_bps=1.0,
            max_latency_ms=10,
            max_position_drift=1e-9,
            max_abs_equity_drift=1e-9,
        ),
        as_of_ms=NOW + 3,
    )
    assert parity["status"] == "PASS"
    assert parity["microstructure_scope"] == "TESTNET_MICROSTRUCTURE_NOT_PRODUCTION"

    sections = _read_sections(
        watermark=watermark,
        run=run,
        oms=oms,
        ledger=oms_ledger,
        risk=risk,
        parity=parity,
    )
    freshness = MftFreshnessPolicy("wave2-integration", "1", 1_000)
    snapshots = []
    for suffix in ("authority", "replay"):
        with MftReadModelStore(
            str(tmp_path / f"read-model-{suffix}.db"), create=True
        ) as read_model:
            for offset, section in enumerate(MFT_READ_MODEL_SECTIONS):
                assert read_model.append_section(
                    section,
                    sections[section],
                    event_at_ms=NOW + 10 + offset,
                    event_id=f"wave2-{offset}",
                ) is True
            if suffix == "authority":
                for offset, section in enumerate(MFT_READ_MODEL_SECTIONS):
                    assert read_model.append_section(
                        section,
                        sections[section],
                        event_at_ms=NOW + 10 + offset,
                        event_id=f"wave2-{offset}",
                    ) is False
            snapshots.append(
                read_model.snapshot(
                    as_of_ms=NOW + 100,
                    freshness_policy=freshness,
                )
            )
    assert snapshots[0] == snapshots[1]
    assert snapshots[0]["snapshot_status"] == "REAL"
    assert snapshots[0]["watermark"]["sequence"] == len(MFT_READ_MODEL_SECTIONS)

    assert outbox.row_count() == 1
    assert commands.row_count() >= 1
    assert events.chain_valid()
    cursor.close()
    outbox.close()
    commands.close()
    recovery.close()
    pipeline_ledger.close()
    events.close()
    oms_ledger.close()
    oms_recovery.close()


@pytest.mark.parametrize("failure", ("429", "TIMEOUT"))
def test_market_recovery_failure_never_publishes_a_trading_watermark(tmp_path, failure):
    cursor = StreamCursorStore(str(tmp_path / f"cursor-{failure}.db"), create=True)
    cursor.observe(
        channel="depth",
        instrument_id=INSTRUMENT,
        first_update_id=100,
        final_update_id=110,
        previous_final_update_id=99,
        event_time_ms=NOW,
        observed_at_ms=NOW,
    )
    duplicate = cursor.observe(
        channel="depth",
        instrument_id=INSTRUMENT,
        first_update_id=100,
        final_update_id=110,
        previous_final_update_id=99,
        event_time_ms=NOW,
        observed_at_ms=NOW,
    )
    assert duplicate.state is StreamCursorState.CONTIGUOUS
    assert cursor.event_count() == 1
    gap = cursor.observe(
        channel="depth",
        instrument_id=INSTRUMENT,
        first_update_id=120,
        final_update_id=130,
        previous_final_update_id=118,
        event_time_ms=NOW + 1,
        observed_at_ms=NOW + 1,
    )
    assert gap.state is StreamCursorState.GAP_DETECTED
    cursor.begin_recovery("depth", INSTRUMENT, observed_at_ms=NOW + 2)
    blocked = cursor.fail_recovery(
        "depth",
        INSTRUMENT,
        observed_at_ms=NOW + 3,
        error_code=f"REST_{failure}",
    )
    watermark = cursor.trading_watermark((("depth", INSTRUMENT),))
    assert blocked.state is StreamCursorState.BLOCKED
    assert watermark["publishable"] is False
    assert watermark["watermark_ms"] is None
    cursor.close()


def test_out_of_order_market_and_partial_cancel_race_remain_blocked_and_idempotent(tmp_path):
    cursor = StreamCursorStore(str(tmp_path / "cursor.db"), create=True)
    cursor.observe(
        channel="depth",
        instrument_id=INSTRUMENT,
        first_update_id=1,
        final_update_id=10,
        previous_final_update_id=0,
        event_time_ms=NOW,
        observed_at_ms=NOW,
    )
    reversed_time = cursor.observe(
        channel="depth",
        instrument_id=INSTRUMENT,
        first_update_id=11,
        final_update_id=20,
        previous_final_update_id=10,
        event_time_ms=NOW - 1,
        observed_at_ms=NOW + 1,
    )
    assert reversed_time.state is StreamCursorState.BLOCKED
    assert cursor.trading_watermark((("depth", INSTRUMENT),))["publishable"] is False

    events = SqliteOrderEventStore(str(tmp_path / "events.db"), create=True)
    ledger = LiveLedger(str(tmp_path / "ledger.db"), create=True)
    recovery = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    oms = OmsAuthority(events, ledger, recovery_store=recovery)
    oms.register_submission(
        client_order_id="partial-cancel",
        instrument_id=INSTRUMENT,
        side=OrderSide.BUY,
        order_quantity=1.0,
        strategy_id=PORTFOLIO,
        order_type="MARKET",
        limit_price=None,
        observed_at_ms=NOW,
        command_event_id="partial-cancel-command",
    )
    for sequence, status, cumulative in (
        (1, "PARTIALLY_FILLED", 0.4),
        (2, "CANCELED", 0.6),
        (2, "CANCELED", 0.6),
    ):
        payload = {"status": status, "executedQty": cumulative, "avgPrice": PRICE}
        oms.ingest_venue_snapshot(
            client_order_id="partial-cancel",
            source=OrderEventSource.REST_POLL,
            source_sequence=sequence,
            venue_event_id=f"partial-cancel-{sequence}",
            venue_order_id="venue-partial-cancel",
            venue_status=status,
            cumulative_filled_qty=cumulative,
            avg_fill_price=PRICE,
            observed_at_ms=NOW + sequence,
            venue_payload_hash=stable_hash(payload),
        )
    assert sum(item["quantity"] for item in ledger.fills()) == pytest.approx(0.6)
    assert len(ledger.fills()) == 2
    assert events.row_count() == 3
    assert ledger.orders()[0]["canonical_state"] == "CANCELLED"
    cursor.close()
    events.close()
    ledger.close()
    recovery.close()


@pytest.mark.parametrize(
    ("failure", "first_status"),
    (("ACK_LOST", "COMPLETED"), ("LEDGER_FAILURE", "UNKNOWN")),
)
def test_ack_loss_and_post_place_ledger_failure_never_duplicate_economic_effects(
    tmp_path, failure, first_status
):
    intent = _direct_intent()
    venue = DeterministicTestnetVenue(
        mode="ACK_LOST" if failure == "ACK_LOST" else "OK"
    )
    commands = SqliteExecutionCommandStore(
        str(tmp_path / f"commands-{failure}.db"), create=True
    )
    recovery = SqliteOrderRecoveryStore(
        str(tmp_path / f"recovery-{failure}.db"), create=True
    )
    inner_ledger = LiveLedger(
        str(tmp_path / f"ledger-{failure}.db"), create=True
    )
    ledger = (
        FailingAfterPlaceLedger(inner_ledger)
        if failure == "LEDGER_FAILURE"
        else inner_ledger
    )
    authority = _authority(intent)
    service = ExecutionService(
        commands,
        recovery_store=recovery,
        venue=venue,
        authority_provider=lambda command, now_ms: authority,
        pipeline_inputs_provider=lambda command, now_ms: _pipeline_inputs(ledger),
        max_market_age_ms=1_000,
        lease_ms=5_000,
    )
    commands.enqueue(intent, recorded_at_ms=NOW)

    first = service.run_once(
        now_ms=NOW, worker_id=f"worker-{failure}", submit_enabled=True
    )
    second = service.run_once(
        now_ms=NOW + 1,
        worker_id=f"worker-{failure}-restart",
        submit_enabled=True,
    )

    assert first.status == first_status
    assert len(venue.place_calls) == 1
    assert venue.query_calls
    assert second.status in {"NO_WORK", "RECOVERY_BLOCKED"}
    assert len(venue.place_calls) == 1
    if failure == "LEDGER_FAILURE":
        assert first.command is not None
        assert first.command.state is ExecutionCommandState.UNKNOWN
    commands.close()
    recovery.close()
    inner_ledger.close()


def test_expired_in_flight_process_crash_blocks_the_next_command_without_submission(tmp_path):
    crashed = _direct_intent()
    following = _direct_intent(suffix="-next")
    venue = DeterministicTestnetVenue()
    commands = SqliteExecutionCommandStore(str(tmp_path / "commands.db"), create=True)
    recovery = SqliteOrderRecoveryStore(str(tmp_path / "recovery.db"), create=True)
    crashed_record = commands.enqueue(crashed, recorded_at_ms=NOW - 9)
    commands.mark_validated(crashed_record.command_id, recorded_at_ms=NOW - 8)
    commands.claim(
        crashed_record.command_id,
        worker_id="dead-worker",
        now_ms=NOW - 7,
        lease_until_ms=NOW,
    )
    commands.enqueue(following, recorded_at_ms=NOW)
    calls = []

    def pipeline(**kwargs):
        calls.append(kwargs)
        raise AssertionError("crash recovery reached submission")

    service = ExecutionService(
        commands,
        recovery_store=recovery,
        venue=venue,
        authority_provider=lambda command, now_ms: _authority(command),
        pipeline_inputs_provider=lambda command, now_ms: {},
        pipeline_submitter=pipeline,
        max_market_age_ms=1_000,
        lease_ms=5_000,
    )
    result = service.run_once(
        now_ms=NOW, worker_id="restart-worker", submit_enabled=True
    )

    assert result.status == "RECOVERY_BLOCKED"
    assert commands.get(crashed.command_id).state is ExecutionCommandState.UNKNOWN
    assert calls == []
    assert venue.place_calls == []
    commands.close()
    recovery.close()


@pytest.mark.parametrize(
    ("case", "expected_status"),
    (
        ("NO_MANDATE", "BLOCKED"),
        ("MAINNET", "BLOCKED"),
        ("EXPIRED", "BLOCKED"),
        ("UNKNOWN_ORDER", "RECOVERY_BLOCKED"),
    ),
)
def test_structural_authority_failures_have_zero_submission_side_effects(
    tmp_path, case, expected_status
):
    intent = _direct_intent(expires_at_ms=NOW + 100)
    venue = DeterministicTestnetVenue()
    if case == "MAINNET":
        venue.venue_mode = "mainnet"
    elif case == "UNKNOWN_ORDER":
        venue.mode = "QUERY_TIMEOUT"
    commands = SqliteExecutionCommandStore(
        str(tmp_path / f"commands-{case}.db"), create=True
    )
    recovery = SqliteOrderRecoveryStore(
        str(tmp_path / f"recovery-{case}.db"), create=True
    )
    if case == "UNKNOWN_ORDER":
        recovery.register_unknown(
            client_order_id="unknown-before-start",
            deployment_id="previous-cycle",
            instrument_id=INSTRUMENT,
            side="BUY",
            quantity=0.1,
            now_ms=NOW - 100,
        )
    calls = []
    authority = _authority(intent)
    if case == "NO_MANDATE":
        authority = replace(authority, mandate=None)

    def pipeline(**kwargs):
        calls.append(kwargs)
        raise AssertionError("structural blocker reached pipeline")

    service = ExecutionService(
        commands,
        recovery_store=recovery,
        venue=venue,
        authority_provider=lambda command, now_ms: authority,
        pipeline_inputs_provider=lambda command, now_ms: {},
        pipeline_submitter=pipeline,
        max_market_age_ms=1_000,
        lease_ms=5_000,
    )
    commands.enqueue(intent, recorded_at_ms=NOW)
    result = service.run_once(
        now_ms=NOW + (1_000 if case == "EXPIRED" else 0),
        worker_id=f"worker-{case}",
        submit_enabled=True,
    )
    assert result.status == expected_status
    assert calls == []
    assert venue.place_calls == []
    commands.close()
    recovery.close()
