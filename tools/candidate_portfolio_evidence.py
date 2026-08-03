"""WP4 W4-1 候选组合/执行证据驱动器 → data/cache/candidate_portfolio_evidence.json
(2026-08-02,设计 docs/engineering/specs/2026-08-02-wp4-portfolio-execution-gate-design.md)。

把五个组合/执行维度做成 per 候选的承重证据行,供 W4-2 接进
tools/alpha_production_workflow.py 的 RESEARCH_RISK 门(READY 需要九引擎包 READY ∧
本 artifact 行存在且非容忍 missing 为空;阈值判定 = W4-3 独立预注册评审,本切片只看
PRESENCE/完备性,绝不预断任何候选)。

数据源(全部既有本地权威,绝不重算信号):
- 候选曲线:dry_run.db 正典 run(canonical_runs 每 strategy_id 取最后一条,与
  console「latest」同语义;raw equity_curve,与 common_risk 主口径一致)。
  人口 = legacy 三条(carry_oi_majors/crowd_contra/top_contra,既是 incumbent 也有
  自己的行)+ challenger(dry_run_candidate_multi.candidate_ids 的 registry 权威
  校验人口)。**db strategy_id 映射:carry_oi_majors → "carry"**(common_risk BOOK
  同口径),其余恒等。challenger 无正典 run(部分 worktree 未跑权威回测)→
  MISSING_RUN 诚实行,绝不合成。
- incumbent book:候选之外、有正典 run 的 book 成员(legacy + challenger),ISO 周
  对齐(factor_decomp.align_weekly)**等权**合成——等权是刻意选择(权重估计本身
  会吃掉样本;common_risk 的逆波动口径另属簇级组合),honesty 块如实标注。
- 因子库:复用 tools/common_risk_research 的构造器(weekly_price_agg/weekly_funding/
  weekly_oi/build_factor_library/common_grid,§7.1 10 因子,同一份本地数据),
  不消费其 artifact(那是 4 书 192 周固定网格的分解结果,不是可复用因子序列)。
- 容量:capacity_snapshot.json 既有容量带**按引用携带**(UNCALIBRATED_PRINCIPLED;
  legacy 三条有带,challenger 无 ADV 带 → MISSING,绝不编造)。
- 拥挤:各候选正典 run detail 的 crowding_gate 汇总(n_periods/n_block;逐期 z
  未持久化 → max_z None 诚实)。
- TCA:paper_multivenue_sor_status.json(book 级 canary)+ 其 run_id 在 dry_run.db
  里的 route_tca 记录(名义加权短点);记录取不到 → 短点 None,available 仍按
  状态文档判定。盘口深度历史缺口 → 显式 MISSING,绝不合成。

幂等:纯函数于上述存储,除 artifact 外零副作用(重跑覆写同一文件)。
无网络、无真单/真钱/真凭证;墙钟仅在 main 边界。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Direct script execution is a supported operator path; bootstrap the repo
# before importing the package trees (same idiom as dry_run_candidate_multi).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.data.caching_store import CachingPITStore
from services.data.persistent_store import SqlitePITStore
from services.execution.dry_run_store import DryRunStore
from services.research import factor_decomp as fd
from services.research import portfolio_evidence as pe
from tools.cache_io import atomic_write_json
from tools.common_risk_research import (
    FACTOR_UNIVERSE,
    build_factor_library,
    canonical_runs,
    curve_series,
    weekly_funding,
    weekly_oi,
    weekly_price_agg,
)
from tools.dry_run_candidate_multi import candidate_ids as challenger_ids

_CACHE = os.path.join("data", "cache")
_DRY_RUN_DB = os.path.join(_CACHE, "dry_run.db")
_CAPACITY = os.path.join(_CACHE, "capacity_snapshot.json")
_SOR_STATUS = os.path.join(_CACHE, "paper_multivenue_sor_status.json")
_PRICE_DB = os.path.join(_CACHE, "crypto_funding.db")
_FUNDING_JSON = os.path.join(_CACHE, "crypto_funding_rates.json")
_OI_JSON = os.path.join(_CACHE, "crypto_oi_metrics.json")
_OUT = os.path.join(_CACHE, "candidate_portfolio_evidence.json")

POLICY_VERSION = "portfolio_evidence.v1"
FLOW = "CANDIDATE_PORTFOLIO_EVIDENCE"

# legacy 三条:workflow 候选 id → dry_run.db strategy_id(carry 的官方驱动器以
# 短名持久化,与 common_risk_research.BOOK 同口径)。
LEGACY_BOOK = (
    ("carry_oi_majors", "carry"),
    ("crowd_contra", "crowd_contra"),
    ("top_contra", "top_contra"),
)
# capacity_snapshot.json 的 books 键(容量带按引用携带;challenger 无带 → MISSING)。
_CAPACITY_BOOK_KEY = {
    "carry_oi_majors": "carry",
    "crowd_contra": "crowd_contra",
    "top_contra": "top_contra",
}


def default_population() -> tuple[str, ...]:
    """默认人口:legacy 三条 + challenger(registry 权威校验,universe 顺序)。

    challenger_ids 在 workflow/registry 权威无效时 raise —— fail-closed,
    绝不静默换人口。"""
    legacy = tuple(candidate for candidate, _ in LEGACY_BOOK)
    return legacy + tuple(cid for cid in challenger_ids() if cid not in legacy)


def _load_json_soft(path: str) -> dict:
    """缺失/不可读 → {}(该源整体缺席 → 对应子证据全 MISSING,不崩驱动器)。"""
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _default_factor_maps(
    price_db_path: str = _PRICE_DB,
    funding_json_path: str = _FUNDING_JSON,
    oi_json_path: str = _OI_JSON,
) -> dict | None:
    """复用 common_risk 构造器建 §7.1 因子库 {name: {week: value}}。

    任一数据源缺席 → None(所有候选 common_exposure MISSING,诚实;绝不退化成
    半套因子库假装完整)。"""
    if not (
        os.path.isfile(price_db_path)
        and os.path.isfile(funding_json_path)
        and os.path.isfile(oi_json_path)
    ):
        return None
    funding_json = _load_json_soft(funding_json_path)
    oi_doc = _load_json_soft(oi_json_path)
    oi_json = oi_doc.get("data")
    if not funding_json or not isinstance(oi_json, dict):
        return None
    store_px = CachingPITStore(SqlitePITStore(price_db_path, create=False))
    price_agg, funding_w, oi_w = {}, {}, {}
    for coin in FACTOR_UNIVERSE:
        bars = store_px.fetch_bars_pit(coin, 0, 10**18)  # PIT 安全批量读(非裸 fetch_bars)
        if not bars or coin not in funding_json or coin not in oi_json:
            continue
        closes_by_day = {int(b.event_time): float(b.close) for b in bars}
        price_agg[coin] = weekly_price_agg(bars)
        funding_w[coin] = weekly_funding(funding_json[coin])
        oi_w[coin] = weekly_oi(oi_json[coin], closes_by_day)
    if "BTC" not in price_agg or "ETH" not in price_agg:
        return None
    master_weeks = sorted({w for agg in price_agg.values() for w in agg})
    if not master_weeks:
        return None
    return build_factor_library(master_weeks, price_agg, funding_w, oi_w)


def _crowding_rows(gate: object) -> list[dict] | None:
    """干跑 detail 的 crowding_gate 汇总 → 决策行(计数精确保留;逐期 z 未持久化
    → z=None,max_z 诚实 None,绝不从 sample_reasons 文本猜上界)。"""
    if not isinstance(gate, dict):
        return None
    n_periods, n_block = gate.get("n_periods"), gate.get("n_block")
    if (
        isinstance(n_periods, bool)
        or isinstance(n_block, bool)
        or not isinstance(n_periods, int)
        or not isinstance(n_block, int)
        or n_periods < 0
        or n_block < 0
        or n_block > n_periods
    ):
        return None
    return (
        [{"blocked": True, "z": None}] * n_block
        + [{"blocked": False, "z": None}] * (n_periods - n_block)
    )


def build_portfolio_evidence(
    *,
    db_path: str = _DRY_RUN_DB,
    output_path: str = _OUT,
    capacity_path: str = _CAPACITY,
    sor_status_path: str = _SOR_STATUS,
    now_ms: int | None = None,
    population_fn=default_population,
    factor_builder=_default_factor_maps,
) -> dict:
    """Build (and atomically publish) the per-candidate portfolio evidence artifact."""
    generated_at = int(now_ms if now_ms is not None else time.time() * 1000)
    population = tuple(population_fn())
    if not os.path.isfile(db_path):
        raise RuntimeError(f"dry-run store missing: {db_path}(incumbent book 无源,fail-closed)")
    store = DryRunStore(db_path, create=False)
    try:
        canonical = canonical_runs(store.all_runs())
        # book 成员 = 人口中有正典 run 者(legacy + challenger);db strategy_id 映射。
        book: dict[str, dict] = {}
        for candidate in population:
            sid = dict(LEGACY_BOOK).get(candidate, candidate)
            record = canonical.get(sid)
            if record is not None:
                book[candidate] = record
        # TCA:状态文档 + 其 run_id 的 route_tca 记录(取不到 → None,短点留白)。
        sor_doc = _load_json_soft(sor_status_path)
        route_records = None
        sor_run_id = sor_doc.get("run_id") if sor_doc else None
        if isinstance(sor_run_id, str) and sor_run_id:
            sor_run = store.get_run(sor_run_id)
            detail = (sor_run or {}).get("detail") if isinstance(sor_run, dict) else None
            records = (detail or {}).get("route_tca") if isinstance(detail, dict) else None
            if isinstance(records, list) and records:
                route_records = records
    finally:
        store.close()

    capacity_doc = _load_json_soft(capacity_path)
    capacity_books = capacity_doc.get("books") if isinstance(capacity_doc.get("books"), dict) else {}
    capacity_provenance = capacity_doc.get("provenance") if capacity_doc else None
    factor_maps = factor_builder() if factor_builder is not None else None

    rows: dict[str, dict] = {}
    for candidate in population:
        record = book.get(candidate)
        if record is None:
            rows[candidate] = pe.build_candidate_portfolio_evidence(candidate)
            continue
        # 策略网格:候选 + 其余 book 成员 N 路 ISO 周对齐。
        peers = {other: rec for other, rec in book.items() if other != candidate}
        series = {cid: curve_series(rec, "equity_curve") for cid, rec in
                  [(candidate, record), *peers.items()]}
        aligned = fd.align_weekly(series)
        weeks = aligned["weeks"]
        aligned_data = aligned["data"]
        candidate_rets = aligned_data[candidate]
        incumbent_rets = {cid: aligned_data[cid] for cid in aligned_data if cid != candidate}
        # 暴露网格 = 策略共同周 ∩ 全因子有效周(因子缺席 → common_exposure 输入 None)。
        factor_grid = None
        if factor_maps is not None and weeks:
            grid = set(weeks)
            for mapping in factor_maps.values():
                grid &= {w for w, v in mapping.items() if v is not None}
            grid = sorted(grid)
            idx = {w: i for i, w in enumerate(weeks)}
            factor_grid = {
                "candidate": [candidate_rets[idx[w]] for w in grid],
                "incumbents": {cid: [rets[idx[w]] for w in grid]
                               for cid, rets in incumbent_rets.items()},
                "factors": {name: [mapping[w] for w in grid]
                            for name, mapping in factor_maps.items()},
            }
        detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
        capacity_key = _CAPACITY_BOOK_KEY.get(candidate)
        capacity_band = capacity_books.get(capacity_key) if capacity_key else None
        # 纯核要求 common_exposure 的全部序列同一网格;边际贡献用策略网格,
        # 共同暴露用暴露网格(两者可能不同长),故分两步:聚合器先不收因子,
        # 暴露块在此按暴露网格单独计算后合并(INPUT_MISSING → 暴露网格计算)。
        rows[candidate] = pe.build_candidate_portfolio_evidence(
            candidate,
            candidate_returns=candidate_rets,
            incumbent_returns_by_id=incumbent_rets,
            factor_returns_by_name=None,
            capacity_band=capacity_band,
            capacity_provenance=(
                capacity_provenance if capacity_band is not None else None
            ),
            crowding_rows=_crowding_rows(detail.get("crowding_gate")),
            sor_status_document=sor_doc or None,
            route_tca_records=route_records,
        )
        rows[candidate]["dry_run"] = {
            "run_id": record.get("run_id"),
            "db_strategy_id": record.get("strategy_id"),
            "curve": "equity_curve",
            "n_periods": len(candidate_rets),
        }
        if factor_grid is not None:
            exposure = pe.common_exposure(
                factor_grid["candidate"],
                factor_grid["factors"],
                factor_grid["incumbents"],
            )
            rows[candidate]["common_exposure"] = exposure
            missing = list(rows[candidate]["missing"])
            if exposure.get("available"):
                missing = [name for name in missing if name != "common_exposure"]
            elif "common_exposure" not in missing:
                missing.append("common_exposure")
            rows[candidate]["missing"] = sorted(missing)
            rows[candidate]["status"] = "INCOMPLETE" if missing else "OK"

    payload = {
        "schema_version": 1,
        "flow": FLOW,
        "policy_version": POLICY_VERSION,
        "provenance": "REAL_CANDIDATE_PORTFOLIO_EVIDENCE",
        "readiness": "L2_DRY_RUN_ONLY",
        "generated_at_ms": generated_at,
        "population": {
            "source": "legacy3+dry_run_candidate_multi.candidate_ids(registry 权威校验)",
            "candidate_ids": list(population),
        },
        "incumbent_book": {
            "members_with_runs": sorted(book),
            "combine": "EQUAL_WEIGHT(刻意:权重估计吃样本;逆波动口径另属 common_risk 簇级组合)",
            "curve": "equity_curve(raw,与 common_risk 主口径一致)",
            "align": "ISO 年-周 N 路对齐(factor_decomp.align_weekly)",
            "excluded": "btc_spot_trend/ashare_*/paper_sor_canary 等非 workflow 候选的 db 策略不进 book",
        },
        "factor_library": {
            "available": factor_maps is not None,
            "built": sorted(factor_maps) if factor_maps is not None else [],
            "source": "tools/common_risk_research 构造器(§7.1,同一批本地数据)",
        },
        "alphas": rows,
        "missing_inventory": {cid: row["missing"] for cid, row in rows.items() if row["missing"]},
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credential_access": False,
            "broker_order_submission": False,
        },
        "honesty": {
            "note": (
                "本切片只产证据与完备性判定(PRESENCE),阈值(marginal IR 下界/共同暴露"
                "上界/容量下界/拥挤 BLOCK 占比上界/TCA 带上界)= W4-3 独立预注册评审,"
                "全部 UNCALIBRATED。诊断非门禁的既有结论一字未动;本 artifact 只经 W4-2 "
                "接 RESEARCH_RISK 门,不驱动任何自动晋级/下单。"
            ),
            "capacity": "legacy 三条容量带按引用携带(capacity_snapshot.json);challenger 无 ADV 带 → MISSING,绝不编造。",
            "crowding": "逐期 z 未持久化 → max_z None;计数来自正典 run detail 的 crowding_gate 汇总。",
            "tca": "book 级多场所 SOR canary 引用(simulated、费率 UNCALIBRATED);非 per 候选实测;盘口深度历史缺口显式 MISSING。",
            "min_common_periods": pe.MIN_COMMON_PERIODS,
        },
    }
    atomic_write_json(output_path, payload, indent=2)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=_DRY_RUN_DB, help="dry-run store path")
    parser.add_argument("--output", default=_OUT, help="evidence artifact path")
    parser.add_argument("--capacity", default=_CAPACITY, help="capacity snapshot path")
    parser.add_argument("--sor-status", default=_SOR_STATUS, help="multi-venue SOR status path")
    args = parser.parse_args(argv)
    payload = build_portfolio_evidence(
        db_path=args.db,
        output_path=args.output,
        capacity_path=args.capacity,
        sor_status_path=args.sor_status,
    )
    for cid, row in payload["alphas"].items():
        print(f"  {cid:24s} {row['status']:12s} missing={row['missing']}", flush=True)
    n_ok = sum(1 for row in payload["alphas"].values() if row["status"] == "OK")
    print(
        f"=== 候选组合证据: {len(payload['alphas'])} 候选 · OK {n_ok} · "
        f"book {len(payload['incumbent_book']['members_with_runs'])} 成员 -> {args.output} ===",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FLOW",
    "LEGACY_BOOK",
    "POLICY_VERSION",
    "build_portfolio_evidence",
    "default_population",
    "main",
]
