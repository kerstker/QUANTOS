"""SHADOW 运行时驱动器(727 §12.2,Phase 7 S3,2026-07-27)。设计:
docs/engineering/specs/2026-07-27-shadow-phase7-design.md §3 S3。

每轮做三件事:① 复用 `tools/live_track_advance.py` 的策略信号注册表(`_STRATEGIES`,
与 live preview 同一信号核,**绝不重算一套信号**)取当前权重/决策价;② 逐标的经
venue 适配器的只读公共 `depth()`(hasattr 可选能力,无则 BOOK_UNAVAILABLE 诚实)抓
真实盘口,`observe_batch` 生成 SHADOW 观测;③ 落 `live/execution/shadow_store.py`
的 append-only 账本(观测与其 canonical 事件同事务),并把 shadow_ready 全条件门
写进证据快照。

**只读、绝不下单**:depth/book_ticker 都是不签名的公共端点;venue 适配器以**空凭证**
构造(适配器只在签名请求时才使用 key,本驱动器结构性只调无签名方法——不加载任何
凭证文件/env);venue 注册从 `_VENUES` 按 base_url 白名单结构性筛出 testnet/demo
项(mainnet/spot 项进不来,不是按名字挡)。信号面板来自本地 PIT 库(离线),
唯一的网络面就是 venue 适配器的公共行情 GET。

默认单轮;``--follow`` 是显式前台轮询(Ctrl-C 干净退出,绝不拉起后台进程)。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from typing import Mapping, Optional

from live.execution.shadow import ShadowLeg, observe_batch
from live.execution.shadow_store import SqliteShadowStore
from live.exchange.binance_futures import BinanceFuturesAdapter
from live.exchange.bybit_futures import BybitFuturesAdapter
from tools.cache_io import atomic_write_json
from tools.live_track_advance import _STRATEGIES, _VENUES

DEFAULT_DB = os.path.join("data", "cache", "shadow_observations.db")
DEFAULT_EVIDENCE = os.path.join("evidence", "shadow_status.json")
DEFAULT_STRATEGY = "crowd_contra"
DEFAULT_VENUE = "binance_testnet"
DEFAULT_NOTIONAL = 10_000.0
DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_MAX_OBS_AGE_MS = 86_400_000           # 观测新鲜度门(24h,UNCALIBRATED 工程默认)

# SHADOW 只认 testnet/demo(base_url 白名单,结构性——mainnet/spot 注册项进不来)
_SHADOW_ALLOWED_BASES = frozenset({
    BinanceFuturesAdapter.TESTNET_BASE,
    BybitFuturesAdapter.TESTNET_BASE,
    BybitFuturesAdapter.DEMO_BASE,
})
# CLI 别名 -> _VENUES 注册键(别名带 _testnet 后缀,把「这是模拟环境」写进名字)
_SHADOW_VENUE_ALIASES = {
    "binance_testnet": "binance",
    "bybit_testnet": "bybit",
    "bybit_demo": "bybit_demo",
}


def shadow_venues() -> dict:
    """从 `tools/live_track_advance.py::_VENUES` 结构性筛出 SHADOW 可用 venue:
    base_url ∈ testnet/demo 白名单 ∧ 无 mainnet_only/spot 标记。单一定义处,
    _VENUES 演进(新增 testnet)自动可见,mainnet 项结构性进不来。"""
    out = {}
    for alias, key in _SHADOW_VENUE_ALIASES.items():
        cfg = _VENUES.get(key)
        if cfg is None:
            continue
        if cfg["base_url"] not in _SHADOW_ALLOWED_BASES:
            continue
        if cfg.get("mainnet_only") or cfg.get("spot"):
            continue
        out[alias] = cfg
    return out


def build_shadow_venue(alias: str):
    """构造只读 SHADOW venue。**空凭证构造**:depth/book_ticker 是不签名公共端点,
    适配器只在签名请求时才使用 key——本驱动器结构性只调无签名方法,绝不加载
    任何凭证文件/env。未知别名或 mainnet/spot 注册项 -> SystemExit(fail-closed)。"""
    venues = shadow_venues()
    cfg = venues.get(alias)
    if cfg is None:
        raise SystemExit(
            f"unknown or non-testnet shadow venue {alias!r} — "
            f"allowed: {sorted(venues)} (mainnet/spot venues are structurally excluded)")
    return cfg["adapter"](api_key="", api_secret="", base_url=cfg["base_url"])


def build_shadow_legs(weights: Mapping[str, float], prices: Mapping[str, float],
                      *, notional: float) -> list:
    """信号权重 × 名义本金 / 决策价 -> ShadowLeg 序列(与 live preview 同语义:
    权重正=BUY、负=SELL,数量=|weight|×notional/决策价)。零权重跳过;
    非零权重缺合法决策价 -> ValueError(fail-closed,绝不静默缩小 book);
    输出按标的排序(确定性)。"""
    if (isinstance(notional, bool) or not math.isfinite(float(notional))
            or notional <= 0):
        raise ValueError(f"notional must be finite and positive, got {notional!r}")
    legs = []
    for inst in sorted(weights):
        weight = float(weights[inst])
        if weight == 0.0:
            continue
        price = prices.get(inst)
        if price is None or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError(
                f"missing/invalid decision price for weighted instrument {inst!r} "
                "— refusing to silently shrink the shadow book")
        legs.append(ShadowLeg(
            instrument_id=str(inst),
            side="BUY" if weight > 0 else "SELL",
            quantity=abs(weight) * float(notional) / float(price),
            decision_price=float(price)))
    return legs


def run_shadow_pass(*, venue, store: SqliteShadowStore, strategy_id: str,
                    legs: list, now_ms: int, latency_clock=None,
                    max_book_age_ms: int = 60_000) -> dict:
    """单轮 SHADOW 观测:逐标的只读 `depth()`(hasattr 探测;无该能力/调用异常/
    返回 None -> BOOK_UNAVAILABLE 诚实,不炸整轮)-> observe_batch -> 逐条落账。

    latency = depth 调用往返实测(latency_clock 可注入,默认 time.perf_counter——
    驱动器是 IO 边界,墙钟只在这里);venue 无 depth 能力时未发起任何请求,
    latency 记 0 并计入 unavailable(绝不编造一个「测得的」往返)。"""
    if isinstance(now_ms, bool) or int(now_ms) != now_ms or int(now_ms) < 0:
        raise ValueError("now_ms must be a non-negative integer ms")
    now_ms = int(now_ms)
    clock = latency_clock or time.perf_counter
    depth_fn = getattr(venue, "depth", None)
    books: dict = {}
    latencies: dict = {}
    for leg in legs:
        book = None
        latency = 0
        if depth_fn is not None:
            started = clock()
            try:
                book = depth_fn(leg.instrument_id)
            except Exception:
                book = None                              # fail-soft,与适配器同型
            latency = max(0, int((clock() - started) * 1000))
        books[leg.instrument_id] = book
        latencies[leg.instrument_id] = latency
    observations = observe_batch(legs, books, latencies_ms=latencies, now_ms=now_ms,
                                 max_book_age_ms=max_book_age_ms)
    run_label = f"{strategy_id}:{now_ms}"
    appended = 0
    replays = 0
    for obs in observations:
        _, inserted = store.append(obs, run_label=run_label, recorded_at_ms=now_ms)
        if inserted:
            appended += 1
        else:
            replays += 1
    executable = [o for o in observations if o.walk.executable]
    slips = [abs(float(o.slippage_bps_vs_decision)) for o in executable
             if o.slippage_bps_vs_decision is not None]
    return {
        "strategy_id": strategy_id,
        "run_label": run_label,
        "now_ms": now_ms,
        "legs": len(legs),
        "observations": len(observations),
        "appended": appended,
        "idempotent_replays": replays,
        "unavailable": sum(1 for o in observations
                           if o.walk.reason == "BOOK_UNAVAILABLE"),
        "stale": sum(1 for o in observations
                     if o.walk.reason in ("BOOK_STALE", "BOOK_FROM_FUTURE")),
        "executable_count": len(executable),
        "mean_abs_slippage_bps": (sum(slips) / len(slips)) if slips else None,
    }


def build_shadow_readiness(*, store: SqliteShadowStore, now_ms: int,
                           max_obs_age_ms: int = DEFAULT_MAX_OBS_AGE_MS) -> dict:
    """shadow_ready 全条件门(镜像 tools/paper_health.py 的「只在全部条件满足时
    为 true」,绝不假通过):NO_OBSERVATIONS / CHAIN_INVALID / OUTBOX_CHAIN_INVALID /
    LATEST_OBSERVATION_STALE(以本轨道自己的 recorded_at_ms 为准——venue as_of_ms
    可缺,MEXC 恒缺)/ NO_EXECUTABLE_OBSERVATION(连一次真实盘口成交模拟都没
    跑成,不算 shadow)。纯核:只读 store,无网络无墙钟。"""
    if isinstance(now_ms, bool) or int(now_ms) != now_ms or int(now_ms) < 0:
        raise ValueError("now_ms must be a non-negative integer ms")
    if (isinstance(max_obs_age_ms, bool) or int(max_obs_age_ms) != max_obs_age_ms
            or int(max_obs_age_ms) <= 0):
        raise ValueError("max_obs_age_ms must be a positive integer ms")
    now_ms = int(now_ms)
    blockers: list = []
    count = store.count()
    chain = store.chain_valid()
    outbox_chain = store.event_outbox_chain_valid()
    if count == 0:
        blockers.append("NO_OBSERVATIONS")
    if not chain:
        blockers.append("CHAIN_INVALID")
    if not outbox_chain:
        blockers.append("OUTBOX_CHAIN_INVALID")
    latest_recorded = None
    latest_as_of = None
    if count:
        rows = store.records()
        latest_recorded = max(r["recorded_at_ms"] for r in rows)
        as_ofs = [r["as_of_ms"] for r in rows if r["as_of_ms"] is not None]
        latest_as_of = max(as_ofs) if as_ofs else None
        if now_ms - latest_recorded > int(max_obs_age_ms):
            blockers.append("LATEST_OBSERVATION_STALE")
        # 防御性读取:篡改过的 payload(链已判 INVALID)可能缺 walk——此时必然
        # 「不可执行」,NO_EXECUTABLE_OBSERVATION 与 CHAIN_INVALID 并列,绝不崩。
        if not any(isinstance(r["payload"].get("walk"), dict)
                   and r["payload"]["walk"].get("executable") is True
                   for r in rows):
            blockers.append("NO_EXECUTABLE_OBSERVATION")
    return {
        "shadow_ready": not blockers,
        "blockers": blockers,
        "observation_count": count,
        "latest_recorded_at_ms": latest_recorded,
        "latest_as_of_ms": latest_as_of,
        "chain_valid": chain,
        "outbox_chain_valid": outbox_chain,
        "max_obs_age_ms": int(max_obs_age_ms),
    }


def build_shadow_status(*, strategy_id: str, venue_alias: str, db_path: str,
                        last_pass: Optional[dict], readiness: dict,
                        now_ms: int) -> dict:
    """证据快照(每轮原子重写)。无观测时 readiness 诚实带 NO_OBSERVATIONS,
    last_pass 为 None,绝不编造。"""
    return {
        "schema_version": 1,
        "generated_at_ms": now_ms,
        "strategy_id": strategy_id,
        "venue": venue_alias,
        "db_path": db_path,
        "last_pass": last_pass,
        "readiness": readiness,
        "provenance": "SHADOW",
        "honesty_note": (
            "SHADOW 观测是只读盘口比较(此刻真实盘口可成交价 vs 决策价/paper 语义)——"
            "不是成交、不是 paper fill 正确性的证明;盘口来自 venue 公共无签名端点,"
            "观测时点以 recorded_at_ms 为准,venue as_of_ms 可缺(如实 None)。"),
        "safety": {
            "public_market_data_only": True,
            "credentials_accessed": False,
            "order_path_touched": False,
        },
    }


def _open_store(db_path: str) -> SqliteShadowStore:
    return SqliteShadowStore(db_path, create=not os.path.exists(db_path))


def run_once(*, strategy_id: str, venue_alias: str, notional: float, db_path: str,
             evidence_path: str, max_obs_age_ms: int = DEFAULT_MAX_OBS_AGE_MS,
             venue=None, signal_fn=None, now_ms: Optional[int] = None) -> dict:
    """单轮(venue/signal_fn/now_ms 可注入——测试零网络;生产默认真 venue 真信号
    真墙钟,全部只在 IO 边界)。"""
    if signal_fn is None:
        spec = _STRATEGIES.get(strategy_id)
        if spec is None:
            raise SystemExit(
                f"unknown strategy {strategy_id!r} — registered: {sorted(_STRATEGIES)}")
        signal_fn = spec["signal"]
    weights, prices, _decided_ts, _ls_row, _evidence = signal_fn()
    legs = build_shadow_legs(weights, prices, notional=notional)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    resolved_venue = venue if venue is not None else build_shadow_venue(venue_alias)
    store = _open_store(db_path)
    try:
        last_pass = run_shadow_pass(venue=resolved_venue, store=store,
                                    strategy_id=strategy_id, legs=legs, now_ms=now)
        readiness = build_shadow_readiness(store=store, now_ms=now,
                                           max_obs_age_ms=max_obs_age_ms)
    finally:
        store.close()
    document = build_shadow_status(strategy_id=strategy_id, venue_alias=venue_alias,
                                   db_path=db_path, last_pass=last_pass,
                                   readiness=readiness, now_ms=now)
    atomic_write_json(evidence_path, document, indent=2)
    return document


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--venue", default=DEFAULT_VENUE)
    parser.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL,
                        help="shadow book notional in USDT (observation only)")
    parser.add_argument("--once", action="store_true", help="single pass (default)")
    parser.add_argument("--follow", action="store_true",
                        help="keep polling in the foreground until Ctrl-C")
    parser.add_argument("--interval-seconds", type=float,
                        default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    parser.add_argument("--max-obs-age-ms", type=int, default=DEFAULT_MAX_OBS_AGE_MS)
    args = parser.parse_args(argv)
    if args.strategy not in _STRATEGIES:
        parser.error(f"unknown strategy {args.strategy!r} — "
                     f"registered: {sorted(_STRATEGIES)}")
    if args.venue not in shadow_venues():
        parser.error(f"unknown or non-testnet shadow venue {args.venue!r} — "
                     f"allowed: {sorted(shadow_venues())}")
    if not math.isfinite(args.notional) or args.notional <= 0:
        parser.error("--notional must be positive")
    if not math.isfinite(args.interval_seconds) or args.interval_seconds < 1.0:
        parser.error("--interval-seconds must be at least 1")
    if args.max_obs_age_ms <= 0:
        parser.error("--max-obs-age-ms must be positive")

    venue = build_shadow_venue(args.venue)
    document: dict = {}
    try:
        while True:
            document = run_once(strategy_id=args.strategy, venue_alias=args.venue,
                                notional=args.notional, db_path=args.db,
                                evidence_path=args.evidence,
                                max_obs_age_ms=args.max_obs_age_ms, venue=venue)
            readiness = document["readiness"]
            print(json.dumps({
                "shadow_ready": readiness["shadow_ready"],
                "blockers": readiness["blockers"],
                "observations": document["last_pass"]["observations"],
                "executable": document["last_pass"]["executable_count"],
                "mean_abs_slippage_bps": document["last_pass"]["mean_abs_slippage_bps"],
                "order_path_touched": False,
            }, ensure_ascii=False))
            print(f"-> {args.evidence}")
            if not args.follow:
                break
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 0
    return 0 if document.get("readiness", {}).get("shadow_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DB",
    "DEFAULT_EVIDENCE",
    "DEFAULT_MAX_OBS_AGE_MS",
    "build_shadow_legs",
    "build_shadow_readiness",
    "build_shadow_status",
    "build_shadow_venue",
    "run_once",
    "run_shadow_pass",
    "shadow_venues",
]
