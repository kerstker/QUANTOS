from __future__ import annotations

import json

import pytest

import tools.paper_order_preview as paper_preview
from tools.paper_order_preview import build_alpha_preview, run_preview


DECISION_TS = 1_700_000_000_000
STATUS = {
    "quality_evidence_current": True,
    "data_quality_admission": {"verdict": "PASS"},
    "alerts": [],
    "ran_at": DECISION_TS + 1_000,
    "staleness_days": {"prices": {"worst_days": 1}},
}


def _pending(weights):
    return {
        "decided_ts": DECISION_TS,
        "recorded_at": DECISION_TS,
        "weights_json": json.dumps(weights),
        "source": "FORWARD_L2_ATTESTED_DECISION",
    }


def test_build_alpha_preview_uses_gate_adapter_and_reconciles():
    result = build_alpha_preview(
        "crowd_contra",
        _pending({"BTC": 0.125, "ETH": -0.125}),
        mark_by_ticker={
            "BTC": {DECISION_TS: 50_000.0},
            "ETH": {DECISION_TS: 2_000.0},
        },
        status=STATUS,
        aum=1_000_000.0,
        generated_at_ms=DECISION_TS + 2_000,
    )

    assert result["paper_execution"] == "PAPER_FILLED"
    assert result["n_intents"] == result["n_approved"] == result["n_paper_fills"]
    assert result["n_rejected"] == 0
    assert result["rejected_reasons"] == []
    assert result["reconciliation_residual"] == 0.0
    assert result["paper_only"] is True
    assert result["live_allowed"] is False
    assert {row["instrument"] for row in result["orders"]} == {"BTC", "ETH"}


def test_build_alpha_preview_rejects_non_major_or_missing_price():
    with pytest.raises(RuntimeError, match="major-universe"):
        build_alpha_preview(
            "crowd_contra",
            _pending({"SHIB": 0.125}),
            mark_by_ticker={"SHIB": {DECISION_TS: 0.001}},
            status=STATUS,
        )


def test_build_alpha_preview_rebalances_from_persisted_paper_positions():
    result = build_alpha_preview(
        "crowd_contra",
        _pending({"ETH": 0.125}),
        mark_by_ticker={
            "BTC": {DECISION_TS: 50_000.0},
            "ETH": {DECISION_TS: 2_000.0},
        },
        status=STATUS,
        current_positions={"BTC": 1},
        aum=1_000_000.0,
        generated_at_ms=DECISION_TS + 2_000,
    )

    orders = {row["instrument"]: row for row in result["orders"]}
    assert orders["BTC"]["side"] == "SELL"
    assert orders["BTC"]["paper_position_before"] == 1
    assert orders["BTC"]["paper_position_after_fill"] == 0
    assert orders["ETH"]["side"] == "BUY"
    assert result["paper_positions"] == {"ETH": 62}
    assert result["n_intents"] == result["n_approved"] == 2
    with pytest.raises(RuntimeError, match="exact decision-time price"):
        build_alpha_preview(
            "crowd_contra",
            _pending({"BTC": 0.125}),
            mark_by_ticker={"BTC": {DECISION_TS + 1: 50_000.0}},
            status=STATUS,
        )


def test_run_preview_publishes_fail_closed_report_for_missing_attested_ledger(tmp_path):
    output = tmp_path / "preview.json"
    report = run_preview(
        alpha_ids=("crowd_contra",),
        forward_db=str(tmp_path / "missing.db"),
        mark_cache=str(tmp_path / "missing-mark.json"),
        status_path=str(tmp_path / "missing-status.json"),
        output_path=str(output),
        paper_store_path=str(tmp_path / "paper.db"),
        generated_at_ms=DECISION_TS,
    )

    assert report["verdict"]["paper_preview_ready"] is False
    assert report["verdict"]["live_allowed"] is False
    assert report["errors"]["crowd_contra"]["paper_execution"] == "PAPER_BLOCKED"
    assert json.loads(output.read_text(encoding="utf-8"))["safety"]["paper_only"] is True


def test_run_preview_blocks_reusing_decision_with_different_sizing_profile(
    monkeypatch, tmp_path
):
    pending = _pending({"BTC": 0.125})
    monkeypatch.setattr(paper_preview, "_load_json", lambda _path: STATUS)
    monkeypatch.setattr(
        paper_preview,
        "load_mark_prices",
        lambda _path: {"BTC": {DECISION_TS: 50_000.0}},
    )
    monkeypatch.setattr(
        paper_preview,
        "_latest_attested_pending",
        lambda _path, _alpha_id: (pending, 1),
    )
    store_path = tmp_path / "paper.db"
    first = run_preview(
        alpha_ids=("crowd_contra",),
        output_path=str(tmp_path / "first.json"),
        paper_store_path=str(store_path),
        aum=1_000_000.0,
        cost_bps=10.0,
        generated_at_ms=DECISION_TS,
    )
    assert first["verdict"]["paper_preview_ready"] is True

    second = run_preview(
        alpha_ids=("crowd_contra",),
        output_path=str(tmp_path / "second.json"),
        paper_store_path=str(store_path),
        aum=10_000.0,
        cost_bps=10.0,
        generated_at_ms=DECISION_TS + 1,
    )
    row = second["alphas"]["crowd_contra"]
    assert second["verdict"]["paper_preview_ready"] is False
    assert row["paper_execution"] == "PAPER_BLOCKED"
    assert row["rejected_reasons"] == [
        "PAPER_PREVIEW_SIZING_MISMATCH_USE_SEPARATE_PAPER_STORE"
    ]
    assert second["errors"]["crowd_contra"]["existing_aum"] == 1_000_000.0
