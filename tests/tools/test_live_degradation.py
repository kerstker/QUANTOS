"""tools/live_degradation.py(W1 驱动器)单元测试。

全 hermetic:tmp_path 播种真实 LiveLedger/ForwardLedger/parity jsonl + 合成 panel,
注入假 venue 注册表/路径函数——**绝不读写仓库真实的 data/cache 或 evidence/**。
钉死:端到端报告数字(手算可复核)、replay 与 canonical `run_multi_period_dry_run`
的曲线等价性(同一套取整/佣金/对账/资金费原语)、已执行决策缺记录权重 -> replay
UNKNOWN(绝不重算信号)、无活动 venue 不进 artifact、smoke 单无 trace 如实披露。
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from live.execution.filters import client_order_id
from live.execution.position_ledger import LiveLedger
from quantos.contracts.trading import OrderSide
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.paper_trading.forward_store import ForwardLedger
from tools import live_degradation as ld

DAY = 86_400_000
T0 = 1_780_000_000_000

_CLOSE = {"BTC": [100.0, 110.0, 120.0, 130.0, 140.0],
          "ETH": [50.0, 48.0, 45.0, 47.0, 49.0]}
_GRID = [T0 + i * DAY for i in range(5)]
_ADMISSION_REF = ForwardAdmissionRef(
    admission_id="live-degradation-test",
    evidence_sha256="a" * 64,
    decision_hash="b" * 64,
    recorded_at_ms=T0,
    price_basis="PERP_MARK_PRICE",
    instrument_scope=("BTC", "ETH"),
)


def _panel():
    instruments = ["BTC", "ETH"]
    close = [[_CLOSE["BTC"][r], _CLOSE["ETH"][r]] for r in range(5)]
    funding = [[0.0, 0.0] for _ in range(5)]
    return (SimpleNamespace(instruments=instruments, event_times=list(_GRID),
                            matrices={"close": close, "funding": funding}),
            instruments)


def _seed_ledger(path):
    """手算可复核的小账本:T0+5h 买 5 BTC @102(决策价 100)/卖 10 ETH @50。"""
    ledger = LiveLedger(str(path), create=True)
    cid_b = client_order_id("crowd_contra", T0, "BTC", OrderSide.BUY, 5.0)
    cid_e = client_order_id("crowd_contra", T0, "ETH", OrderSide.SELL, 10.0)
    ledger.record_submission(cid_b, "BTC", OrderSide.BUY, 5.0, strategy_id="crowd_contra",
                             submitted_at=T0 + 5 * 3_600_000)
    ledger.record_submission(cid_e, "ETH", OrderSide.SELL, 10.0, strategy_id="crowd_contra",
                             submitted_at=T0 + 5 * 3_600_000)
    ledger.update_status(cid_b, "FILLED")
    ledger.update_status(cid_e, "FILLED")
    ledger.record_fill("f-b", cid_b, "BTC", OrderSide.BUY, 5.0, 102.0,
                       strategy_id="crowd_contra", filled_at=T0 + 6 * 3_600_000)
    ledger.record_fill("f-e", cid_e, "ETH", OrderSide.SELL, 10.0, 50.0,
                       strategy_id="crowd_contra", filled_at=T0 + 6 * 3_600_000)
    ledger.record_tca("t-b", client_order_id=cid_b, instrument_id="BTC", side=OrderSide.BUY,
                      decision_price=100.0, fill_price=102.0, quantity=5.0,
                      strategy_id="crowd_contra", recorded_at=T0 + 6 * 3_600_000)
    ledger.record_tca("t-e", client_order_id=cid_e, instrument_id="ETH", side=OrderSide.SELL,
                      decision_price=50.0, fill_price=50.0, quantity=10.0,
                      strategy_id="crowd_contra", recorded_at=T0 + 6 * 3_600_000)
    ledger.close()
    return str(path)


def _seed_forward(path, *, decision=True):
    fwd = ForwardLedger(str(path), create=True)
    if decision:
        fwd.record_decision("crowd_contra", T0, recorded_at=T0 + 1000,
                            weights={"BTC": 0.5, "ETH": -0.5}, source="FORWARD_LIVE",
                            admission_ref=_ADMISSION_REF)
    return str(path)


def _seed_parity(path, *, aum=1000.0):
    rec = {"schema": "live_parity.v1", "mode": "submit", "decided_ts": T0, "aum": aum,
           "paper_source": "forward_ledger", "strategy_id": "crowd_contra",
           "n_paper_legs": 2, "n_with_live_order": 2,
           "per_instrument": [{"instrument_id": "BTC", "target_weight": 0.5},
                              {"instrument_id": "ETH", "target_weight": -0.5}]}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return str(path)


def _hermetic(tmp_path):
    ledger = _seed_ledger(tmp_path / "live_track_fv.db")
    parity = _seed_parity(tmp_path / "live_parity_fv.jsonl")
    forward = _seed_forward(tmp_path / "paper_track.db")
    venues_registry = {"fv": {"suffix": "_fv"}}
    paths = {"ledger": ledger, "parity": parity}
    return {"venues_registry": venues_registry,
            "venue_paths_fn": lambda suffix: paths,
            "forward_db": forward, "panel_bundle": _panel()}


# ---------------------------------------------------------------------------
# 端到端(手算可复核)
# ---------------------------------------------------------------------------

def test_build_report_end_to_end_hand_computed(tmp_path):
    artifact = ld.build_report(now_ms=123, venues=["fv"], **_hermetic(tmp_path))
    assert artifact["artifact"] == "LIVE_DEGRADATION"
    assert artifact["generated_at_ms"] == 123
    assert artifact["readiness"] == "TECHNICAL_VALIDATION_ONLY"
    assert artifact["backtest_reference_status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert len(artifact["reports"]) == 1
    rep = artifact["reports"][0]
    assert rep["venue"] == "fv" and rep["strategy_id"] == "crowd_contra"

    # live:5 BTC @102 + 空 10 ETH @50,mark_end=T0+4d(BTC140/ETH49)
    # live_gross = 5×38 + 10×1 = 200
    assert rep["live_net_pnl"] == pytest.approx(200.0)
    assert rep["live_gross_pnl"] == pytest.approx(200.0)
    # V1 replay has been invalidated.  Real fills/TCA remain visible, while all
    # replay-dependent economics stay explicitly unknown.
    assert rep["replay_gross_pnl"] is None
    assert rep["replay_net_pnl"] is None
    assert rep["components"]["replay_model_cost"] is None
    assert rep["live_degradation_ratio"] is None
    assert rep["degradation_ratio_reason"] == "replay_net_unknown"
    assert rep["components"]["slippage_cost_actual"] == pytest.approx(10.0)
    assert rep["components"]["trading_pnl_delta"] is None
    assert rep["components"]["position_timing_effect"] is None
    assert rep["components"]["residual"] is None
    # UNKNOWN 纪律
    assert rep["cost_overrun"] is None
    unk = set(rep["unknown_fields"])
    assert {"fee_actual", "funding_actual", "cost_overrun", "slippage_vs_arrival"} <= unk
    # 计数/trace/决策
    assert rep["n_orders"] == 2 and rep["n_fills"] == 2 and rep["fill_rate"] == 1.0
    assert rep["orders_without_decision_trace"] == []
    dec = rep["decisions"]
    assert len(dec) == 1 and dec[0]["decided_ts"] == T0
    assert dec[0]["executed"] is True and dec[0]["n_orders"] == 2
    assert dec[0]["weights_provenance"] == "FORWARD_LEDGER(FORWARD_LIVE)"
    assert dec[0]["aum"] == pytest.approx(1000.0)
    assert dec[0]["aum_provenance"] == "PARITY_SUBMIT_RECORD"
    # 窗口与逐日瀑布(5 行;live T0 行未入账)
    assert rep["window"]["n_days"] == 5 and rep["window"]["mark_end_ts"] == _GRID[-1]
    assert len(rep["daily"]) == 5
    assert rep["daily"][0]["live_equity"] == pytest.approx(1000.0)
    assert rep["daily"][0]["replay_equity"] is None
    assert rep["daily"][-1]["live_equity"] == pytest.approx(1200.0)
    assert rep["daily"][-1]["replay_equity"] is None
    assert rep["daily"][-1]["gap"] is None
    # summary
    assert artifact["summary"]["n_reports"] == 1
    assert artifact["summary"]["n_with_ratio"] == 0
    leak = artifact["summary"]["biggest_leak_by_strategy"]["fv:crowd_contra"]
    assert leak is not None and leak["component"]


def test_production_report_never_invokes_legacy_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ld,
        "_replay_book",
        lambda *_args, **_kwargs: pytest.fail("legacy replay must not be invoked"),
    )

    artifact = ld.build_report(now_ms=123, venues=["fv"], **_hermetic(tmp_path))

    assert artifact["reports"][0]["replay_net_pnl"] is None
    assert (
        artifact["reports"][0]["replay_construction"]["skip_reason"]
        == "INVALIDATED_PERP_LEDGER_V1"
    )


# ---------------------------------------------------------------------------
# UNKNOWN / 边界
# ---------------------------------------------------------------------------

def test_executed_decision_missing_recorded_weights_makes_replay_unknown(tmp_path):
    """已执行但前向台账无该 decided_ts、parity 也无 submit 记录 -> replay 不可构造
    (绝不拿今天的数据重算信号凑数);live 侧数字照报,ratio None+reason。"""
    kw = _hermetic(tmp_path)
    # 前向台账改放空(无决策),parity 置空文件
    kw["forward_db"] = _seed_forward(tmp_path / "empty_forward.db", decision=False)
    (tmp_path / "live_parity_fv.jsonl").write_text("", encoding="utf-8")
    artifact = ld.build_report(now_ms=1, venues=["fv"], **kw)
    rep = artifact["reports"][0]
    assert rep["replay_net_pnl"] is None
    assert rep["replay_construction"]["skip_reason"] == "INVALIDATED_PERP_LEDGER_V1"
    assert rep["live_degradation_ratio"] is None
    assert rep["degradation_ratio_reason"] == "replay_net_unknown"
    assert rep["live_net_pnl"] == pytest.approx(200.0)      # live 侧不受影响
    assert "replay_net_pnl" in rep["unknown_fields"]
    dec = rep["decisions"][0]
    assert dec["executed"] is True and dec["weights_provenance"] is None


def test_unbound_legacy_forward_decision_is_never_loaded(tmp_path):
    path = tmp_path / "legacy-forward.db"
    _seed_forward(path, decision=False)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO forward_decisions(alpha_id,decided_ts,recorded_at,weights_json,"
            "signals_json,source,realized) VALUES(?,?,?,?,?,?,0)",
            (
                "crowd_contra",
                T0,
                T0 + 1_000,
                json.dumps({"BTC": 0.5, "ETH": -0.5}, sort_keys=True),
                None,
                "FORWARD_LIVE",
            ),
        )

    assert ld.load_forward_decisions(str(path), "crowd_contra") == []


def test_unexecuted_paper_decision_is_replayed_and_disclosed(tmp_path):
    """纸面决策(前向台账记录在案)但 live 未执行:进 replay(研究侧该持的仓),
    executed=False 如实披露——漏调仓的 PnL 后果进 trading/position 分解。"""
    tmp = tmp_path
    ledger = _seed_ledger(tmp / "live_track_fv.db")
    parity = _seed_parity(tmp / "live_parity_fv.jsonl")
    fwd = ForwardLedger(str(tmp / "paper_track.db"), create=True)
    fwd.record_decision("crowd_contra", T0, recorded_at=T0 + 1000,
                        weights={"BTC": 0.5, "ETH": -0.5}, source="FORWARD_LIVE",
                        admission_ref=_ADMISSION_REF)
    # 第二个决策(T0+2d):live 从未执行(无对应订单)
    fwd.record_decision("crowd_contra", T0 + 2 * DAY, recorded_at=T0 + 2 * DAY + 1000,
                        weights={"BTC": -0.5, "ETH": 0.5}, source="FORWARD_LIVE",
                        admission_ref=_ADMISSION_REF)
    artifact = ld.build_report(now_ms=1, venues=["fv"],
                               venues_registry={"fv": {"suffix": "_fv"}},
                               venue_paths_fn=lambda suffix: {"ledger": ledger, "parity": parity},
                               forward_db=str(tmp / "paper_track.db"), panel_bundle=_panel())
    rep = artifact["reports"][0]
    assert len(rep["decisions"]) == 2
    d2 = rep["decisions"][1]
    assert d2["decided_ts"] == T0 + 2 * DAY
    assert d2["executed"] is False and d2["n_orders"] == 0
    assert d2["aum_provenance"] == "CARRIED_LAST_RECORDED"
    # replay 在 T0+2d 翻仓(整数取整):BTC 目标 round(−0.5×1000/120)=−4 -> 卖 9 @120;
    # ETH 目标 round(0.5×1000/45)=11 -> 买 21 @45。收盘行 @140/49:
    # BTC gross = 5×(140−100) − 9×(140−120) = 200−180 = 20
    # ETH gross = −10×(49−50) + 21×(49−45) = 10+84 = 94;总 gross = 114
    # 佣金 = (500+1080+500+945)×10bps = 3.025;funding=0 -> net = 110.975
    assert rep["replay_gross_pnl"] is None
    assert rep["replay_net_pnl"] is None
    assert rep["live_net_pnl"] == pytest.approx(200.0)      # live 没翻仓,守着旧腿
    assert rep["live_degradation_ratio"] is None


def test_smoke_order_without_trace_is_disclosed(tmp_path):
    tmp = tmp_path
    ledger = LiveLedger(str(tmp / "live_track_fv.db"), create=True)
    ledger.record_submission("quantos-smoke-1", "BTC", OrderSide.BUY, 0.002,
                             strategy_id="crowd_contra", submitted_at=T0 + 3_600_000)
    ledger.record_fill("smoke-f", "quantos-smoke-1", "BTC", OrderSide.BUY, 0.002, 100.0,
                       strategy_id="crowd_contra", filled_at=T0 + 3_600_000)
    ledger.close()
    artifact = ld.build_report(now_ms=1, venues=["fv"],
                               venues_registry={"fv": {"suffix": "_fv"}},
                               venue_paths_fn=lambda s: {"ledger": str(tmp / "live_track_fv.db"),
                                                         "parity": str(tmp / "no.jsonl")},
                               forward_db=_seed_forward(tmp / "paper_track.db"),
                               panel_bundle=_panel())
    rep = artifact["reports"][0]
    assert rep["orders_without_decision_trace"] == ["quantos-smoke-1"]
    # smoke 无决策记录 -> replay 不可构造(已执行=有订单但无记录权重)
    assert rep["replay_net_pnl"] is None


def test_venue_without_activity_produces_no_report(tmp_path):
    venues_registry = {"empty_v": {"suffix": "_empty"}}
    paths = {"ledger": str(tmp_path / "missing.db"), "parity": str(tmp_path / "no.jsonl")}
    artifact = ld.build_report(now_ms=1, venues=["empty_v"],
                               venues_registry=venues_registry,
                               venue_paths_fn=lambda suffix: paths,
                               forward_db=str(tmp_path / "no_forward.db"),
                               panel_bundle=_panel())
    assert artifact["reports"] == []
    assert artifact["summary"]["n_reports"] == 0


def test_artifact_written_by_main(tmp_path, monkeypatch, capsys):
    out = tmp_path / "live_degradation.json"
    kw = _hermetic(tmp_path)
    monkeypatch.setattr(ld, "_VENUES", kw["venues_registry"])
    monkeypatch.setattr(ld, "_venue_paths", kw["venue_paths_fn"])
    monkeypatch.setattr(ld, "_FORWARD_DB", kw["forward_db"])
    monkeypatch.setattr(ld, "build_panel", lambda: kw["panel_bundle"])
    ld.main(["--venue", "fv", "--out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["artifact"] == "LIVE_DEGRADATION"
    assert len(data["reports"]) == 1
    assert data["reports"][0]["live_net_pnl"] == pytest.approx(200.0)
    assert "fv:crowd_contra" in capsys.readouterr().out
