"""tools/go_live_readiness.py(2026-07-24,go-live 上线向导后端驱动器)的单元测试。

全部用 tmp_path fixture + 显式路径/注册表 monkeypatch——**绝不读写仓库真实的
key/ 或 evidence/ 文件**。覆盖:
- mandate 缺失/过期/域不匹配/限额缺口(guidance 的核心表面)
- 信号腿 -> required_instruments;min_deploy 数学(BTC 50/0.0714 ≈ 700.3,binding leg)
- suggested_caps 数学(deploy target $1000 + 0.95 余量,50 步进向上取整)
- key_permissions / smoke / kill_switch / last_run 的 fail-soft(缺文件不崩、诚实标记)
- build_report 装配形状(注入假 venue 注册表 + 假策略快照,全 hermetic)
"""
from __future__ import annotations

import json

import pytest

from tools import go_live_readiness as glr


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

_LEG_NAMES = ["BCH", "ETC", "APT", "AAVE", "BTC", "FIL", "OP",
             "BNB", "XRP", "DOGE", "SOL", "LTC", "ADA", "DOT"]


def _legs14(weight: float = 0.0714):
    return [{"instrument": name,
             "weight": weight if i < 7 else -weight,
             "price": 100.0 + i}
            for i, name in enumerate(_LEG_NAMES)]


def _write_mandate(path, *, venue="mainnet", exchange="binance",
                   allowed=("BTC", "ETH"), gross=1100.0, single=150.0, net=100.0,
                   expires_at=None):
    payload = {
        "mandate_id": "test-mandate",
        "venue": venue,
        "exchange": exchange,
        "allowed_instruments": list(allowed),
        "max_single_order_notional": single,
        "max_gross_notional": gross,
        "max_net_notional": net,
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# key_permissions(fail-soft + 三态)
# ---------------------------------------------------------------------------

def test_key_permissions_missing_file_is_unverified(tmp_path):
    assert glr.key_permissions_status("binance_mainnet", evidence_dir=str(tmp_path)) == {
        "status": "UNVERIFIED", "checked_at_ms": None}


def test_key_permissions_safe_artifact(tmp_path):
    (tmp_path / "key_permissions_binance_mainnet.json").write_text(json.dumps(
        {"venue": "binance", "safety": "SAFE", "reasons": ["ok"],
         "raw_fields": {"ipRestrict": True}, "checked_at_ms": 123}), encoding="utf-8")
    assert glr.key_permissions_status("binance_mainnet", evidence_dir=str(tmp_path)) == {
        "status": "SAFE", "checked_at_ms": 123}


def test_key_permissions_unsafe_and_unknown_are_not_safe(tmp_path):
    for safety in ("UNSAFE", "UNKNOWN"):
        (tmp_path / "key_permissions_mexc.json").write_text(json.dumps(
            {"venue": "mexc", "safety": safety, "reasons": [], "raw_fields": {},
             "checked_at_ms": 5}), encoding="utf-8")
        assert glr.key_permissions_status("mexc", evidence_dir=str(tmp_path))["status"] == "NOT_SAFE"


def test_key_permissions_corrupt_file_is_unverified(tmp_path):
    (tmp_path / "key_permissions_bybit.json").write_text("{not json", encoding="utf-8")
    assert glr.key_permissions_status("bybit", evidence_dir=str(tmp_path))["status"] == "UNVERIFIED"


# ---------------------------------------------------------------------------
# smoke(PASS/FAIL/ABSENT + 年龄)
# ---------------------------------------------------------------------------

def test_smoke_absent_when_no_artifact(tmp_path):
    assert glr.smoke_status("binance_mainnet", now_ms=1000, smoke_paths={}) == {
        "verdict": "ABSENT", "age_ms": None}
    paths = {"binance_mainnet": str(tmp_path / "nope.json")}
    assert glr.smoke_status("binance_mainnet", now_ms=1000, smoke_paths=paths)["verdict"] == "ABSENT"


def test_smoke_pass_with_age(tmp_path):
    p = tmp_path / "smoke.json"
    p.write_text(json.dumps({"verdict": "PASS", "ran_at_ms": 900}), encoding="utf-8")
    out = glr.smoke_status("binance_mainnet", now_ms=1000,
                           smoke_paths={"binance_mainnet": str(p)})
    assert out == {"verdict": "PASS", "age_ms": 100}


def test_smoke_fail_on_non_pass_verdict_and_corrupt(tmp_path):
    p = tmp_path / "smoke.json"
    p.write_text(json.dumps({"verdict": "FAIL", "ran_at_ms": 1}), encoding="utf-8")
    assert glr.smoke_status("binance_mainnet", now_ms=10,
                            smoke_paths={"binance_mainnet": str(p)})["verdict"] == "FAIL"
    p.write_text("{broken", encoding="utf-8")
    assert glr.smoke_status("binance_mainnet", now_ms=10,
                            smoke_paths={"binance_mainnet": str(p)})["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# mandate(缺失/过期/域/限额)
# ---------------------------------------------------------------------------

def test_mandate_missing_file(tmp_path):
    out = glr.mandate_status("binance_mainnet", now_ms=1000,
                             mandate_path=str(tmp_path / "missing.json"))
    assert out["present"] is False and out["loads"] is False
    assert out["expired"] is None and out["max_gross_notional"] is None
    # 域期望仍诚实给出(binance_mainnet = mainnet/binance)
    assert out["expected_venue"] == "mainnet" and out["expected_exchange"] == "binance"


def test_mandate_valid_loads_and_domains_ok(tmp_path):
    path = _write_mandate(tmp_path / "m.json", expires_at=2000)
    out = glr.mandate_status("binance_mainnet", now_ms=1000, mandate_path=path)
    assert out["loads"] is True and out["expired"] is False
    assert out["venue_domain_ok"] is True and out["exchange_domain_ok"] is True
    assert out["allowed_instruments"] == ["BTC", "ETH"]
    assert out["max_gross_notional"] == 1100.0
    assert out["max_single_order_notional"] == 150.0
    assert out["max_net_notional"] == 100.0
    assert out["expires_at"] == 2000


def test_mandate_expired(tmp_path):
    path = _write_mandate(tmp_path / "m.json", expires_at=500)
    out = glr.mandate_status("binance_mainnet", now_ms=1000, mandate_path=path)
    assert out["loads"] is True and out["expired"] is True


def test_mandate_domain_mismatch(tmp_path):
    path = _write_mandate(tmp_path / "m.json", venue="testnet", exchange="bybit")
    out = glr.mandate_status("binance_mainnet", now_ms=1000, mandate_path=path)
    assert out["venue_domain_ok"] is False and out["exchange_domain_ok"] is False


def test_mandate_malformed_json_loads_false(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{broken", encoding="utf-8")
    out = glr.mandate_status("binance_mainnet", now_ms=1000, mandate_path=str(p))
    assert out["present"] is True and out["loads"] is False


# ---------------------------------------------------------------------------
# 腿 -> required_instruments + min_deploy 数学
# ---------------------------------------------------------------------------

def test_required_instruments_sorted_from_legs():
    assert glr.required_instruments(_legs14()) == sorted(_LEG_NAMES)


def test_min_deploy_binding_leg_is_btc():
    # BTC min-notional 50 / |w|=0.0714 ≈ 700.28 —— 全场最紧的腿
    min_deploy, binding = glr.min_deploy_for_all_legs(_legs14())
    assert min_deploy == pytest.approx(50.0 / 0.0714, rel=1e-9)
    assert min_deploy == pytest.approx(700.28, abs=0.01)
    assert binding == "BTC"


def test_min_deploy_exact_equal_weight():
    legs = _legs14(weight=1.0 / 14)
    min_deploy, binding = glr.min_deploy_for_all_legs(legs)
    assert min_deploy == pytest.approx(700.0, rel=1e-9)
    assert binding == "BTC"


def test_min_deploy_custom_min_notional_table():
    legs = [{"instrument": "BTC", "weight": 0.5, "price": 1.0},
            {"instrument": "DOGE", "weight": 0.5, "price": 1.0}]
    min_deploy, binding = glr.min_deploy_for_all_legs(legs, min_notional={"DOGE": 200.0})
    assert binding == "DOGE" and min_deploy == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# suggested_caps 数学
# ---------------------------------------------------------------------------

def test_suggested_caps_deploy_target_dominates():
    # min_deploy 700.28 < target 1000:gross = ceil(1000/0.95/50)*50 = 1100
    caps = glr.suggested_caps(700.28)
    assert caps["max_gross_notional"] == 1100.0
    assert caps["max_single_order_notional"] == 150.0
    assert caps["max_net_notional"] == 100.0
    assert caps["per_instrument"] == 150.0


def test_suggested_caps_min_deploy_dominates():
    # min_deploy 3000 > target:gross = ceil(3000/0.95/50)*50 = 3200
    assert glr.suggested_caps(3000.0)["max_gross_notional"] == 3200.0


# ---------------------------------------------------------------------------
# guidance 警告(缺口表面化)
# ---------------------------------------------------------------------------

def test_guidance_surfaces_instrument_and_cap_gaps():
    legs = _legs14()
    mandate_info = {"loads": True, "expired": False,
                    "venue_domain_ok": True, "exchange_domain_ok": True,
                    "allowed_instruments": ["BTC", "ETH"],
                    "max_gross_notional": 100.0, "path": "x"}
    g = glr.build_guidance(legs, mandate_info)
    assert g["required_instruments"] == sorted(_LEG_NAMES)
    # 14 腿里只有 BTC 被授权(ETH 不在当前信号里)-> 缺 13、ETH 是 stale
    assert sorted(g["missing_from_mandate"]) == sorted(set(_LEG_NAMES) - {"BTC"})
    assert g["stale_in_mandate"] == ["ETH"]
    assert g["min_deploy_binding_leg"] == "BTC"
    assert g["suggested_caps"]["max_gross_notional"] == 1100.0
    text = "\n".join(g["warnings"])
    assert "13/14" in text                       # 授权缺口
    assert "min-notional" in text                 # 限额缺口(deploy≈95 -> 4 条腿低于 min)
    assert "4/14" in text                         # BTC/BCH/ETC/LTC 四条低于 min(ETH 不在腿里)


def test_guidance_stale_in_mandate_and_expired_warning():
    legs = _legs14()
    mandate_info = {"loads": True, "expired": True, "expires_at": 1,
                    "venue_domain_ok": False, "exchange_domain_ok": True,
                    "allowed_instruments": sorted(_LEG_NAMES) + ["PEPE"],
                    "max_gross_notional": 1e9, "path": "x"}
    g = glr.build_guidance(legs, mandate_info)
    assert g["stale_in_mandate"] == ["PEPE"]
    text = "\n".join(g["warnings"])
    assert "已过期" in text and "venue 域不匹配" in text and "PEPE" in text


def test_guidance_mandate_not_loadable_warning():
    g = glr.build_guidance(_legs14(), {"loads": False, "allowed_instruments": [], "path": "x"})
    assert g["missing_from_mandate"] == sorted(_LEG_NAMES)
    assert any("不可加载" in w or "缺失" in w for w in g["warnings"])


def test_guidance_min_deploy_above_target_warns():
    legs = [{"instrument": "BTC", "weight": 0.01, "price": 1.0}]   # 50/0.01 = 5000 > 1000
    g = glr.build_guidance(legs, {"loads": False, "allowed_instruments": [], "path": "x"})
    assert g["min_deploy_for_all_legs"] == pytest.approx(5000.0)
    assert any("超过部署目标" in w for w in g["warnings"])


# ---------------------------------------------------------------------------
# kill_switch / last_run / attestations fail-soft
# ---------------------------------------------------------------------------

def test_kill_switch_absent_is_not_tripped(tmp_path):
    assert glr.kill_switch_tripped(str(tmp_path / "no_ks.json")) is False


def test_kill_switch_corrupt_fails_closed_tripped(tmp_path):
    p = tmp_path / "ks.json"
    p.write_text("{broken", encoding="utf-8")
    assert glr.kill_switch_tripped(str(p)) is True


def test_last_run_absent_and_present(tmp_path):
    missing = glr.last_run_status(str(tmp_path / "no.json"))
    assert missing["present"] is False and missing["at_ms"] is None and missing["summary"] == {}
    p = tmp_path / "status.json"
    p.write_text(json.dumps({"ran_at": 42, "mode": "submit", "tier": "AUTO",
                             "strategy": "crowd_contra", "venue": "binance",
                             "tca": {"huge": "block"}}), encoding="utf-8")
    out = glr.last_run_status(str(p))
    assert out["present"] is True and out["at_ms"] == 42
    assert out["summary"] == {"mode": "submit", "tier": "AUTO",
                              "strategy": "crowd_contra", "venue": "binance"}


def test_attestations_fail_soft(tmp_path):
    out = glr.attestations_status(["crowd_contra", "carry_oi_majors"],
                                  path=str(tmp_path / "missing.json"))
    assert out == {"crowd_contra": {"present": False, "kind": None},
                   "carry_oi_majors": {"present": False, "kind": None}}
    p = tmp_path / "att.json"
    p.write_text(json.dumps({"crowd_contra": {"kind": "human_override"}}), encoding="utf-8")
    out = glr.attestations_status(["crowd_contra", "carry_oi_majors"], path=str(p))
    assert out["crowd_contra"] == {"present": True, "kind": "human_override"}
    assert out["carry_oi_majors"] == {"present": False, "kind": None}


# ---------------------------------------------------------------------------
# strategy_snapshot + build_report 装配(hermetic)
# ---------------------------------------------------------------------------

def test_strategy_snapshot_gross_and_sorted_legs():
    snap = glr.strategy_snapshot({"BBB": -0.4, "AAA": 0.6}, {"AAA": 10.0, "BBB": 20.0}, 777)
    assert snap["decided_ts"] == 777 and snap["n_legs"] == 2
    assert snap["gross"] == pytest.approx(1.0)
    assert [leg["instrument"] for leg in snap["legs"]] == ["AAA", "BBB"]
    assert snap["legs"][0] == {"instrument": "AAA", "weight": 0.6, "price": 10.0}


class _FakeAdapter:
    MAINNET_BASE = "https://mainnet.example"
    venue_name = "fake"


def _hermetic_report(tmp_path, monkeypatch, *, strategies):
    fake_venues = {
        "binance_mainnet": {"base_url": "https://mainnet.example", "suffix": "_fm",
                            "mandate_file": str(tmp_path / "mandate.json"),
                            "adapter": _FakeAdapter},
        "fake_testnet": {"base_url": "https://testnet.example", "suffix": "_ft",
                         "mandate_file": str(tmp_path / "missing_mandate.json"),
                         "adapter": _FakeAdapter},
    }
    monkeypatch.setattr(glr, "_VENUES", fake_venues)
    monkeypatch.setattr(glr, "_venue_paths", lambda suffix: {
        "kill_switch": str(tmp_path / f"ks{suffix}.json"),
        "status": str(tmp_path / f"status{suffix}.json"),
    })
    att = tmp_path / "att.json"
    att.write_text(json.dumps({"crowd_contra": {"kind": "human_override"}}), encoding="utf-8")
    (tmp_path / f"status_fm.json").write_text(json.dumps(
        {"ran_at": 9, "mode": "preview"}), encoding="utf-8")
    _write_mandate(tmp_path / "mandate.json", venue="mainnet", exchange="fake",
                   allowed=("BTC",), gross=100.0)
    return glr.build_report(now_ms=1000, strategies=strategies,
                            evidence_dir=str(tmp_path), attestations_path=str(att),
                            smoke_paths={})


def test_build_report_shape_and_guidance(tmp_path, monkeypatch):
    strategies = {"crowd_contra": glr.strategy_snapshot(
        {leg["instrument"]: leg["weight"] for leg in _legs14()},
        {leg["instrument"]: leg["price"] for leg in _legs14()}, 555)}
    report = _hermetic_report(tmp_path, monkeypatch, strategies=strategies)
    assert report["artifact"] == "GO_LIVE_READINESS"
    assert report["generated_at_ms"] == 1000
    assert report["readiness"] == "L2_DRY_RUN_ONLY"
    assert len(report["venues"]) == 2
    mainnet = next(v for v in report["venues"] if v["venue"] == "binance_mainnet")
    assert mainnet["mainnet"] is True and mainnet["suffix"] == "_fm"
    assert mainnet["key_permissions"]["status"] == "UNVERIFIED"
    assert mainnet["smoke"]["verdict"] == "ABSENT"
    assert mainnet["mandate"]["loads"] is True
    assert mainnet["mandate"]["exchange_domain_ok"] is True
    assert mainnet["attestations"]["crowd_contra"]["present"] is True
    assert mainnet["kill_switch_tripped"] is False
    assert mainnet["last_run"]["present"] is True and mainnet["last_run"]["at_ms"] == 9
    testnet = next(v for v in report["venues"] if v["venue"] == "fake_testnet")
    assert testnet["mainnet"] is False
    assert testnet["mandate"]["loads"] is False       # 缺文件 fail-soft,不崩
    guidance = report["guidance"]
    assert guidance["venue"] == "binance_mainnet" and guidance["strategy"] == "crowd_contra"
    assert len(guidance["required_instruments"]) == 14
    assert guidance["suggested_caps"]["max_gross_notional"] == 1100.0


def test_build_report_signal_unavailable_guidance_degrades_honestly(tmp_path, monkeypatch):
    strategies = {"crowd_contra": {"error": "RuntimeError", "decided_ts": None,
                                   "n_legs": 0, "gross": None, "legs": []}}
    report = _hermetic_report(tmp_path, monkeypatch, strategies=strategies)
    guidance = report["guidance"]
    assert guidance["required_instruments"] == []
    assert guidance["min_deploy_for_all_legs"] is None
    assert guidance["suggested_caps"] is None
    assert any("信号不可用" in w for w in guidance["warnings"])


def test_last_run_surfaces_would_submit_after_preview(tmp_path):
    # 2026-07-24:preview 演练的 status JSON 带 would_submit/armed → last_run summary 透传,
    # 上线向导页在一次 /preview 后能直接看到"本来会提交什么"。
    p = tmp_path / "status.json"
    legs = [{"instrument_id": "BTC", "side": "BUY", "quantity": 0.001,
             "order_type": "MARKET", "limit_price": None}]
    p.write_text(json.dumps({"ran_at": 42, "mode": "preview", "tier": "AUTO",
                             "strategy": "crowd_contra", "venue": "binance_mainnet",
                             "armed": True, "would_submit": legs}),
                 encoding="utf-8")
    out = glr.last_run_status(str(p))
    assert out["summary"]["n_would_submit"] == 1
    assert out["summary"]["would_submit"] == legs
    assert out["summary"]["armed"] is True and out["summary"]["mode"] == "preview"


def test_last_run_submit_snapshot_has_no_would_submit_keys(tmp_path):
    # submit/历史快照无 would_submit 键 → summary 形状与此前逐字节一致(加法式,零回归)
    p = tmp_path / "status.json"
    p.write_text(json.dumps({"ran_at": 7, "mode": "submit", "tier": "AUTO",
                             "strategy": "crowd_contra", "venue": "binance"}),
                 encoding="utf-8")
    out = glr.last_run_status(str(p))
    assert "n_would_submit" not in out["summary"] and "would_submit" not in out["summary"]


# ---------------------------------------------------------------------------
# 容量安全规则(spec §11.3:deploy ≤ safe_capacity/3)
# ---------------------------------------------------------------------------

def _book(low=3_582_051.48, aum_low=7_164_102.96):
    return {"aum_low": aum_low, "aum_high": aum_low,
            "participation_cap_aum": aum_low, "binding": "participation",
            "calibrated_range": {"low": low, "high": 71_641_029.64},
            "gross_sharpe": 1.566, "n_periods": 234, "data_quality": "OK"}


def _capacity_snapshot(crowd_low=3_582_051.48):
    return {"provenance": "UNCALIBRATED_PRINCIPLED",
            "books": {"crowd_contra": _book(low=crowd_low),
                      "carry": _book(low=976_700.55),
                      "top_contra": _book()}}  # btc_spot_trend 无容量带(诚实不注)


def test_safe_capacity_est_picks_most_conservative_field():
    # calibrated_range.low(5% 参与率 × 单日角)< binding aum_low(10% 中位角)-> 取 low
    assert glr.safe_capacity_est(_book()) == pytest.approx(3_582_051.48)
    # 无 calibrated_range -> 退到 aum_low / participation_cap_aum 的现存最小值
    book = _book()
    del book["calibrated_range"]
    assert glr.safe_capacity_est(book) == pytest.approx(7_164_102.96)
    assert glr.safe_capacity_est({}) is None and glr.safe_capacity_est(None) is None
    assert glr.safe_capacity_est({"calibrated_range": {"low": float("nan")}}) is None


def test_capacity_safety_passes_with_utilization():
    block = glr.capacity_safety_block("crowd_contra", 1000.0, capacity=_capacity_snapshot())
    assert block["passes"] is True
    assert block["safe_capacity_est"] == pytest.approx(3_582_051.48)
    assert block["utilization"] == pytest.approx(1000.0 / 3_582_051.48, rel=1e-9)
    assert block["rule"] == "deploy <= safe_capacity/3"
    assert block["deploy_target_usdt"] == 1000.0
    assert "books.crowd_contra" in block["source"]


def test_capacity_safety_violates_warns_loudly_and_clamps_nothing():
    # safe/3 = 666.67 < deploy 1000 -> 违反;guidance 是建议,不 clamp suggested_caps
    g = glr.build_guidance(_legs14(), {"loads": False, "allowed_instruments": [], "path": "x"},
                           capacity=_capacity_snapshot(crowd_low=2000.0))
    block = g["capacity_safety"]
    assert block["passes"] is False
    assert block["utilization"] == pytest.approx(0.5)
    text = "\n".join(g["warnings"])
    assert "容量安全规则被违反" in text and "safe_capacity/3" in text and "人工治理审批" in text
    assert g["suggested_caps"]["max_gross_notional"] == 1100.0     # 未被 clamp


def test_capacity_safety_absent_is_null_and_warns_never_fabricates():
    g = glr.build_guidance(_legs14(), {"loads": False, "allowed_instruments": [], "path": "x"},
                           capacity=None)
    block = g["capacity_safety"]
    assert block["passes"] is None and block["safe_capacity_est"] is None
    assert block["utilization"] is None
    assert any("容量证据缺失" in w for w in g["warnings"])
    # 策略无容量带映射(未研究的策略)同样 null,不编造
    block2 = glr.capacity_safety_block("btc_spot_trend", 1000.0, capacity=_capacity_snapshot())
    assert block2["passes"] is None and block2["safe_capacity_est"] is None


def test_capacity_utilization_note_only_where_band_exists():
    cap = _capacity_snapshot()
    note = glr.capacity_utilization_note("carry_oi_majors", capacity=cap)
    assert note is not None
    # 注记含带端点/binding/provenance/利用率/规则判定
    assert "976700.55" in note and "participation" in note and "UNCALIBRATED_PRINCIPLED" in note
    assert "PASS" in note
    assert glr.capacity_utilization_note("top_contra", capacity=cap) is not None
    # btc_spot_trend 无容量带 -> None(诚实不注);capacity 缺失 -> None
    assert glr.capacity_utilization_note("btc_spot_trend", capacity=cap) is None
    assert glr.capacity_utilization_note("crowd_contra", capacity=None) is None


def test_build_report_attaches_capacity_block_and_notes(tmp_path, monkeypatch):
    strategies = {"crowd_contra": glr.strategy_snapshot(
        {leg["instrument"]: leg["weight"] for leg in _legs14()},
        {leg["instrument"]: leg["price"] for leg in _legs14()}, 555),
        "carry_oi_majors": {"decided_ts": 1, "n_legs": 0, "gross": 0.0, "legs": []},
        "btc_spot_trend": {"decided_ts": 1, "n_legs": 1, "gross": 1.0, "legs": []}}
    cap_path = tmp_path / "capacity.json"
    cap_path.write_text(json.dumps(_capacity_snapshot()), encoding="utf-8")
    fake_venues = {
        "binance_mainnet": {"base_url": "https://mainnet.example", "suffix": "_fm",
                            "mandate_file": str(tmp_path / "mandate.json"),
                            "adapter": _FakeAdapter},
    }
    monkeypatch.setattr(glr, "_VENUES", fake_venues)
    monkeypatch.setattr(glr, "_venue_paths", lambda suffix: {
        "kill_switch": str(tmp_path / f"ks{suffix}.json"),
        "status": str(tmp_path / f"status{suffix}.json"),
    })
    report = glr.build_report(now_ms=1000, strategies=strategies,
                              evidence_dir=str(tmp_path),
                              attestations_path=str(tmp_path / "att.json"),
                              smoke_paths={}, capacity_path=str(cap_path))
    block = report["guidance"]["capacity_safety"]
    assert block["passes"] is True
    assert block["safe_capacity_est"] == pytest.approx(3_582_051.48)
    # 有容量带的策略注记存在;btc_spot_trend 无带 -> 无注记键
    assert "capacity_utilization_note" in report["strategies"]["carry_oi_majors"]
    assert "capacity_utilization_note" in report["strategies"]["crowd_contra"]
    assert "capacity_utilization_note" not in report["strategies"]["btc_spot_trend"]
    # 调用方注入的 strategies 不被 mutate(注记只加在报告副本上)
    assert "capacity_utilization_note" not in strategies["carry_oi_majors"]
