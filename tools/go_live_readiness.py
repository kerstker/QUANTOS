"""Go-Live 上线就绪驱动器(2026-07-24,"Go-Live 上线向导"console 功能的后端半)。

回答一个真人上线前最关心的问题:**每个 live venue 现在到底有多ready、还差什么**——
尤其是 Binance 期货 mainnet 首发(crowd_contra 14 腿)。产物
`evidence/go_live_readiness.json` 由只读 console 端点
`/api/v1/console/go-live-readiness` 原样serve(见 services/console/api/go_live_views.py)。

## 输入(全部本地文件 + 离线信号计算,**绝无网络**)

- venue/策略注册表:`tools/live_track_advance.py::_VENUES`/`_STRATEGIES`(唯一真源,加 venue
  或策略 = 那里加一行,本驱动器自动跟随);
- key 权限证据:`evidence/key_permissions_<venue>.json`(由
  `python -m tools.verify_key_permissions --venue <exchange> --write-evidence ...` 人工产出;
  缺 = 诚实 UNVERIFIED,本驱动器**绝不自己发起校验**——那需要网络 + 凭据);
- 只读 smoke 证据:`evidence/binance_mainnet_smoke.json` / `evidence/mexc_mainnet_smoke.json`;
- mandate:`key/live_mandate*.json`(经 `live/governance/mandate_store.load_mandate` 解析,
  域校验镜像 `live/governance/mandate.evaluate_mandate` 的 venue/exchange 双域);
- attestations:`key/live_validation_attestations.json`;
- kill-switch:经 `live/governance/kill_switch_store.load_kill_switch_state`(SQLite 权威 +
  JSON 镜像,损坏即 fail-closed tripped);
- 最近一批状态:`data/cache/live_track_status*.json`(`_venue_paths` 唯一真源);
- 信号:`_STRATEGIES[sid]["signal"]` 五元组(读本地行情快照,复用 live_track 同一信号核,
  不另造信号)。
- 容量带:`data/cache/capacity_snapshot.json`(tools/capacity_research.py 的
  UNCALIBRATED_PRINCIPLED 区间带)—— guidance.capacity_safety(spec §11.3 deploy ≤ safe/3,
  最保守角)+ 各有容量带策略的 capacity_utilization_note;缺证据 = passes null + 告警,
  绝不编造容量。

## Fail-soft 纪律

任何文件缺失/损坏/形状不符都诚实落成 ABSENT/UNVERIFIED/loads=False 标记,**绝不崩驱动器**;
信号计算异常逐策略记 `error` 类型名。本驱动器是**诊断清单,不是授权机制**:它绝不 arm 任何
安全旗标、绝不读凭据值、绝不下单;guidance 里的 suggested_caps 是给真人重签 mandate 的
建议数字,代码不代签(镜像 mandate_store "代码不代填数字"的哲学)。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from typing import Any, Dict, Optional

from live.governance.kill_switch_store import load_kill_switch_state
from live.governance.mandate_store import load_mandate
from services.execution.backtest_authority import (
    LEGACY_INVALIDATION_CODE,
    is_authoritative_backtest_record,
)
from tools.cache_io import atomic_write_json
from tools.crypto_oi_research import build_panel
from tools.live_track_advance import _STRATEGIES, _VENUES, _venue_paths

OUTPUT = os.path.join("evidence", "go_live_readiness.json")

_ATTESTATIONS_PATH = os.path.join("key", "live_validation_attestations.json")
_SMOKE_ARTIFACTS = {
    "binance_mainnet": os.path.join("evidence", "binance_mainnet_smoke.json"),
    "mexc": os.path.join("evidence", "mexc_mainnet_smoke.json"),
}

# 容量证据(W7 §11.3):tools/capacity_research.py 落的诚实区间带(UNCALIBRATED_PRINCIPLED)。
_CAPACITY_SNAPSHOT = os.path.join("data", "cache", "capacity_snapshot.json")
# 策略 -> 容量 snapshot books 键(那边用短名;无映射 = 该策略暂无容量带,诚实 None)。
_CAPACITY_BOOKS = {
    "crowd_contra": "crowd_contra",
    "carry_oi_majors": "carry",
    "top_contra": "top_contra",
}

# 交易所 min-notional(USDT),2026-07-24 实测 Binance USDT-M exchangeInfo(14 腿内币种);
# 未列出的腿按 5 USDT(Binance USDT-M 永续的常见下限)。
_MIN_NOTIONAL_USDT = {"BTC": 50.0, "ETH": 20.0, "BCH": 20.0, "ETC": 20.0, "LTC": 20.0}
_DEFAULT_MIN_NOTIONAL_USDT = 5.0

# guidance 的部署目标与工程参数:目标 $1000 部署;0.95 镜像 live_track_advance 的 mandate
# 工程余量(整批 gross 恰好贴上限会被浮点舍入随机顶过线);上限按 50 USDT 步进向上取整。
_DEPLOY_TARGET_USDT = 1000.0
_GROSS_HEADROOM = 0.95
_CAP_ROUND_STEP = 50.0
_SUGGESTED_SINGLE_ORDER_USDT = 150.0
_SUGGESTED_NET_USDT = 100.0
_SUGGESTED_PER_INSTRUMENT_USDT = 150.0

_GUIDANCE_VENUE = "binance_mainnet"
_GUIDANCE_STRATEGY = "crowd_contra"


# ---------------------------------------------------------------------------
# 通用 fail-soft 读取
# ---------------------------------------------------------------------------

def _load_json_or_none(path: str) -> Optional[dict]:
    """缺文件/坏文件/非 dict -> None(fail-safe,不崩驱动器)。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value)
    return None


def _float_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


# ---------------------------------------------------------------------------
# 各项检查(纯函数,路径显式传入,方便 tmp_path 测试)
# ---------------------------------------------------------------------------

def key_permissions_status(venue: str, *, evidence_dir: str = "evidence") -> Dict[str, Any]:
    """读 `evidence/key_permissions_<venue>.json`(verify_key_permissions --write-evidence 的
    产物)。缺/坏/字段不认识 -> UNVERIFIED;SAFE -> SAFE;UNSAFE/UNKNOWN -> NOT_SAFE
    (fail-closed:只要不是明确 SAFE 就不能当作已验证安全)。"""
    path = os.path.join(evidence_dir, f"key_permissions_{venue}.json")
    data = _load_json_or_none(path)
    if data is None:
        return {"status": "UNVERIFIED", "checked_at_ms": None}
    safety = data.get("safety")
    if safety == "SAFE":
        status = "SAFE"
    elif safety in ("UNSAFE", "UNKNOWN"):
        status = "NOT_SAFE"
    else:
        status = "UNVERIFIED"
    return {"status": status, "checked_at_ms": _int_or_none(data.get("checked_at_ms"))}


def smoke_status(venue: str, *, now_ms: int,
                 smoke_paths: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """只读 smoke 证据:verdict PASS/FAIL/ABSENT + 年龄。无登记的 artifact 路径或文件缺失
    -> ABSENT(诚实"没跑过");文件存在但损坏/verdict 非 PASS -> FAIL。"""
    path = (smoke_paths if smoke_paths is not None else _SMOKE_ARTIFACTS).get(venue)
    if not path or not os.path.exists(path):
        return {"verdict": "ABSENT", "age_ms": None}
    data = _load_json_or_none(path)
    if data is None:
        return {"verdict": "FAIL", "age_ms": None}
    ran_at = _int_or_none(data.get("ran_at_ms"))
    if ran_at is None:
        ran_at = _int_or_none(data.get("ran_at"))
    age_ms = (now_ms - ran_at) if ran_at is not None else None
    return {"verdict": "PASS" if data.get("verdict") == "PASS" else "FAIL",
            "age_ms": age_ms}


def mandate_status(venue_key: str, venue_cfg: Optional[dict] = None, *, now_ms: int,
                   mandate_path: Optional[str] = None) -> Dict[str, Any]:
    """mandate 存在性/可加载性/域匹配/限额。域校验镜像 `evaluate_mandate` 的双域:
    venue 域 = 适配器 venue_mode(mainnet base -> "mainnet",否则 "testnet"),
    exchange 域 = 适配器 venue_name(类属性 unbound 访问,不实例化、不触发 mainnet 闸门,
    与 tools/verify_key_permissions.py 同一手法)。任何加载失败 -> loads=False,其余 None。"""
    cfg = venue_cfg if venue_cfg is not None else _VENUES[venue_key]
    path = mandate_path if mandate_path is not None else cfg["mandate_file"]
    expected_venue = "mainnet" if cfg["base_url"] == cfg["adapter"].MAINNET_BASE else "testnet"
    expected_exchange = cfg["adapter"].venue_name
    result: Dict[str, Any] = {
        "path": path,
        "present": os.path.exists(path),
        "loads": False,
        "expired": None,
        "expires_at": None,
        "venue_domain_ok": None,
        "exchange_domain_ok": None,
        "expected_venue": expected_venue,
        "expected_exchange": expected_exchange,
        "allowed_instruments": [],
        "max_gross_notional": None,
        "max_single_order_notional": None,
        "max_net_notional": None,
    }
    if not result["present"]:
        return result
    try:
        mandate = load_mandate(path)
    except Exception:
        return result
    result.update({
        "loads": True,
        "expired": (mandate.expires_at is not None and now_ms > mandate.expires_at),
        "expires_at": mandate.expires_at,
        "venue_domain_ok": mandate.venue == expected_venue,
        "exchange_domain_ok": mandate.exchange == expected_exchange,
        "allowed_instruments": sorted(mandate.allowed_instruments),
        "max_gross_notional": mandate.max_gross_notional,
        "max_single_order_notional": mandate.max_single_order_notional,
        "max_net_notional": mandate.max_net_notional,
    })
    return result


def attestations_status(strategy_ids, *, path: str = _ATTESTATIONS_PATH) -> Dict[str, Any]:
    """每个策略的人工 attestation 存在性 + kind。文件缺/坏 -> 全 present=False。"""
    data = _load_json_or_none(path) or {}
    out: Dict[str, Any] = {}
    for sid in strategy_ids:
        row = data.get(sid)
        out[sid] = {"present": isinstance(row, dict),
                    "kind": row.get("kind") if isinstance(row, dict) else None}
    return out


def kill_switch_tripped(ks_path: str) -> bool:
    """kill-switch 是否跳闸。状态损坏 -> load_kill_switch_state 自身 fail-closed 成 tripped;
    这里再兜一层:任何读取异常 -> True(绝不把"读不出"报成"未跳闸")。"""
    try:
        return bool(load_kill_switch_state(ks_path).tripped)
    except Exception:
        return True


def last_run_status(status_path: str) -> Dict[str, Any]:
    """最近一批 live_track 状态快照(per-venue 默认策略路径,`_venue_paths` 唯一真源)。
    summary 只挑小的标量字段,绝不整个转存(status JSON 里有大块 TCA 明细)。"""
    data = _load_json_or_none(status_path)
    if data is None:
        return {"status_path": status_path, "present": False, "at_ms": None, "summary": {}}
    summary = {k: data[k] for k in ("mode", "tier", "strategy", "venue", "armed") if k in data}
    # preview 演练留痕(2026-07-24):status JSON 仅在 preview 模式写 would_submit——存在即
    # 透传(列表很小,≤ 信号腿数),上线向导页在一次 /preview 演练后能直接看到"本来会
    # 提交什么";submit/历史快照无此键,summary 形状不变。
    if "would_submit" in data:
        legs = data.get("would_submit") or []
        summary["n_would_submit"] = len(legs)
        summary["would_submit"] = legs
    return {"status_path": status_path, "present": True,
            "at_ms": _int_or_none(data.get("ran_at")), "summary": summary}


# ---------------------------------------------------------------------------
# 策略信号快照(复用 live_track 注册表同一信号核)
# ---------------------------------------------------------------------------

def strategy_snapshot(weights: Dict[str, float], prices: Dict[str, float],
                      decided_ts: int) -> Dict[str, Any]:
    legs = [
        {"instrument": inst, "weight": float(w),
         "price": (_float_or_none(prices.get(inst)) if prices else None)}
        for inst, w in sorted(weights.items())
    ]
    return {"decided_ts": int(decided_ts), "n_legs": len(legs),
            "gross": round(sum(abs(leg["weight"]) for leg in legs), 10), "legs": legs}


def compute_strategy_snapshots() -> Dict[str, Any]:
    """逐策略跑 `_STRATEGIES` 注册的信号五元组(本地快照,无网络)。面板类三条策略共享一次
    build_panel;任何异常逐策略记 error 类型名,绝不崩驱动器(fail-soft 纪律)。"""
    snapshots: Dict[str, Any] = {}
    panel_bundle = None
    for sid, cfg in _STRATEGIES.items():
        try:
            signal = cfg["signal"]
            if sid == "btc_spot_trend":
                weights, prices, decided_ts, _ls, _ev = signal()
            else:
                if panel_bundle is None:
                    panel_bundle = build_panel()
                weights, prices, decided_ts, _ls, _ev = signal(panel_bundle)
            snapshots[sid] = strategy_snapshot(weights, prices, decided_ts)
        except Exception as exc:
            snapshots[sid] = {"error": type(exc).__name__, "decided_ts": None,
                              "n_legs": 0, "gross": None, "legs": []}
    return snapshots


# ---------------------------------------------------------------------------
# guidance(crowd_contra × binance_mainnet):还差什么 + 建议 mandate 数字
# ---------------------------------------------------------------------------

def required_instruments(legs) -> list:
    return sorted(leg["instrument"] for leg in legs)


def min_deploy_for_all_legs(legs, *, min_notional: Optional[dict] = None,
                            default_min: float = _DEFAULT_MIN_NOTIONAL_USDT) -> tuple:
    """让**每一条腿**都满足交易所 min-notional 的最小 deploy(= 账户级目标总名义):
    腿 i 名义 = deploy × |w_i| ≥ min_notional_i -> deploy ≥ min_notional_i / |w_i|,取 max。
    返回 (min_deploy, binding_leg)。"""
    table = min_notional if min_notional is not None else _MIN_NOTIONAL_USDT
    worst = 0.0
    binding = None
    for leg in legs:
        w = abs(float(leg["weight"]))
        if w <= 0:
            continue
        need = table.get(leg["instrument"], default_min) / w
        if need > worst:
            worst, binding = need, leg["instrument"]
    return worst, binding


def suggested_caps(min_deploy: float, *, target: float = _DEPLOY_TARGET_USDT,
                   headroom: float = _GROSS_HEADROOM,
                   step: float = _CAP_ROUND_STEP) -> Dict[str, float]:
    """给真人重签 mandate 的建议数字(代码不代签):gross 上限 = max(target, min_deploy)
    加 0.95 工程余量后按 50 USDT 步进向上取整;single/net/per-instrument 为固定建议值。"""
    gross = math.ceil(max(target, min_deploy) / headroom / step) * step
    return {"max_gross_notional": gross,
            "max_single_order_notional": _SUGGESTED_SINGLE_ORDER_USDT,
            "max_net_notional": _SUGGESTED_NET_USDT,
            "per_instrument": _SUGGESTED_PER_INSTRUMENT_USDT}


# ---------------------------------------------------------------------------
# 容量安全规则(spec §11.3:实际部署资金 ≤ 安全容量估计 / 3,除非人工治理审批)
# ---------------------------------------------------------------------------

def capacity_book(capacity: Optional[dict], strategy_id: str) -> Optional[dict]:
    """容量 snapshot 中本策略的 book 条目;无映射/无条目/形状不符 -> None(绝不编造容量)。"""
    book_key = _CAPACITY_BOOKS.get(strategy_id)
    books = capacity.get("books") if isinstance(capacity, dict) else None
    entry = books.get(book_key) if book_key and isinstance(books, dict) else None
    return entry if isinstance(entry, dict) else None


def safe_capacity_est(book: Optional[dict]) -> Optional[float]:
    """安全容量估计,取存量字段里**最保守**的值:`calibrated_range.low`(= 5% 参与率 ×
    单日执行的最保守角)与 binding `aum_low` / `participation_cap_aum`(10% 参与率中位角)
    中现存正数的最小值。选择记录(2026-07-24):UNCALIBRATED_PRINCIPLED 区间带已给低/高端,
    部署安全规则用带低端,不用中位/高端,更不用冲击-only 的 $B 级虚高(那正是容量引擎首跑
    抓到的自欺);字段全缺/非有限 -> None(绝不编造)。"""
    if not isinstance(book, dict):
        return None
    candidates = []
    rng = book.get("calibrated_range")
    if isinstance(rng, dict):
        candidates.append(_float_or_none(rng.get("low")))
    candidates.append(_float_or_none(book.get("aum_low")))
    candidates.append(_float_or_none(book.get("participation_cap_aum")))
    present = [c for c in candidates if c is not None and c > 0]
    return min(present) if present else None


def capacity_safety_block(strategy_id: str, deploy_target: float, *,
                          capacity: Optional[dict],
                          source_path: str = _CAPACITY_SNAPSHOT) -> Dict[str, Any]:
    """spec §11.3 容量安全规则块。证据缺失 -> passes=None + note(fail-closed,绝不编造);
    违反 -> passes=False(guidance 是建议性质,**不 clamp 任何数字**,由调用方大声告警)。"""
    book = capacity_book(capacity, strategy_id)
    safe = safe_capacity_est(book)
    source = f"{source_path}#books.{_CAPACITY_BOOKS.get(strategy_id)}"
    if safe is None:
        return {"safe_capacity_est": None, "source": source,
                "deploy_target_usdt": deploy_target, "utilization": None,
                "rule": "deploy <= safe_capacity/3", "passes": None,
                "note": "容量证据缺失或形状不符 —— 无法校验 deploy ≤ safe/3(spec §11.3);"
                        "绝不按无证据放行,上线前必须由真人确认容量"}
    utilization = deploy_target / safe
    passes = deploy_target <= safe / 3.0
    note = (f"safe_capacity 取最保守角(calibrated_range.low 与 binding aum 现存者的最小值,"
            f"UNCALIBRATED_PRINCIPLED 未校准原则带,诊断口径);deploy {deploy_target:.0f} USDT "
            f"= safe 的 {utilization:.4%},规则上限 safe/3 = {safe / 3.0:.2f} USDT")
    if not passes:
        note += (" —— 违反 spec §11.3:实际部署资金不得超过安全容量 1/3,"
                 "除非通过人工治理审批")
    return {"safe_capacity_est": safe, "source": source,
            "deploy_target_usdt": deploy_target, "utilization": utilization,
            "rule": "deploy <= safe_capacity/3", "passes": passes, "note": note}


def capacity_utilization_note(strategy_id: str, *, capacity: Optional[dict],
                              target: float = _DEPLOY_TARGET_USDT,
                              source_path: str = _CAPACITY_SNAPSHOT) -> Optional[str]:
    """有容量带的 live-track 策略给一句利用率注记(与 guidance 同一最保守 safe 口径);
    无容量带 -> None(诚实不注)。"""
    book = capacity_book(capacity, strategy_id)
    if book is None:
        return None
    block = capacity_safety_block(strategy_id, target, capacity=capacity,
                                  source_path=source_path)
    if block["passes"] is None:
        return ("容量带存在但 safe_capacity 字段缺失/非有限 —— 无法校验 deploy ≤ safe/3"
                "(spec §11.3),需人工确认容量")
    rng = book.get("calibrated_range") if isinstance(book.get("calibrated_range"), dict) else {}
    provenance = capacity.get("provenance") if isinstance(capacity, dict) else None
    verdict = "PASS" if block["passes"] else "违反(需人工治理审批)"
    return (f"容量带 [{_float_or_none(rng.get('low'))}, {_float_or_none(rng.get('high'))}] AUM"
            f"(binding={book.get('binding')}, {provenance});deploy 目标 {target:.0f} USDT 对"
            f"最保守 safe={block['safe_capacity_est']:.2f} 的利用率 = {block['utilization']:.4%};"
            f"规则 deploy ≤ safe/3:{verdict}")


def build_guidance(legs, mandate_info: Dict[str, Any], *,
                   target: float = _DEPLOY_TARGET_USDT,
                   min_notional: Optional[dict] = None,
                   capacity: Optional[dict] = None,
                   capacity_path: str = _CAPACITY_SNAPSHOT) -> Dict[str, Any]:
    table = min_notional if min_notional is not None else _MIN_NOTIONAL_USDT
    required = required_instruments(legs)
    allowed = set(mandate_info.get("allowed_instruments") or [])
    missing = [inst for inst in required if inst not in allowed]
    stale = sorted(allowed - set(required))
    min_deploy, binding = min_deploy_for_all_legs(legs, min_notional=table)
    caps = suggested_caps(min_deploy, target=target)

    warnings = []
    if not mandate_info.get("loads"):
        warnings.append(
            f"{_GUIDANCE_VENUE} 的 mandate 缺失或不可加载(path={mandate_info.get('path')})"
            "—— 所有批次恒升人工评审;先由真人签署该文件")
    else:
        if mandate_info.get("expired"):
            warnings.append(
                f"mandate 已过期(expires_at={mandate_info.get('expires_at')})"
                "—— evaluate_mandate 恒 mandate_expired 升人工")
        if mandate_info.get("venue_domain_ok") is False:
            warnings.append(
                f"mandate venue 域不匹配(签了 {mandate_info.get('expected_venue')!r} 之外的值)"
                "—— evaluate_mandate 恒 venue_mismatch 升人工")
        if mandate_info.get("exchange_domain_ok") is False:
            warnings.append(
                f"mandate exchange 域不匹配(应为 {mandate_info.get('expected_exchange')!r})"
                "—— evaluate_mandate 恒 exchange_mismatch 升人工")
    if missing:
        warnings.append(
            f"{len(missing)}/{len(required)} 条信号腿不在 mandate allowed_instruments 里: "
            f"{', '.join(missing)} —— 这些腿的订单恒 instrument_not_allowed 升人工")
    if stale:
        warnings.append(
            f"mandate 授权了 {len(stale)} 个当前信号没有的标的: {', '.join(stale)}"
            " —— 重签时按最新信号腿收敛授权面")
    gross_cap = _float_or_none(mandate_info.get("max_gross_notional"))
    if gross_cap is not None and gross_cap > 0 and legs:
        deploy = _GROSS_HEADROOM * gross_cap
        below = [leg["instrument"] for leg in legs
                 if deploy * abs(float(leg["weight"]))
                 < table.get(leg["instrument"], _DEFAULT_MIN_NOTIONAL_USDT)]
        if below:
            warnings.append(
                f"按当前 mandate gross 上限 {gross_cap} 推算 deploy≈{deploy:.2f} USDT,"
                f"{len(below)}/{len(legs)} 条腿名义额低于交易所 min-notional: {', '.join(below)}"
                f" —— gross 上限提到 ≥ {caps['max_gross_notional']} 全部腿才可下单")
    if min_deploy > target:
        warnings.append(
            f"min_deploy_for_all_legs={min_deploy:.2f} 超过部署目标 {target}"
            " —— 目标仓位下必有腿低于 min-notional,需上调目标或接受量化后的部分覆盖")
    if legs:
        leg_notional = target / len(legs)
        warnings.append(
            f"按 deploy≈{target:.0f} USDT / {len(legs)} 腿,单腿目标名义≈{leg_notional:.1f} USDT,"
            "周度调仓 delta 多数 < 交易所 min-notional(5–50 USDT)——调仓将是量化/部分的;"
            "这是工程约束,不是 alpha 衰减信号")

    cap_safety = capacity_safety_block(_GUIDANCE_STRATEGY, target, capacity=capacity,
                                       source_path=capacity_path)
    if cap_safety["passes"] is False:
        warnings.append(
            f"容量安全规则被违反:deploy 目标 {target:.0f} USDT > safe_capacity/3 = "
            f"{cap_safety['safe_capacity_est'] / 3.0:.2f} USDT"
            f"(safe={cap_safety['safe_capacity_est']:.2f},{cap_safety['source']})"
            "—— spec §11.3:实际部署资金不得超过安全容量 1/3,除非人工治理审批;"
            "guidance 是建议、不 clamp 任何数字,但这个违反必须大声标出")
    elif cap_safety["passes"] is None:
        warnings.append(
            f"{_GUIDANCE_STRATEGY} 容量证据缺失 —— 无法校验 deploy ≤ safe/3(spec §11.3);"
            "绝不按无证据放行,上线前需人工确认容量")

    return {
        "venue": _GUIDANCE_VENUE,
        "strategy": _GUIDANCE_STRATEGY,
        "deploy_target_usdt": target,
        "required_instruments": required,
        "missing_from_mandate": missing,
        "stale_in_mandate": stale,
        "min_deploy_for_all_legs": min_deploy,
        "min_deploy_binding_leg": binding,
        "suggested_caps": caps,
        "capacity_safety": cap_safety,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

def build_report(*, now_ms: Optional[int] = None,
                 strategies: Optional[Dict[str, Any]] = None,
                 evidence_dir: str = "evidence",
                 attestations_path: str = _ATTESTATIONS_PATH,
                 smoke_paths: Optional[Dict[str, str]] = None,
                 capacity_path: str = _CAPACITY_SNAPSHOT) -> Dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if strategies is None:
        strategies = compute_strategy_snapshots()
    else:
        strategies = {sid: dict(snap) for sid, snap in strategies.items()}  # 不 mutate 调用方注入

    capacity_document = _load_json_or_none(capacity_path)
    capacity_record = (
        capacity_document.get("authoritative_backtest_record")
        if isinstance(capacity_document, dict)
        else None
    )
    capacity_authoritative = bool(
        isinstance(capacity_record, dict)
        and is_authoritative_backtest_record(capacity_record)
    )
    capacity = capacity_document if capacity_authoritative else None
    # 每条 live-track 策略的容量利用率注记(只注有容量带的策略;spec §11.3 同一最保守 safe 口径)
    for sid, snap_i in strategies.items():
        if not isinstance(snap_i, dict):
            continue
        note = capacity_utilization_note(sid, capacity=capacity, source_path=capacity_path)
        if note is not None:
            snap_i["capacity_utilization_note"] = note

    attestations = attestations_status(_STRATEGIES.keys(), path=attestations_path)
    venues = []
    for venue_key, cfg in _VENUES.items():
        suffix = cfg["suffix"]
        paths = _venue_paths(suffix)
        venues.append({
            "venue": venue_key,
            "mainnet": cfg["base_url"] == cfg["adapter"].MAINNET_BASE,
            "suffix": suffix,
            "key_permissions": key_permissions_status(venue_key, evidence_dir=evidence_dir),
            "smoke": smoke_status(venue_key, now_ms=now, smoke_paths=smoke_paths),
            "mandate": mandate_status(venue_key, cfg, now_ms=now),
            "attestations": attestations,
            "kill_switch_tripped": kill_switch_tripped(paths["kill_switch"]),
            "last_run": last_run_status(paths["status"]),
        })

    guidance_mandate = next((v["mandate"] for v in venues if v["venue"] == _GUIDANCE_VENUE),
                            {"loads": False, "allowed_instruments": [], "path": None})
    snap = strategies.get(_GUIDANCE_STRATEGY) or {}
    legs = snap.get("legs") or []
    if legs:
        guidance = build_guidance(legs, guidance_mandate, capacity=capacity,
                                  capacity_path=capacity_path)
    else:
        guidance = {
            "venue": _GUIDANCE_VENUE,
            "strategy": _GUIDANCE_STRATEGY,
            "deploy_target_usdt": _DEPLOY_TARGET_USDT,
            "required_instruments": [],
            "missing_from_mandate": [],
            "stale_in_mandate": [],
            "min_deploy_for_all_legs": None,
            "min_deploy_binding_leg": None,
            "suggested_caps": None,
            "capacity_safety": capacity_safety_block(
                _GUIDANCE_STRATEGY, _DEPLOY_TARGET_USDT, capacity=capacity,
                source_path=capacity_path),
            "warnings": [f"{_GUIDANCE_STRATEGY} 信号不可用"
                         f"(error={snap.get('error')})——guidance 无法计算,先修信号/数据"],
        }

    return {
        "artifact": "GO_LIVE_READINESS",
        "generated_at_ms": now,
        "readiness": "L2_DRY_RUN_ONLY",
        "capacity_evidence_status": (
            "AUTHORITATIVE" if capacity_authoritative else LEGACY_INVALIDATION_CODE
        ),
        "venues": venues,
        "strategies": strategies,
        "guidance": guidance,
        "honesty_note": (
            "Go-live 就绪清单:全部项绿也只是'具备提交人工评审的材料',绝不代表可以实盘——"
            "L2 边界(SafetyConfig 五旗标 + readiness pin)不变,live 仍需多方人工 mandate + "
            "人工 --arm-live。key_permissions 来自人工跑 verify_key_permissions --write-evidence "
            "的脱敏证据(缺 = UNVERIFIED,本驱动器绝不自己联网校验);mandate/attestation 读本地 "
            "key/ 文件但不涉及任何密钥值;kill-switch 读 SQLite 权威,损坏即 fail-closed tripped。"
            "guidance.suggested_caps 是给真人重签 mandate 的建议,代码不代签任何数字。"
            "guidance.capacity_safety 与各策略 capacity_utilization_note 是 spec §11.3 "
            "deploy ≤ safe_capacity/3 的只读诊断:safe 取容量带最保守角(UNCALIBRATED_PRINCIPLED),"
            "缺证据 passes=null 绝不编造,违反只大声告警、不 clamp 任何数字。"
        ),
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Go-live 上线就绪驱动器:汇总每个 live venue 的就绪状态 + crowd_contra × "
                    "binance_mainnet 的缺口 guidance,落 evidence/go_live_readiness.json"
                    "(全本地输入,无网络,只读诊断不授权)。")
    parser.add_argument("--output", default=OUTPUT,
                        help=f"输出路径(默认 {OUTPUT})")
    args = parser.parse_args(argv)

    report = build_report()
    atomic_write_json(args.output, report, indent=2)

    print(f"[go_live_readiness] artifact -> {args.output} "
          f"(venues={len(report['venues'])}, strategies={len(report['strategies'])})")
    for v in report["venues"]:
        print(f"  {v['venue']:<16} mainnet={v['mainnet']!s:<5} "
              f"key={v['key_permissions']['status']:<10} smoke={v['smoke']['verdict']:<6} "
              f"mandate(loads={v['mandate']['loads']!s:<5} expired={v['mandate']['expired']}) "
              f"kill_switch_tripped={v['kill_switch_tripped']}")
    guidance = report["guidance"]
    print(f"  guidance[{guidance['strategy']}×{guidance['venue']}]: "
          f"required={len(guidance['required_instruments'])} "
          f"missing={len(guidance['missing_from_mandate'])} "
          f"min_deploy={guidance['min_deploy_for_all_legs']} "
          f"suggested_gross={(guidance['suggested_caps'] or {}).get('max_gross_notional')}")
    for w in guidance["warnings"]:
        print(f"    ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
