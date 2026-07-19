"""Phase 5 item 3(dim10):自动日终 GoNoGo 报告 + PnL/成本归因。gonogo_report.py 此前零测试——
先钉死既有装配骨架(assemble_report 纯核 + render_markdown),再加 PnL/成本归因块(从真实持久化
权益曲线 + TCA 算,不造 fill-matching)。全只读/报告,无自动动作、无风险数字。"""
import json

import pytest

import tools.gonogo_report as gonogo

from tools.gonogo_report import (_venue_risk_snapshot, assemble_pnl_attribution,
                                 assemble_report, render_markdown)

_INCIDENTS = {"n_alert_batches": 0, "risk_breaches": 0, "reconciliation_hard_stops": 0,
              "emergency_flattens": 0, "sentinel_failures": 0}


def test_assemble_report_shape_and_byte_identical_keys():
    rep = assemble_report(forward_evidence={"present": False},
                          execution_evidence={"binance": {"venue": "binance"}},
                          incidents=_INCIDENTS, now_ms=1000)
    assert rep["schema"] == "gonogo_report.v1"
    assert rep["generated_at"] == 1000
    assert rep["readiness"] == "L2_DRY_RUN_ONLY"
    assert rep["forward_evidence"] == {"present": False}
    assert rep["execution_evidence"] == {"binance": {"venue": "binance"}}
    assert rep["incidents"] == _INCIDENTS
    assert "honesty_note" in rep


def test_assemble_pnl_from_equity_curve():
    points = [{"ts": 1, "equity": 5000.0, "upnl_total": 10.0, "gross_notional": 200.0, "net_notional": 50.0},
              {"ts": 2, "equity": 5120.0, "upnl_total": 30.0, "gross_notional": 300.0, "net_notional": 80.0}]
    tca = {"notional_weighted_slippage_bps": 12.0, "total_notional": 1000.0}
    p = assemble_pnl_attribution(points, tca)
    assert p["present"] is True
    assert p["first_equity"] == pytest.approx(5000.0)
    assert p["last_equity"] == pytest.approx(5120.0)
    assert p["total_equity_change"] == pytest.approx(120.0)
    assert p["latest_unrealized_pnl"] == pytest.approx(30.0)
    assert p["latest_gross_notional"] == pytest.approx(300.0)
    assert p["latest_net_notional"] == pytest.approx(80.0)
    assert p["execution_cost_estimate"] == pytest.approx(12.0 / 1e4 * 1000.0)   # 1.2


def test_assemble_pnl_empty_is_absent():
    assert assemble_pnl_attribution([], None)["present"] is False
    assert assemble_pnl_attribution(None, None)["present"] is False


def test_assemble_pnl_missing_tca_and_upnl_honest_none():
    points = [{"ts": 1, "equity": 5000.0}, {"ts": 2, "equity": 5100.0}]
    p = assemble_pnl_attribution(points, None)
    assert p["total_equity_change"] == pytest.approx(100.0)
    assert p["execution_cost_estimate"] is None       # 无 TCA -> 诚实 None(不硬凑 0)
    assert p["latest_unrealized_pnl"] is None          # 无 upnl_total 键 -> None


def test_assemble_pnl_ignores_non_numeric_equity():
    points = [{"ts": 1, "equity": None}, {"ts": 2, "equity": 5100.0}]
    p = assemble_pnl_attribution(points, None)
    # 只有一条有效点 -> first==last -> change 0(不拿 None 当数字算)
    assert p["present"] is True
    assert p["first_equity"] == pytest.approx(5100.0)
    assert p["total_equity_change"] == pytest.approx(0.0)


def test_render_markdown_is_string_and_surfaces_pnl():
    rep = assemble_report(
        forward_evidence={"present": False},
        execution_evidence={"binance": {"venue": "binance",
            "pnl": {"present": True, "n_points": 2, "first_equity": 5000.0, "last_equity": 5120.0,
                    "total_equity_change": 120.0, "latest_unrealized_pnl": 30.0,
                    "latest_gross_notional": 300.0, "latest_net_notional": 80.0,
                    "execution_cost_estimate": 1.2}}},
        incidents=_INCIDENTS, now_ms=1000)
    md = render_markdown(rep)
    assert isinstance(md, str)
    assert "PnL" in md and "120" in md                 # 归因块被渲染出来


def test_render_markdown_no_pnl_block_does_not_crash():
    rep = assemble_report(forward_evidence={"present": True, "alphas": {}},
                          execution_evidence={"binance": {"venue": "binance"}},
                          incidents=_INCIDENTS, now_ms=1000)
    assert isinstance(render_markdown(rep), str)        # 无 pnl 键也不崩(向后兼容)


def test_forward_evidence_uses_attested_baseline_not_legacy_cache(tmp_path, monkeypatch):
    baseline = tmp_path / "forward_evidence_baseline.json"
    baseline.write_text(json.dumps({
        "observed_at_ms": 1000,
        "max_age_ms": 172_800_000,
        "counts": {
            "attested_realizations": 0,
            "attested_pending": 0,
            "legacy_unattested_decisions": 2,
        },
        "evidence_gate": {"minimum_weeks": 8, "eligible": False},
        "data_admission": {"status": "BLOCKED"},
    }), encoding="utf-8")
    monkeypatch.setattr(gonogo, "_FORWARD_EVIDENCE_BASELINE", str(baseline), raising=False)

    evidence = gonogo._forward_evidence(now_ms=1000)
    markdown = render_markdown(assemble_report(
        forward_evidence=evidence, execution_evidence={}, incidents=_INCIDENTS, now_ms=1000,
    ))

    assert evidence["evidence_state"] == "DATA_BLOCKED"
    assert evidence["attested_realizations"] == 0
    assert evidence["legacy_unattested_decisions"] == 2
    assert evidence["evidence_gate_eligible"] is False
    assert "认证前向 0/8 周" in markdown
    assert "历史未认证 2" in markdown
    assert "gate passed" not in markdown.lower()


def test_forward_evidence_invalid_bytes_fail_closed(tmp_path, monkeypatch):
    baseline = tmp_path / "forward_evidence_baseline.json"
    baseline.write_bytes(b"\xff")
    monkeypatch.setattr(gonogo, "_FORWARD_EVIDENCE_BASELINE", str(baseline), raising=False)

    assert gonogo._forward_evidence(now_ms=1000) == {
        "present": False,
        "evidence_state": "UNAVAILABLE",
        "actions_allowed": False,
    }


# ---------------------------------------------------------------------------
# 审计 §4.4 [P1-R2]:VaR/CVaR/HHI 只读展示——gonogo 读取哨兵已算好的 risk_snapshot,不重算
# (同一份计算,避免两处口径不一致)。
# ---------------------------------------------------------------------------

def test_venue_risk_snapshot_extracts_matching_venue():
    status = {"results": [
        {"venue": "binance", "risk_snapshot": {"var_95": 0.02, "cvar_95": 0.03, "hhi": 0.3}},
        {"venue": "bybit_demo", "error": "boom"},
    ]}
    assert _venue_risk_snapshot(status, "binance") == {"var_95": 0.02, "cvar_95": 0.03, "hhi": 0.3}


def test_venue_risk_snapshot_missing_venue_is_none():
    status = {"results": [{"venue": "binance", "risk_snapshot": {}}]}
    assert _venue_risk_snapshot(status, "bybit_demo") is None


def test_venue_risk_snapshot_venue_present_but_errored_is_none():
    # 该 venue 本轮 check_venue 直接抛异常(main() 只记 error,没有 risk_snapshot 键)-> 诚实 None
    status = {"results": [{"venue": "bybit_demo", "error": "boom"}]}
    assert _venue_risk_snapshot(status, "bybit_demo") is None


def test_venue_risk_snapshot_absent_status_is_none():
    assert _venue_risk_snapshot(None, "binance") is None
    assert _venue_risk_snapshot({}, "binance") is None


def test_render_markdown_surfaces_risk_snapshot():
    rep = assemble_report(
        forward_evidence={"present": False},
        execution_evidence={"binance": {"venue": "binance",
            "risk_snapshot": {"var_95": 0.0212, "cvar_95": 0.0355, "hhi": 0.3, "volatility": 0.1,
                              "effective_n": 3.33, "n_daily_returns": 12, "n_positions": 4,
                              "provenance": "REAL_RESEARCH_RISK_ENGINE_READ_ONLY_DISPLAY"}}},
        incidents=_INCIDENTS, now_ms=1000)
    md = render_markdown(rep)
    assert "VaR" in md and "0.0212" in md
    assert "HHI" in md and "0.3" in md


def test_render_markdown_no_risk_snapshot_does_not_crash():
    rep = assemble_report(forward_evidence={"present": False},
                          execution_evidence={"binance": {"venue": "binance"}},
                          incidents=_INCIDENTS, now_ms=1000)
    assert isinstance(render_markdown(rep), str)         # 无 risk_snapshot 键也不崩
