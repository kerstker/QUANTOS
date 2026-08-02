from __future__ import annotations

import json

import pytest

import tools.paper_realtime as paper_realtime
from services.execution.paper_execution_store import PaperExecutionStore
from tools.paper_realtime import (
    ALPHA_IDS,
    PublicMarkDataError,
    build_mark_snapshot,
    fetch_public_funding_history,
    fetch_public_mark_prices,
    funding_cash_flow,
    run_cycle,
)


def _row(alpha_id: str, *, decision_ts: int = 1000) -> dict:
    return {
        "alpha_id": alpha_id,
        "decision_ts": decision_ts,
        "run_id": f"run-{alpha_id}",
        "cost_bps": 10.0,
        "paper_execution": "PAPER_FILLED",
        "paper_positions": {"BTC": 1},
        "orders": [
            {
                "instrument": "BTC",
                "side": "BUY",
                "quantity": 1,
                "decision_mark_price": 100.0,
            }
        ],
    }


def test_print_summary_includes_paper_and_backtest_metrics(capsys):
    paper_realtime._print_summary(
        {
            "verdict": {
                "cycle_completed": True,
                "paper_mark_ready": True,
                "backtest_reference_present_for_all_three": True,
            },
            "persistence": {"new_snapshots": 3},
            "alphas": {
                "carry_oi_majors": {
                    "status": "PAPER_MARKED",
                    "equity": 9_950.0,
                    "net_pnl": -50.0,
                    "reconciliation_residual": 0.0,
                }
            },
            "backtests": {"carry_oi_majors": {"run_id": "run-1", "sharpe": 0.42}},
            "errors": {},
        }
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["alphas"]["carry_oi_majors"] == {
        "status": "PAPER_MARKED",
        "equity": 9_950.0,
        "net_pnl": -50.0,
        "funding_pnl": None,
        "funding_events": None,
        "funding_data_complete": None,
        "reconciliation_residual": 0.0,
    }
    assert payload["backtests"]["carry_oi_majors"] == {
        "run_id": "run-1",
        "sharpe": 0.42,
        "sharpe_vt": None,
        "max_drawdown": None,
    }


def _operator() -> dict:
    return {
        "verdict": {"operator_completed": True, "paper_preview_ready": True},
        "stages": {
            "three_alpha_paper_flow": {
                "flow": {
                    "stages": {
                        "authoritative_dry_runs": [
                            {
                                "alpha_id": alpha,
                                "status": "PASS",
                                "snapshot": {
                                    "run_id": f"bt-{alpha}",
                                    "n_periods": 12,
                                    "pnl": {
                                        "sharpe": 1.1,
                                        "sharpe_vt": 1.0,
                                        "max_drawdown": 0.2,
                                        "max_drawdown_vt": 0.1,
                                        "total_return": 0.3,
                                    },
                                    "reconciliation_residual": 0.0,
                                    "research_risk_decision": "RISK_APPROVED",
                                },
                            }
                            for alpha in ALPHA_IDS
                        ]
                    }
                }
            }
        },
    }


def test_public_mark_provider_accepts_only_requested_symbols():
    payload = [
        {"symbol": "BTCUSDT", "markPrice": "100.0", "time": 2000},
        {"symbol": "ETHUSDT", "markPrice": "200.0", "time": 1999},
        {"symbol": "DOGEUSDT", "markPrice": "0.1", "time": 2000},
    ]
    result = fetch_public_mark_prices(("BTC", "ETH"), http_get=lambda _url: payload)
    assert result["observed_at"] == 2000
    assert result["marks"] == {"BTC": 100.0, "ETH": 200.0}
    assert result["private_access"] is False


def test_public_mark_provider_fails_closed_on_partial_response():
    with pytest.raises(PublicMarkDataError, match="missing"):
        fetch_public_mark_prices(
            ("BTC", "ETH"),
            http_get=lambda _url: [{"symbol": "BTCUSDT", "markPrice": "100", "time": 2}],
        )


def test_public_funding_provider_extracts_requested_series_and_pit_marks():
    payload = [
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.01",
            "fundingTime": 2000,
            "markPrice": "100.0",
        },
        {
            "symbol": "ETHUSDT",
            "fundingRate": "-0.02",
            "fundingTime": 1999,
            "markPrice": "200.0",
        },
        {
            "symbol": "DOGEUSDT",
            "fundingRate": "0.03",
            "fundingTime": 1998,
            "markPrice": "0.1",
        },
    ]
    urls: list[str] = []
    result = fetch_public_funding_history(
        ("BTC", "ETH"),
        http_get=lambda url: urls.append(url) or payload,
    )
    assert "limit=1000" in urls[0]
    assert result["provider"] == "BINANCE_PUBLIC_FUNDING_RATE"
    assert result["rates"] == {"BTC": {"2000": 0.01}, "ETH": {"1999": -0.02}}
    assert result["marks"] == {"BTC": {"2000": 100.0}, "ETH": {"1999": 200.0}}
    assert result["private_access"] is False


def test_funding_cash_flow_flags_stale_series_for_open_position():
    fill = paper_realtime._paper_fills([_row("crowd_contra")], alpha_id="crowd_contra")[0]
    observed_at = paper_realtime.MAX_FUNDING_EVENT_AGE_MS + 3000
    result = funding_cash_flow(
        [fill],
        funding_rates={"BTC": {"2000": 0.01}},
        funding_marks={"BTC": {"2000": 100.0}},
        observed_at=observed_at,
    )
    assert result["data_available"] is False
    assert "BTC@after:2000" in result["missing_events"]


def test_build_mark_snapshot_reconciles_current_equity():
    result = build_mark_snapshot(
        "crowd_contra",
        [_row("crowd_contra")],
        marks={"BTC": 110.0},
        observed_at=2000,
        source_times={"BTC": 2000},
        aum=1000.0,
    )
    assert result["status"] == "PAPER_MARKED"
    assert result["positions"] == {"BTC": 1}
    assert result["equity"] == pytest.approx(1009.9)
    assert result["net_pnl"] == pytest.approx(9.9)
    assert result["reconciliation_residual"] == pytest.approx(0.0)
    assert result["paper_only"] is True


def test_funding_cash_flow_uses_signed_position_and_pit_mark():
    fills = [
        paper_realtime._paper_fills([_row("crowd_contra")], alpha_id="crowd_contra")[0],
        paper_realtime._paper_fills(
            [{**_row("crowd_contra", decision_ts=3000), "run_id": "second", "orders": [{
                "instrument": "BTC", "side": "SELL", "quantity": 1, "decision_mark_price": 100.0,
            }]}],
            alpha_id="crowd_contra",
        )[0],
    ]
    result = funding_cash_flow(
        fills,
        funding_rates={"BTC": {"2000": 0.01, "4000": 0.01}},
        funding_marks={"BTC": {"2000": 100.0, "4000": 100.0}},
        observed_at=5000,
    )
    # Long one BTC pays 1 at t=2000; after the sell it is flat, so no second
    # payment is charged.  The strict fill/event boundary is intentional.
    assert result["total"] == pytest.approx(-1.0)
    assert result["events"] == 1
    assert result["data_available"] is True


def test_build_mark_snapshot_includes_funding_without_breaking_reconciliation():
    result = build_mark_snapshot(
        "crowd_contra",
        [_row("crowd_contra")],
        marks={"BTC": 110.0},
        observed_at=3000,
        source_times={"BTC": 3000},
        aum=1000.0,
        funding_rates={"BTC": {"2000": 0.01}},
        funding_marks={"BTC": {"2000": 100.0}},
    )
    assert result["funding_pnl"] == pytest.approx(-1.0)
    assert result["equity"] == pytest.approx(1008.9)
    assert result["net_pnl"] == pytest.approx(8.9)
    assert result["funding_events"] == 1
    assert result["funding_data_complete"] is True
    assert result["reconciliation_residual"] == pytest.approx(0.0)


def test_build_mark_snapshot_reconstructs_multiple_rebalances():
    second = _row("crowd_contra", decision_ts=2000)
    second["run_id"] = "run-crowd-contra-2"
    second["paper_positions"] = {}
    second["orders"] = [
        {
            "instrument": "BTC",
            "side": "SELL",
            "quantity": 1,
            "decision_mark_price": 120.0,
        }
    ]
    result = build_mark_snapshot(
        "crowd_contra",
        [_row("crowd_contra"), second],
        marks={"BTC": 130.0},
        observed_at=3000,
        source_times={"BTC": 3000},
        aum=1000.0,
    )
    assert result["positions"] == {}
    assert result["realized_pnl"] == pytest.approx(20.0)
    assert result["unrealized_pnl"] == pytest.approx(0.0)
    assert result["costs"] == pytest.approx(0.22)
    assert result["net_pnl"] == pytest.approx(19.78)
    assert result["reconciliation_residual"] == pytest.approx(0.0)


def test_run_cycle_persists_three_alpha_marks_and_backtests(tmp_path):
    paper_path = tmp_path / "paper-orders.db"
    order_store = PaperExecutionStore(str(paper_path), create=True)
    try:
        for alpha in ALPHA_IDS:
            order_store.append_preview(_row(alpha))
    finally:
        order_store.close()

    def provider(instruments):
        assert tuple(instruments) == ("BTC",)
        return {
            "provider": "BINANCE_PUBLIC_MARK_PRICE",
            "private_access": False,
            "observed_at": 2000,
            "marks": {"BTC": 110.0},
            "source_times": {"BTC": 2000},
        }

    report = run_cycle(
        operator=_operator(),
        mark_provider=provider,
        paper_store_path=str(paper_path),
        realtime_store_path=str(tmp_path / "realtime.db"),
        output_path=str(tmp_path / "status.json"),
        aum=1000.0,
        generated_at_ms=3000,
    )
    assert report["verdict"]["cycle_completed"] is True
    assert report["verdict"]["paper_mark_ready"] is True
    assert report["verdict"]["backtest_reference_present_for_all_three"] is True
    assert report["persistence"]["new_snapshots"] == 3
    assert report["persistence"]["chain_valid"] is True
    assert report["paper_ledger"] == {
        "n_rows": 3,
        "n_decisions": 1,
        "latest_decision_ts": 1000,
    }
    assert len(report["history"]) == 3
    assert [point["alpha_id"] for point in report["history"]] == list(ALPHA_IDS)
    assert all(row["status"] == "PAPER_MARKED" for row in report["alphas"].values())
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))["verdict"]["live_allowed"] is False


def test_run_cycle_marks_existing_fills_when_new_operator_is_blocked(tmp_path):
    """A blocked new decision must not stop observation of immutable paper fills."""
    paper_path = tmp_path / "paper-orders.db"
    order_store = PaperExecutionStore(str(paper_path), create=True)
    try:
        for alpha in ALPHA_IDS:
            order_store.append_preview(_row(alpha))
    finally:
        order_store.close()

    blocked_operator = _operator()
    blocked_operator["verdict"] = {
        "operator_completed": False,
        "paper_preview_ready": False,
    }
    report = run_cycle(
        operator=blocked_operator,
        mark_provider=lambda _instruments: {
            "provider": "BINANCE_PUBLIC_MARK_PRICE",
            "observed_at": 2000,
            "marks": {"BTC": 110.0},
            "source_times": {"BTC": 2000},
        },
        paper_store_path=str(paper_path),
        realtime_store_path=str(tmp_path / "realtime.db"),
        output_path=str(tmp_path / "status.json"),
        aum=1000.0,
        generated_at_ms=3000,
    )

    assert report["operator_mode"] == paper_realtime.MARK_ONLY_REPLAY_REASON
    assert report["readiness_class"] == "PAPER_MARKS_ONLY_REPLAY"
    assert report["verdict"]["cycle_completed"] is True
    assert report["verdict"]["new_paper_orders_allowed"] is False
    assert report["verdict"]["live_allowed"] is False
    assert report["persistence"]["chain_valid"] is True
    assert all(row["status"] == "PAPER_MARKED" for row in report["alphas"].values())


def test_run_cycle_fails_closed_on_stale_public_marks(tmp_path):
    paper_path = tmp_path / "paper-orders.db"
    order_store = PaperExecutionStore(str(paper_path), create=True)
    try:
        for alpha in ALPHA_IDS:
            order_store.append_preview(_row(alpha))
    finally:
        order_store.close()

    report = run_cycle(
        operator=_operator(),
        mark_provider=lambda _instruments: {
            "provider": "BINANCE_PUBLIC_MARK_PRICE",
            "observed_at": 1,
            "marks": {"BTC": 110.0},
            "source_times": {"BTC": 1},
        },
        paper_store_path=str(paper_path),
        realtime_store_path=str(tmp_path / "realtime.db"),
        output_path=str(tmp_path / "status.json"),
        aum=1000.0,
        generated_at_ms=1_000_000,
    )
    assert report["verdict"]["cycle_completed"] is False
    assert "stale" in report["errors"]["mark_cycle"]
    assert report["verdict"]["live_allowed"] is False


def test_run_cycle_does_not_claim_complete_without_all_backtest_references(tmp_path):
    paper_path = tmp_path / "paper-orders.db"
    order_store = PaperExecutionStore(str(paper_path), create=True)
    try:
        for alpha in ALPHA_IDS:
            order_store.append_preview(_row(alpha))
    finally:
        order_store.close()
    operator = _operator()
    operator["stages"]["three_alpha_paper_flow"]["flow"]["stages"]["authoritative_dry_runs"] = []
    report = run_cycle(
        operator=operator,
        mark_provider=lambda _instruments: {
            "provider": "BINANCE_PUBLIC_MARK_PRICE",
            "observed_at": 2000,
            "marks": {"BTC": 110.0},
            "source_times": {"BTC": 2000},
        },
        paper_store_path=str(paper_path),
        realtime_store_path=str(tmp_path / "realtime.db"),
        output_path=str(tmp_path / "status.json"),
        aum=1000.0,
        generated_at_ms=3000,
    )
    assert report["verdict"]["paper_mark_ready"] is True
    assert report["verdict"]["cycle_completed"] is False
    assert report["verdict"]["backtest_reference_present_for_all_three"] is False


def test_main_passes_custom_paper_profile_to_order_preview(monkeypatch, tmp_path):
    captured = {}

    def fake_preview(**kwargs):
        captured["preview"] = kwargs
        return {"verdict": {"paper_preview_ready": True}}

    def fake_operator(**kwargs):
        captured["operator"] = kwargs
        preview = kwargs["preview_runner"]()
        return {
            "verdict": {
                "operator_completed": True,
                "paper_preview_ready": preview["verdict"]["paper_preview_ready"],
            }
        }

    def fake_cycle(**kwargs):
        kwargs["operator_runner"]()
        return {"verdict": {"cycle_completed": True}}

    monkeypatch.setattr(paper_realtime, "run_preview", fake_preview)
    monkeypatch.setattr(paper_realtime, "run_operator", fake_operator)
    monkeypatch.setattr(paper_realtime, "run_cycle", fake_cycle)

    paper_store = tmp_path / "small-paper.db"
    receipt = tmp_path / "small-operator.json"
    assert (
        paper_realtime.main(
            [
                "--cycles",
                "1",
                "--aum",
                "10000",
                "--paper-store",
                str(paper_store),
                "--operator-receipt",
                str(receipt),
                "--output",
                str(tmp_path / "status.json"),
                "--store",
                str(tmp_path / "realtime.db"),
            ]
        )
        == 0
    )
    assert captured["preview"]["aum"] == 10_000.0
    assert captured["preview"]["paper_store_path"] == str(paper_store)
    assert captured["preview"]["output_path"].endswith(
        "small-operator_paper_order_preview_10000.json"
    )


def test_main_health_check_runs_after_cycle_and_controls_exit_code(monkeypatch, tmp_path):
    captured = {}

    def fake_operator(**_kwargs):
        return _operator()

    def fake_cycle(**_kwargs):
        return {"verdict": {"cycle_completed": True}}

    def fake_health(**kwargs):
        captured["health"] = kwargs
        return {"verdict": {"paper_ready": True}, "blockers": []}

    monkeypatch.setattr(paper_realtime, "run_operator", fake_operator)
    monkeypatch.setattr(paper_realtime, "run_cycle", fake_cycle)
    monkeypatch.setattr(paper_realtime, "build_report", fake_health)

    status_path = tmp_path / "status.json"
    health_path = tmp_path / "health.json"
    assert (
        paper_realtime.main(
            [
                "--cycles",
                "1",
                "--health-check",
                "--output",
                str(status_path),
                "--health-output",
                str(health_path),
            ]
        )
        == 0
    )
    assert captured["health"] == {
        "realtime_status_path": str(status_path),
        "order_preview_path": "data/cache/paper_order_preview.json",
        "output_path": str(health_path),
    }

    small_receipt = tmp_path / "small-operator.json"
    small_store = tmp_path / "small-paper.db"
    assert (
        paper_realtime.main(
            [
                "--cycles",
                "1",
                "--health-check",
                "--aum",
                "10000",
                "--paper-store",
                str(small_store),
                "--operator-receipt",
                str(small_receipt),
                "--output",
                str(status_path),
                "--health-output",
                str(health_path),
            ]
        )
        == 0
    )
    assert captured["health"]["order_preview_path"].endswith(
        "small-operator_paper_order_preview_10000.json"
    )

    monkeypatch.setattr(
        paper_realtime,
        "build_report",
        lambda **_kwargs: {"verdict": {"paper_ready": False}, "blockers": ["STALE"]},
    )
    assert (
        paper_realtime.main(
            ["--cycles", "1", "--health-check", "--output", str(status_path)]
        )
        == 2
    )


def test_main_keeps_incomplete_cycle_as_operational_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        paper_realtime,
        "run_operator",
        lambda **_kwargs: _operator(),
    )
    monkeypatch.setattr(
        paper_realtime,
        "run_cycle",
        lambda **_kwargs: {"verdict": {"cycle_completed": False}},
    )

    assert (
        paper_realtime.main(
            ["--cycles", "1", "--output", str(tmp_path / "status.json")]
        )
        == 1
    )


def _candidate_stage_row(alpha_id: str, *, status: str = "PASS") -> dict:
    row = {
        "alpha_id": alpha_id,
        "module": "tools.dry_run_candidate_multi",
        "status": status,
        "paper_order_submission": False,
    }
    if status == "PASS":
        row["snapshot"] = {
            "run_id": f"bt-{alpha_id}",
            "n_periods": 81,
            "pnl": {
                "sharpe": 0.6,
                "sharpe_vt": 0.5,
                "max_drawdown": 0.3,
                "max_drawdown_vt": 0.2,
                "total_return": 0.4,
            },
            "reconciliation_residual": 0.0,
            "research_risk_decision": "RISK_APPROVED",
        }
    return row


def test_backtest_summary_maps_candidate_stage_to_authoritative_provenance():
    flow = {
        "stages": {
            "authoritative_dry_runs": [
                {
                    "alpha_id": "crowd_contra",
                    "status": "PASS",
                    "snapshot": {"run_id": "bt-crowd", "n_periods": 12, "pnl": {}},
                }
            ],
            "candidate_authoritative_dry_runs": [
                _candidate_stage_row("crowd_contra_3d"),
                _candidate_stage_row("crowd_contra_14d", status="ERROR"),
            ],
        }
    }
    backtests = paper_realtime._backtest_summary(flow)
    assert set(backtests) == {"crowd_contra", "crowd_contra_3d"}
    challenger = backtests["crowd_contra_3d"]
    assert challenger["provenance"] == "REAL_AUTHORITATIVE_L2_DRY_RUN"
    assert challenger["run_id"] == "bt-crowd_contra_3d"
    assert challenger["n_periods"] == 81
    assert challenger["sharpe"] == 0.6
    assert challenger["sharpe_vt"] == 0.5
    assert challenger["research_risk_decision"] == "RISK_APPROVED"


def test_backtest_summary_without_candidate_stage_keeps_legacy_only():
    flow = {
        "stages": {
            "authoritative_dry_runs": [
                {
                    "alpha_id": "crowd_contra",
                    "status": "PASS",
                    "snapshot": {"run_id": "bt-crowd", "n_periods": 12, "pnl": {}},
                }
            ]
        }
    }
    backtests = paper_realtime._backtest_summary(flow)
    assert set(backtests) == {"crowd_contra"}
    assert backtests["crowd_contra"]["provenance"] == "REAL_AUTHORITATIVE_L2_DRY_RUN"


def test_run_cycle_maps_candidate_authoritative_backtest(tmp_path):
    paper_path = tmp_path / "paper-orders.db"
    order_store = PaperExecutionStore(str(paper_path), create=True)
    try:
        for alpha in ALPHA_IDS:
            order_store.append_preview(_row(alpha))
    finally:
        order_store.close()
    operator = _operator()
    operator["stages"]["three_alpha_paper_flow"]["flow"]["stages"][
        "candidate_authoritative_dry_runs"
    ] = [_candidate_stage_row("crowd_contra_3d")]
    report = run_cycle(
        operator=operator,
        mark_provider=lambda _instruments: {
            "provider": "BINANCE_PUBLIC_MARK_PRICE",
            "observed_at": 2000,
            "marks": {"BTC": 110.0},
            "source_times": {"BTC": 2000},
        },
        paper_store_path=str(paper_path),
        realtime_store_path=str(tmp_path / "realtime.db"),
        output_path=str(tmp_path / "status.json"),
        aum=1000.0,
        generated_at_ms=3000,
    )
    assert report["verdict"]["cycle_completed"] is True
    challenger = report["backtests"]["crowd_contra_3d"]
    assert challenger["provenance"] == "REAL_AUTHORITATIVE_L2_DRY_RUN"
    assert challenger["run_id"] == "bt-crowd_contra_3d"
    for alpha in ALPHA_IDS:
        assert report["backtests"][alpha]["provenance"] == "REAL_AUTHORITATIVE_L2_DRY_RUN"
