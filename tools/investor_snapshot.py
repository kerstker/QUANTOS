"""投资者/审计证据快照驱动器(W9 vertical slice,规格 §13,2026-07-25)。

从**账本与已发布证据 artifact** 机械装配投资者/审计快照(绝不手工可编辑),落
`evidence/investor_snapshot.json` —— 由只读 console 端点
`/api/v1/console/investor-snapshot`(services/console/api/investor_snapshot_views.py)原样
serve。全部计算在纯核 `live/execution/investor_snapshot.py`;本模块只做 IO 边界装配。

数据源(全部只读;sqlite 一律 `?mode=ro`,绝不开写连接,绝不写任何 live store/凭证):
- `data/cache/live_track{suffix}.db` — live 账本 orders/fills/TCA(全策略)。
- `data/cache/live_equity{suffix}.jsonl` — 账户级权益采样(日频 NAV 的源)。
- `data/cache/live_track_status{suffix}.json` — 最后一次驱动器批次(账户级对账块)。
- `data/cache/live_kill_switch{suffix}.{db,json}` — kill-switch 权威转移链(优先)/
  legacy JSON 镜像(provenance 如实标;权威库不可读 → 按仓库惯例 fail-closed 读作 tripped
  并标 UNREADABLE_FAIL_CLOSED)。
- `evidence/live_degradation.json`(W1)、`evidence/go_live_readiness.json`、
  `evidence/alpha_lifecycle_cockpit.json` — 已发布 artifact,原样消费。

诚实边界:
- venue 无账本活动(orders/fills 全空)→ `ABSENT_NO_ACTIVITY`(binance_mainnet 在首单前
  恒为此态)——ABSENT 是诚实状态,绝不合成空指标。
- live 账本不记录 venue 出入金 → 全部 venue `external_flows=None`,NAV/风险在 raw 权益
  序列上计算并标 `RAW_FLOWS_UNRECORDED`(见纯核 docstring 的现金流调整定义)。
- fee_actual/funding_actual 账本不记录 → 恒 UNKNOWN;策略版本/代码哈希无 artifact 钉 →
  恒 UNKNOWN。任何不可导出字段 → None + 点名 `unknown_fields`,绝不估算。
- §13.3 双组合视图(Showcase/Growth)今天只有一条 live 轨道 → 两视图 ABSENT + reason。
- 墙钟只在此边界(generated_at_ms),**绝不进哈希**——同一数据截止点重跑必得同一
  report_snapshot_hash(幂等可复核)。L2_DRY_RUN_ONLY 不变。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from live.execution import investor_snapshot as inv
from services.risk.pre_trade.kill_switch_store import kill_switch_authority_path
from tools.cache_io import atomic_write_json
from tools.live_track_advance import _VENUES, _venue_paths

OUTPUT = os.path.join("evidence", "investor_snapshot.json")
_DEGRADATION = os.path.join("evidence", "live_degradation.json")
_GO_LIVE = os.path.join("evidence", "go_live_readiness.json")
_COCKPIT = os.path.join("evidence", "alpha_lifecycle_cockpit.json")


# ---------------------------------------------------------------------------
# 只读装载(全部 fail-soft;sqlite 一律 ?mode=ro)
# ---------------------------------------------------------------------------

def _open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_ledger_rows(ledger_path: str) -> Tuple[List[dict], List[dict], List[dict]]:
    """(orders, fills, tca) 全策略;库/表缺失 -> ([], [], [])(该 venue 无活动)。"""
    if not os.path.exists(ledger_path) or os.path.getsize(ledger_path) == 0:
        return [], [], []
    try:
        conn = _open_ro(ledger_path)
    except sqlite3.Error:
        return [], [], []
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "live_orders" not in tables or "live_fills" not in tables:
            return [], [], []
        orders = [dict(r) for r in conn.execute(
            "SELECT * FROM live_orders ORDER BY submitted_at")]
        fills = [dict(r) for r in conn.execute(
            "SELECT * FROM live_fills ORDER BY filled_at")]
        tca: List[dict] = []
        if "live_tca" in tables:
            tca = [dict(r) for r in conn.execute(
                "SELECT * FROM live_tca ORDER BY recorded_at")]
        return orders, fills, tca
    except sqlite3.Error:
        return [], [], []
    finally:
        conn.close()


def load_equity_samples(equity_path: str) -> List[dict]:
    """live_equity*.jsonl(append-only,逐行自描述);缺文件/坏行 fail-soft。"""
    if not os.path.exists(equity_path):
        return []
    out: List[dict] = []
    try:
        with open(equity_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (isinstance(rec, dict) and rec.get("ts") is not None
                        and isinstance(rec.get("equity"), (int, float))):
                    out.append({"ts": int(rec["ts"]), "equity": float(rec["equity"])})
    except OSError:
        return []
    return out


def load_status(status_path: str) -> Optional[dict]:
    if not os.path.exists(status_path):
        return None
    try:
        with open(status_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_kill_switch(json_path: str) -> Optional[dict]:
    """kill-switch 记录:权威 SQLite 转移链优先,legacy JSON 镜像兜底(如实标 provenance)。

    权威库存在但不可读 -> 按仓库惯例 corrupt authority fail-closed 读作 tripped,并标
    UNREADABLE_FAIL_CLOSED(透明,不静默)。两者皆无 -> None(该 venue 从未初始化)。"""
    db_path = kill_switch_authority_path(json_path)
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        try:
            conn = _open_ro(db_path)
            try:
                tables = {r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "kill_switch_transitions" not in tables:
                    raise sqlite3.Error("missing kill_switch_transitions")
                transitions: List[dict] = []
                tripped: Optional[bool] = None
                incident_id: Optional[str] = None
                for row in conn.execute(
                        "SELECT action, recorded_at, state_json, transition_id"
                        " FROM kill_switch_transitions ORDER BY transition_seq"):
                    try:
                        state = json.loads(row["state_json"])
                    except json.JSONDecodeError:
                        state = {}
                    tripped = bool(state.get("tripped"))
                    incident_id = state.get("incident_id") or incident_id
                    transitions.append({
                        "action": row["action"],
                        "recorded_at": int(row["recorded_at"]),
                        "transition_id": row["transition_id"],
                        "incident_id": state.get("incident_id"),
                        "reason": state.get("reason"),
                        "tripped": state.get("tripped"),
                    })
                return {"provenance": "KILL_SWITCH_AUTHORITY_DB",
                        "tripped": tripped, "incident_id": incident_id,
                        "transitions": transitions}
            finally:
                conn.close()
        except sqlite3.Error:
            return {"provenance": "KILL_SWITCH_AUTHORITY_DB_UNREADABLE_FAIL_CLOSED",
                    "tripped": True, "incident_id": "corrupt-state-fail-closed",
                    "transitions": []}
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, json.JSONDecodeError):
            state = None
        if isinstance(state, dict):
            transitions = []
            if int(state.get("armed_at") or 0) > 0:
                transitions.append({"action": "ARM",
                                    "recorded_at": int(state["armed_at"]),
                                    "transition_id": None,
                                    "incident_id": state.get("incident_id"),
                                    "reason": state.get("reason"),
                                    "tripped": True})
            if int(state.get("reset_at") or 0) > 0:
                transitions.append({"action": "RESET",
                                    "recorded_at": int(state["reset_at"]),
                                    "transition_id": None,
                                    "incident_id": state.get("incident_id"),
                                    "reason": None,
                                    "tripped": False})
            return {"provenance": "LEGACY_JSON_MIRROR",
                    "tripped": bool(state.get("tripped")),
                    "incident_id": state.get("incident_id"),
                    "transitions": transitions}
    return None


def load_json_artifact(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# artifact 装配
# ---------------------------------------------------------------------------

def build_snapshot(*, now_ms: int, venues: Optional[Sequence[str]] = None,
                   venues_registry: Optional[Mapping] = None,
                   venue_paths_fn=None,
                   degradation_path: str = _DEGRADATION,
                   go_live_path: str = _GO_LIVE,
                   cockpit_path: str = _COCKPIT,
                   external_flows_by_venue: Optional[Mapping[str, Sequence[Mapping]]] = None
                   ) -> Dict[str, Any]:
    """装配完整 artifact(全 hermetic 参数化,测试注入 tmp 路径/假注册表/合成 artifact)。"""
    registry = venues_registry or _VENUES
    paths_fn = venue_paths_fn or _venue_paths
    degradation = load_json_artifact(degradation_path) or {}
    go_live = load_json_artifact(go_live_path) or {}
    cockpit = load_json_artifact(cockpit_path) or {}
    reports = [r for r in (degradation.get("reports") or []) if isinstance(r, dict)]
    go_live_venues = {str(v["venue"]): v for v in (go_live.get("venues") or [])
                      if isinstance(v, dict) and v.get("venue")}
    guidance = go_live.get("guidance") or {}
    alphas = [a for a in (cockpit.get("alphas") or []) if isinstance(a, dict)]

    venue_inputs: List[dict] = []
    for venue in (venues or sorted(registry)):
        cfg = registry.get(venue)
        if cfg is None:
            continue
        paths = paths_fn(cfg["suffix"])
        orders, fills, tca = load_ledger_rows(paths["ledger"])
        glv = go_live_venues.get(venue) or {}
        capacity_safety = None
        if isinstance(guidance, Mapping) and guidance.get("venue") == venue:
            cs = guidance.get("capacity_safety")
            capacity_safety = dict(cs) if isinstance(cs, Mapping) else None
        venue_inputs.append({
            "venue": venue,
            "mainnet": bool(cfg.get("mainnet_only") or cfg.get("spot")),
            "spot": bool(cfg.get("spot")),
            "orders": orders,
            "fills": fills,
            "tca": tca,
            "equity_samples": load_equity_samples(paths["equity"]),
            "external_flows": (None if external_flows_by_venue is None
                               else external_flows_by_venue.get(venue)),
            "status": load_status(paths["status"]),
            "kill_switch": load_kill_switch(paths["kill_switch"]),
            "mandate": glv.get("mandate"),
            "attestations": glv.get("attestations") or {},
            "capacity_safety": capacity_safety,
            "degradation_reports": [r for r in reports if r.get("venue") == venue],
            "lifecycle_alphas": alphas,
        })
    source_generated = [a.get("generated_at_ms")
                        for a in (degradation, go_live, cockpit)
                        if isinstance(a.get("generated_at_ms"), int)]
    return inv.build_snapshot(now_ms=now_ms, venues=venue_inputs,
                              source_generated_at=source_generated)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="投资者/审计证据快照驱动器(只读 live 账本/证据 artifact,无网络)")
    parser.add_argument("--venue", choices=sorted(_VENUES), default=None,
                        help="只跑一个 venue(默认全部注册 venue)")
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args(argv)

    artifact = build_snapshot(now_ms=int(time.time() * 1000),
                              venues=[args.venue] if args.venue else None)
    atomic_write_json(args.out, artifact, indent=2)
    s = artifact["summary"]
    print(f"data_cutoff_ms={artifact['data_cutoff_ms']} "
          f"hash={artifact['report_snapshot_hash'][:16]}…", flush=True)
    for b in artifact["venues"]:
        if b["status"] != "ACTIVE":
            print(f"[{b['venue']}] {b['status']}", flush=True)
            continue
        nav = b["nav"] or {}
        risk = b["risk"] or {}
        rec = b["reconciliation"] or {}
        dd = risk.get("max_drawdown_pct")
        print(f"[{b['venue']}] nav={nav.get('last_nav')} ({nav.get('basis')}) "
              f"days={nav.get('n_days')} maxDD={'None' if dd is None else f'{dd:.4%}'} "
              f"rec_in_sync={rec.get('in_sync')} "
              f"tripped={(b['incidents'] or {}).get('tripped')}", flush=True)
        for tl in b["three_line"]:
            print(f"  {tl['strategy_id']}: live_net={tl['live']['net']} "
                  f"replay_net={None if not tl.get('replay') else tl['replay']['net']} "
                  f"ratio={tl['live_degradation_ratio']}", flush=True)
    print(f"unknown_fields={len(artifact['unknown_fields'])} "
          f"views={s['portfolio_views_status']} -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
