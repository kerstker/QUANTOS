"""Read-only MEXC (zero-fee BTC spot) integration console routes.

Three surfaces, all BACKEND_COMPUTED_SNAPSHOT + honest provenance, failing
closed to an explicit synthetic/absent marker when the underlying artifact has
not been produced yet:

- ``/api/v1/console/mexc-market`` — BTC market snapshot built from
  ``data/cache/mexc_btc_klines.json`` and/or the PIT ``crypto_mexc.db``.
- ``/api/v1/console/mexc-strategy`` — the ``btc_spot_trend`` dry-run results
  from ``dry_run.db`` (equity curve, sharpe, drawdown, turnover) plus a
  buy-and-hold benchmark computed from the same market data.
- ``/api/v1/console/mexc-readiness`` — a live-readiness checklist. Credential,
  mandate and attestation state is deliberately NOT inspected by this console;
  the kill-switch state is read from a stable private snapshot (fail-closed).

This module never touches the network, never reads credentials, and never
grants any live capability.  L2_DRY_RUN_ONLY.
"""

from __future__ import annotations

import math
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import APIRouter

from quantos.runtime_paths import runtime_state_root
from services.console.api.common import with_snapshot_metadata
from services.console.api.sqlite_snapshot import (
    SqliteSnapshotError,
    stable_sqlite_snapshot,
)
from services.console.api.state_artifact import INVALID, VALID, read_json_artifact
from services.execution.dry_run_store import DryRunStore
from services.governance.json_integrity import load_json_without_duplicate_keys


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_ROOT = runtime_state_root(cwd=_REPO_ROOT)
_MEXC_KLINES_JSON = os.path.join(_STATE_ROOT, "data", "cache", "mexc_btc_klines.json")
_MEXC_DB = os.path.join(_STATE_ROOT, "data", "cache", "crypto_mexc.db")
_DRY_RUN_DB = os.path.join(_STATE_ROOT, "data", "cache", "dry_run.db")
_MEXC_ADAPTER = os.path.join(_REPO_ROOT, "live", "exchange", "mexc_spot.py")
_MEXC_SMOKE_EVIDENCE = os.path.join(_STATE_ROOT, "evidence", "mexc_mainnet_smoke.json")
_LIVE_KILL_SWITCH_STATE = os.path.join(_STATE_ROOT, "data", "cache", "live_kill_switch.json")
_GOVERNANCE_TEST_DIR = os.path.join(_REPO_ROOT, "tests", "governance")
_MEXC_STRATEGY_ID = "btc_spot_trend"
_FRESHNESS_LIMIT_HOURS = 72.0


# ---------------------------------------------------------------------------
# SQLite authorities are copied to private scratch before opening. This keeps
# every GET physically read-only even when the producer has a committed WAL.
# ---------------------------------------------------------------------------


def _path_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _is_reparse_point(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _inspect_sqlite_components(path: Path) -> tuple[tuple[str, tuple[int, int, int, int, int]], ...]:
    """Bind every state-root component without following symlinks/reparse points."""
    trusted_root = Path(os.path.abspath(os.fspath(_STATE_ROOT)))
    try:
        root_metadata = trusted_root.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise SqliteSnapshotError("QUANTOS_STATE_ROOT is unavailable") from exc
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or _is_reparse_point(root_metadata)
    ):
        raise SqliteSnapshotError("QUANTOS_STATE_ROOT must be a trusted directory")
    target = Path(os.path.abspath(path))
    try:
        relative = target.relative_to(trusted_root)
    except ValueError as exc:
        raise SqliteSnapshotError("SQLite authority is outside QUANTOS_STATE_ROOT") from exc
    if not relative.parts:
        raise SqliteSnapshotError("SQLite authority path cannot equal state root")
    observed = [(".", _path_identity(root_metadata))]
    current = trusted_root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return tuple(observed)
        except OSError as exc:
            raise SqliteSnapshotError("SQLite authority component is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
            raise SqliteSnapshotError("SQLite authority component is a link/reparse point")
        final = index == len(relative.parts) - 1
        if (final and not stat.S_ISREG(metadata.st_mode)) or (
            not final and not stat.S_ISDIR(metadata.st_mode)
        ):
            raise SqliteSnapshotError("SQLite authority component has invalid type")
        observed.append((part, _path_identity(metadata)))
    return tuple(observed)


@contextmanager
def _sqlite_copy(path: str) -> Iterator[Optional[str]]:
    source = Path(path)
    before = _inspect_sqlite_components(source)
    if not before or before[-1][0] != source.name:
        try:
            yield None
        finally:
            final = _inspect_sqlite_components(source)
            if final != before or (final and final[-1][0] == source.name):
                raise SqliteSnapshotError(
                    "SQLite authority appeared or its parent identity changed"
                )
        return
    with stable_sqlite_snapshot(source) as snapshot:
        after = _inspect_sqlite_components(source)
        if after != before:
            raise SqliteSnapshotError("SQLite authority path identity changed")
        try:
            yield str(snapshot)
        finally:
            final = _inspect_sqlite_components(source)
            if final != before:
                raise SqliteSnapshotError("SQLite authority path identity changed")


# ---------------------------------------------------------------------------
# market data helpers (tolerant parsers: the fetch driver owns the exact
# artifact schema, this view degrades honestly instead of crashing)
# ---------------------------------------------------------------------------

def _to_seconds(ts: Any) -> Optional[float]:
    """Normalize a timestamp that may be seconds or milliseconds."""
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    if value > 1e11:  # milliseconds
        value = value / 1000.0
    return value


def _bar_from_row(row: Any) -> Optional[Dict[str, float]]:
    """Extract (t_seconds, close) from a MEXC array row or a dict bar."""
    if isinstance(row, (list, tuple)) and len(row) >= 5:
        t = _to_seconds(row[0])
        try:
            close = float(row[4])
        except (TypeError, ValueError):
            return None
        if t is None or not math.isfinite(close) or close <= 0:
            return None
        return {"t": t, "close": close}
    if isinstance(row, dict):
        t = _to_seconds(
            row.get("t", row.get("open_time", row.get("openTime", row.get("event_time"))))
        )
        close_raw = row.get("close", row.get("c"))
        try:
            close = float(close_raw)
        except (TypeError, ValueError):
            return None
        if t is None or not math.isfinite(close) or close <= 0:
            return None
        return {"t": t, "close": close}
    return None


def _bars_from_klines_json(path: str) -> Dict[str, Any]:
    """Return {'bars': [...], 'fetched_at': float|None} or {'bars': []}."""
    status, snap = read_json_artifact(path, trusted_root=_STATE_ROOT)
    if status != VALID:
        return {"status": status, "bars": [], "fetched_at": None}
    fetched_at = None
    raw: Any = snap
    if isinstance(snap, dict):
        fetched_at = _to_seconds(
            snap.get(
                "fetched_at_ms",
                snap.get("fetched_at", snap.get("generated_at", snap.get("observed_at"))),
            )
        )
        for key in ("klines", "rows", "bars", "data"):
            if isinstance(snap.get(key), list):
                raw = snap[key]
                break
        else:
            raw = []
    if not isinstance(raw, list):
        raw = []
    bars = [b for b in (_bar_from_row(r) for r in raw) if b is not None]
    bars.sort(key=lambda b: b["t"])
    return {"status": VALID, "bars": bars, "fetched_at": fetched_at}


def _pit_rows_from_snapshot(snapshot: str) -> list[tuple[Any, Any]]:
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT b.event_time,b.payload_json FROM bars b "
            "JOIN (SELECT event_time,MAX(knowledge_time) AS knowledge_time "
            "FROM bars WHERE key='BTC' GROUP BY event_time) latest "
            "ON latest.event_time=b.event_time "
            "AND latest.knowledge_time=b.knowledge_time WHERE b.key='BTC' "
            "ORDER BY b.event_time"
        ).fetchall()
    finally:
        conn.close()


def _parse_pit_rows(rows: list[tuple[Any, Any]]) -> tuple[List[Dict[str, float]], List[Dict[str, Any]]]:
    closes: List[Dict[str, float]] = []
    candles: List[Dict[str, Any]] = []
    for event_time, payload_json in rows:
        try:
            payload = load_json_without_duplicate_keys(payload_json)
            if not isinstance(payload, dict):
                raise TypeError("bar payload must be an object")
            candle: Dict[str, Any] = {
                "t": float(event_time) / 1000.0,
                "open": float(payload["open"]),
                "high": float(payload["high"]),
                "low": float(payload["low"]),
                "close": float(payload["close"]),
            }
            if not all(
                math.isfinite(candle[key]) for key in ("t", "open", "high", "low", "close")
            ) or candle["open"] <= 0 or candle["close"] <= 0:
                raise ValueError("bar contains an invalid numeric value")
            volume = payload.get("volume")
            if (
                isinstance(volume, (int, float))
                and not isinstance(volume, bool)
                and math.isfinite(float(volume))
                and volume > 0
            ):
                candle["volume"] = float(volume)
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("authoritative MEXC PIT row is invalid") from exc
        closes.append({"t": float(event_time) / 1000.0, "close": candle["close"]})
        candles.append(candle)
    return closes, candles


def _market_from_pit_db(path: str) -> Dict[str, Any]:
    """Read all market projections from exactly one stable SQLite cut."""
    try:
        with _sqlite_copy(path) as snapshot:
            if snapshot is None:
                return {
                    "status": "MISSING",
                    "present": False,
                    "bars": [],
                    "candles": [],
                }
            rows = _pit_rows_from_snapshot(snapshot)
            bars, candles = _parse_pit_rows(rows)
    except SqliteSnapshotError:
        return {"status": "UNAVAILABLE", "present": True, "bars": [], "candles": []}
    except Exception:
        return {
            "status": "CORRUPT_OR_UNREADABLE",
            "present": True,
            "bars": [],
            "candles": [],
        }
    return {
        "status": "REAL",
        "present": True,
        "bars": bars,
        "candles": candles,
    }


def _bars_from_pit_db(path: str) -> Dict[str, Any]:
    result = _market_from_pit_db(path)
    return {key: result[key] for key in ("status", "present", "bars")}


_MARKET_BARS_LIMIT = 120


def _ohlcv_bars_from_pit_db(path: str, limit: int = _MARKET_BARS_LIMIT) -> List[Dict[str, Any]]:
    """PIT sqlite 里 BTC 的最近 `limit` 根日线 OHLCV(供 CandleChart;t=**秒**,字段名镜像
    前端 `MexcMarketPayload.bars` / `Candle` 契约:open/high/low/close 必填、volume 可选)。
    库缺/读不出/行形状坏 → 诚实空列表(页面在无 bars 时本就诚实降级,绝不硬凑)。"""
    return _market_from_pit_db(path)["candles"][-int(limit):]


def _ann_volatility(closes: List[float], periods_per_year: float = 365.0) -> Optional[float]:
    if len(closes) < 3:
        return None
    rets = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(periods_per_year), 6)


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _mexc_market_payload() -> Dict[str, Any]:
    json_side = _bars_from_klines_json(_MEXC_KLINES_JSON)
    db_side = _market_from_pit_db(_MEXC_DB)
    bars = json_side["bars"] or db_side["bars"]
    present = bool(bars)
    closes = [b["close"] for b in bars]
    latest = closes[-1] if closes else None
    prev = closes[-2] if len(closes) >= 2 else None
    change_24h_pct = (
        round((latest / prev - 1.0) * 100.0, 4)
        if latest is not None and prev
        else None
    )
    vol_30d = _ann_volatility(closes[-31:]) if len(closes) >= 3 else None
    fetched_at = json_side["fetched_at"]
    if fetched_at is None and db_side["bars"]:
        fetched_at = db_side["bars"][-1]["t"]
    age_hours = (
        round((time.time() - fetched_at) / 3600.0, 2) if fetched_at is not None else None
    )
    db_bars = db_side["bars"]
    unavailable = (
        db_side.get("status") in {"UNAVAILABLE", "CORRUPT_OR_UNREADABLE"}
        or json_side.get("status") == INVALID
    )
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": (
            "REAL_MEXC_MARKET_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"
        ),
        "snapshot_status": "UNAVAILABLE" if unavailable else (
            "REAL" if present else "NO_SNAPSHOT"
        ),
        "snapshot_present": present,
        "verdict": (
            "UNAVAILABLE"
            if unavailable
            else ("MEXC_MARKET_OK" if present else "NO_SNAPSHOT")
        ),
        "venue": "MEXC",
        "symbol": "BTCUSDT",
        "latest_price": latest,
        "change_24h_pct": change_24h_pct,
        "volatility_30d_ann": vol_30d,
        "coverage": {
            "date_start": _iso(bars[0]["t"]) if bars else None,
            "date_end": _iso(bars[-1]["t"]) if bars else None,
            "n_rows": len(bars),
        },
        "freshness": {
            "fetched_at": fetched_at,
            "age_hours": age_hours,
            "fresh_within_hours": _FRESHNESS_LIMIT_HOURS,
            "is_fresh": (
                age_hours is not None and age_hours <= _FRESHNESS_LIMIT_HOURS
            ),
        },
        "db": {
            "status": db_side.get("status"),
            "present": db_side["present"],
            "n_rows": len(db_bars),
            "date_start": _iso(db_bars[0]["t"]) if db_bars else None,
            "date_end": _iso(db_bars[-1]["t"]) if db_bars else None,
        },
        # 逐日 OHLCV(最近 ~120 根,PIT 库只读;klines JSON 快照只存元数据、无逐行数据,
        # 故 bars 只能来自 crypto_mexc.db)。库缺 → 诚实空数组,页面降级渲染(见前端
        # MexcMarketPayload.bars 可选契约)。
        "bars": db_side["candles"][-_MARKET_BARS_LIMIT:],
        "honesty_note": (
            "MEXC 现货 BTC 行情只读快照(零手续费现货,Binance 兼容 v3)。数据来自离线抓取的 "
            "mexc_btc_klines.json / crypto_mexc.db,非实时推送;24h 涨跌为最近两根日 K 收盘差,"
            "30 日波动率为对数收益年化。仅研究/展示,不构成任何下单能力。"
        ),
    }


@router.get("/api/v1/console/mexc-market")
async def mexc_market() -> Dict[str, Any]:
    return with_snapshot_metadata(_mexc_market_payload())


# ---------------------------------------------------------------------------
# strategy dry-run surface
# ---------------------------------------------------------------------------

def _max_drawdown_from_curve(equities: List[float]) -> Optional[float]:
    if not equities:
        return None
    peak = equities[0]
    worst = 0.0
    for e in equities:
        if e > peak:
            peak = e
        if peak > 0:
            worst = min(worst, e / peak - 1.0)
    return round(worst, 6)


def _buy_and_hold(bars: List[Dict[str, float]], curve: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Buy-and-hold BTC benchmark aligned to the dry-run period timestamps."""
    empty: Dict[str, Any] = {"present": False}
    if not bars or len(curve) < 2:
        return empty
    ts = [_to_seconds(p.get("t")) for p in curve]
    if any(t is None for t in ts):
        return empty
    points: List[Dict[str, float]] = []
    idx = 0
    base_close = None
    for t in ts:
        while idx < len(bars) and bars[idx]["t"] <= t:
            idx += 1
        if idx == 0:
            continue
        close = bars[idx - 1]["close"]
        if base_close is None:
            base_close = close
        points.append({"t": t, "equity": close / base_close})
    if len(points) < 2:
        return empty
    equities = [p["equity"] for p in points]
    rets = [
        equities[i] / equities[i - 1] - 1.0
        for i in range(1, len(equities))
        if equities[i - 1] > 0
    ]
    sharpe = None
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        if var > 0:
            # weekly cadence of the multi-period dry run
            sharpe = round(mean / math.sqrt(var) * math.sqrt(52.0), 4)
    return {
        "present": True,
        "total_return": round(equities[-1] - 1.0, 6),
        "sharpe": sharpe,
        "max_drawdown": _max_drawdown_from_curve(equities),
        "curve": [{"t": p["t"], "equity": round(p["equity"], 6)} for p in points],
    }


def _mexc_strategy_payload() -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    dry_run_status = "MISSING"
    market_side = _bars_from_pit_db(_MEXC_DB)
    try:
        with _sqlite_copy(_DRY_RUN_DB) as snapshot:
            if snapshot is not None:
                dry_run_status = "REAL"
                store = DryRunStore(snapshot, create=False)
            else:
                store = None
            if store is not None:
                try:
                    runs = [
                        r
                        for r in store.all_runs()
                        if r.get("strategy_id") == _MEXC_STRATEGY_ID
                    ]
                finally:
                    store.close()
    except SqliteSnapshotError:
        dry_run_status = "UNAVAILABLE"
        runs = []
    except Exception:
        dry_run_status = "CORRUPT_OR_UNREADABLE"
        runs = []
    mexc_data_runs = [r for r in runs
                      if (r.get("detail") or {}).get("data_source") == "MEXC_PIT"]
    # canonical 口径 = MEXC 数据(2026-07 切换);只有切换前 LEGACY/CoinGecko run 时诚实标注,
    # 不冒充 MEXC 证据(与 readiness 的 dry_run_evidence 检查同一口径)。
    canonical = mexc_data_runs or runs
    canonical_source = ("MEXC_PIT" if mexc_data_runs
                        else ("LEGACY_COINGECKO_PRE_SWITCH" if runs else None))
    has = bool(canonical)
    latest = canonical[-1] if canonical else {}
    pnl = latest.get("pnl") or {}
    detail = latest.get("detail") or {}
    curve = detail.get("equity_curve") or []
    curve_vt = detail.get("equity_curve_vt") or []
    json_side = _bars_from_klines_json(_MEXC_KLINES_JSON)
    bars = json_side["bars"] or market_side["bars"]
    bnh = _buy_and_hold(bars, curve) if has else {"present": False}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": (
            "REAL_DRY_RUN_OFFLINE_SNAPSHOT" if has else "SYNTHETIC_DETERMINISTIC"
        ),
        "snapshot_status": (
            "UNAVAILABLE"
            if (
                dry_run_status in {"UNAVAILABLE", "CORRUPT_OR_UNREADABLE"}
                or market_side.get("status") in {"UNAVAILABLE", "CORRUPT_OR_UNREADABLE"}
            )
            else ("REAL" if has else "NO_SNAPSHOT")
        ),
        "dry_run_store_status": dry_run_status,
        "snapshot_present": has,
        "verdict": (
            "UNAVAILABLE"
            if (
                dry_run_status in {"UNAVAILABLE", "CORRUPT_OR_UNREADABLE"}
                or market_side.get("status") in {"UNAVAILABLE", "CORRUPT_OR_UNREADABLE"}
            )
            else ("BTC_SPOT_TREND_OK" if has else "NO_RUN")
        ),
        "venue": "MEXC",
        "strategy_id": _MEXC_STRATEGY_ID,
        "n_runs": len(canonical),
        "canonical_data_source": canonical_source,
        "n_legacy_runs": len(runs) - len(canonical),
        "metrics": {
            "total_return": pnl.get("total_return"),
            "sharpe": pnl.get("sharpe"),
            "sharpe_vt": pnl.get("sharpe_vt"),
            "max_drawdown": pnl.get("max_drawdown"),
            "max_drawdown_vt": pnl.get("max_drawdown_vt"),
            "turnover": pnl.get("turnover"),
            "n_periods": pnl.get("n_periods", detail.get("n_periods")),
            "n_approved": latest.get("n_approved"),
            "n_rejected": latest.get("n_rejected"),
            "reconciliation_residual": pnl.get("residual"),
        },
        "equity_curve": curve if isinstance(curve, list) else [],
        "equity_curve_vt": curve_vt if isinstance(curve_vt, list) else [],
        "buy_and_hold": bnh,
        "latest": latest,
        "runs": runs,
        "honesty_note": (
            "btc_spot_trend 的端到端干跑结果(预交易风控门 fail-closed + 纸面成交 + 平均成本对账),"
            "与同期 BTC buy-and-hold 基准对比(由 MEXC 行情按干跑周期对齐计算)。这是对已知历史的"
            "执行现实回测,非前向/实盘;MEXC 无现货测试网,毕业路径 = 干跑/paper + 人工 mandate 下"
            "的微小真实金额。L2_DRY_RUN_ONLY。"
        ),
    }


@router.get("/api/v1/console/mexc-strategy")
async def mexc_strategy() -> Dict[str, Any]:
    return with_snapshot_metadata(_mexc_strategy_payload())


# ---------------------------------------------------------------------------
# live-readiness checklist. Credential-domain inputs are never inspected;
# kill-switch state is read from state-root and fails closed.
# ---------------------------------------------------------------------------

def _mexc_governance_test_present() -> bool:
    if not os.path.isdir(_GOVERNANCE_TEST_DIR):
        return False
    try:
        for name in os.listdir(_GOVERNANCE_TEST_DIR):
            if name.startswith("test_") and "mexc" in name.lower() and name.endswith(".py"):
                return True
    except OSError:
        return False
    return False


def _readiness_check(check_id: str, label: str, ready: bool, reason: str) -> Dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": "READY" if ready else "BLOCKED",
        "reason": reason,
    }


def _credential_boundary_check(check_id: str, label: str) -> Dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": "BLOCKED",
        "reason": "CREDENTIAL_BOUNDARY_NOT_INSPECTED",
    }


def _fail_closed_kill_switch(reason: str):
    from quantos.contracts.killswitch import KillSwitchState

    return KillSwitchState(
        tripped=True,
        incident_id="unavailable-state-fail-closed",
        reason=reason,
    )


def _load_mexc_kill_switch_state():
    """Load state SQLite first, then strict legacy JSON; absence is TRIPPED."""
    from quantos.contracts.killswitch import KillSwitchState
    from services.risk.pre_trade.kill_switch_store import (
        KillSwitchStateStore,
        kill_switch_authority_path,
    )

    authority_path = Path(kill_switch_authority_path(_LIVE_KILL_SWITCH_STATE))
    try:
        authority_before = _inspect_sqlite_components(authority_path)
        with _sqlite_copy(os.fspath(authority_path)) as snapshot:
            if snapshot is not None:
                store = KillSwitchStateStore(str(snapshot), create=False)
                try:
                    return store.load_state()
                finally:
                    store.close()
    except Exception:
        return _fail_closed_kill_switch(
            "kill-switch authority unavailable/corrupt - failing closed"
        )

    status, raw = read_json_artifact(
        _LIVE_KILL_SWITCH_STATE, trusted_root=_STATE_ROOT
    )
    try:
        authority_after = _inspect_sqlite_components(authority_path)
    except Exception:
        return _fail_closed_kill_switch(
            "kill-switch authority path changed during legacy read - failing closed"
        )
    if authority_after != authority_before or (
        authority_after and authority_after[-1][0] == authority_path.name
    ):
        return _fail_closed_kill_switch(
            "kill-switch authority appeared during legacy read - failing closed"
        )
    if status == VALID:
        try:
            return KillSwitchState.model_validate(raw)
        except Exception:
            pass
    return _fail_closed_kill_switch(
        "kill-switch state absent/unreadable - failing closed"
    )


def _valid_smoke_artifact(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema_version") == 1
        and value.get("provenance") == "REAL_MEXC_MAINNET_READONLY_SMOKE"
        and type(value.get("ran_at_ms")) is int
        and value["ran_at_ms"] >= 0
        and isinstance(value.get("ran_at_utc"), str)
        and bool(value["ran_at_utc"].strip())
        and value.get("verdict") in {"PASS", "FAIL"}
        and isinstance(value.get("checks"), list)
        and all(isinstance(item, dict) for item in value["checks"])
        and value.get("order_response_shape") == "UNVERIFIED_UNTIL_FIRST_FILL"
        and isinstance(value.get("notes"), list)
    )


def _mexc_readiness_payload() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    adapter_exists = os.path.exists(_MEXC_ADAPTER)
    checks.append(
        _readiness_check(
            "adapter_exists",
            "MEXC 现货适配器 live/exchange/mexc_spot.py",
            adapter_exists,
            "适配器文件存在" if adapter_exists else "适配器文件不存在——live 提交路径无从谈起",
        )
    )

    gov_test = _mexc_governance_test_present()
    checks.append(
        _readiness_check(
            "adapter_governance_test",
            "适配器治理边界测试(tests/governance/test_*mexc*.py)",
            gov_test,
            "治理边界测试文件存在(无 SDK/网络出界 + L2 下 mainnet 不可实例化)"
            if gov_test
            else "缺少 MEXC 适配器治理边界测试——安全不变量未被机器钉死",
        )
    )

    checks.append(_credential_boundary_check("human_mandate", "人工 mandate(本端点不检查)"))
    checks.append(
        _credential_boundary_check(
            "validation_attestation", "验证 attestation(本端点不检查)"
        )
    )

    try:
        ks = _load_mexc_kill_switch_state()
        tripped = bool(getattr(ks, "tripped", True))
        ks_reason = getattr(ks, "reason", None)
    except Exception:
        tripped, ks_reason = True, "kill-switch state unreadable — fail-closed"
    checks.append(
        _readiness_check(
            "kill_switch_clear",
            "Kill-switch 未跳闸",
            not tripped,
            "kill-switch CLEAR" if not tripped else f"kill-switch TRIPPED({ks_reason})——人工复位前全拒",
        )
    )

    json_side = _bars_from_klines_json(_MEXC_KLINES_JSON)
    db_side = _bars_from_pit_db(_MEXC_DB)
    fetched_at = json_side["fetched_at"]
    if fetched_at is None and db_side["bars"]:
        fetched_at = db_side["bars"][-1]["t"]
    age_hours = (
        round((time.time() - fetched_at) / 3600.0, 2) if fetched_at is not None else None
    )
    data_fresh = age_hours is not None and age_hours <= _FRESHNESS_LIMIT_HOURS
    checks.append(
        _readiness_check(
            "market_data_fresh",
            f"MEXC 行情数据新鲜度(≤{_FRESHNESS_LIMIT_HOURS:.0f}h)",
            data_fresh,
            (
                f"数据年龄 {age_hours}h"
                if age_hours is not None
                else "无 MEXC 行情数据(mexc_btc_klines.json / crypto_mexc.db 均缺)"
            ),
        )
    )

    strategy_runs = 0
    mexc_data_runs = 0
    dry_run_readable = True
    try:
        with _sqlite_copy(_DRY_RUN_DB) as snapshot:
            if snapshot is not None:
                store = DryRunStore(snapshot, create=False)
                try:
                    for r in store.all_runs():
                        if r.get("strategy_id") != _MEXC_STRATEGY_ID:
                            continue
                        strategy_runs += 1
                        if (r.get("detail") or {}).get("data_source") == "MEXC_PIT":
                            mexc_data_runs += 1
                finally:
                    store.close()
    except Exception:
        dry_run_readable = False
        strategy_runs = 0
        mexc_data_runs = 0
    checks.append(
        _readiness_check(
            "dry_run_evidence",
            "btc_spot_trend 干跑执行证据(dry_run.db,MEXC 数据口径)",
            dry_run_readable and mexc_data_runs > 0,
            (
                f"{mexc_data_runs} 条 MEXC 数据干跑 run 在案"
                if dry_run_readable and mexc_data_runs > 0
                else (
                    "dry_run.db 无法取得稳定一致快照——UNAVAILABLE"
                    if not dry_run_readable
                    else (
                    f"无 MEXC 数据干跑 run(仅 {strategy_runs} 条 LEGACY/CoinGecko 切换前旧口径,"
                    "不作数)——先跑 tools/live_data_refresh 再跑 tools/dry_run_btc_spot_multi"
                    if strategy_runs
                    else "无 btc_spot_trend 干跑 run——执行现实未检验"
                    )
                )
            ),
        )
    )

    checks.append(
        _credential_boundary_check(
            "credential_configured", "MEXC 凭证配置(本端点不检查)"
        )
    )

    smoke_ready = False
    smoke_reason = ("UNVERIFIED_MAINNET_RESPONSE — 只读 smoke 未跑(人工步骤: "
                    "python -m tools.mexc_readonly_smoke)")
    smoke_status, smoke = read_json_artifact(
        _MEXC_SMOKE_EVIDENCE, trusted_root=_STATE_ROOT
    )
    if smoke_status == VALID:
        if _valid_smoke_artifact(smoke):
            if smoke.get("verdict") == "PASS":
                smoke_ready = True
                smoke_reason = (
                    f"只读 smoke PASS(ran_at={smoke.get('ran_at_utc')});下单响应形状仍 "
                    "UNVERIFIED_UNTIL_FIRST_FILL(首单人工验证)"
                )
            else:
                smoke_reason = (f"只读 smoke verdict={smoke.get('verdict')!r} — 解决后重跑 "
                                "tools/mexc_readonly_smoke.py")
        else:
            smoke_reason = "smoke 证据 schema 无效 — 由人工重跑只读 smoke"
    elif smoke_status == INVALID:
        smoke_reason = "smoke 证据不可安全读取 — 由人工检查并重跑只读 smoke"
    checks.append(
        _readiness_check(
            "mainnet_smoke_evidence",
            "mainnet 只读首验证据(evidence/mexc_mainnet_smoke.json)",
            smoke_ready,
            smoke_reason,
        )
    )

    try:
        from quantos.contracts.config import SafetyConfig

        live_armed = bool(SafetyConfig().live_trading_armed)
    except Exception:
        live_armed = True  # fail-closed: unknown config => treat as armed/blocked
    checks.append(
        _readiness_check(
            "live_flag_disarmed",
            "SafetyConfig.live_trading_armed 默认关闭(L2 安全边界)",
            not live_armed,
            (
                "默认配置下 live_trading_armed=False——安全边界完整"
                if not live_armed
                else "live_trading_armed=True——L2 边界被破坏,立即人工介入"
            ),
        )
    )

    n_blocked = sum(1 for c in checks if c["status"] == "BLOCKED")
    verdict = "READY" if n_blocked == 0 else "BLOCKED"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": "REAL_MEXC_READINESS_CHECK",
        # 显式 REAL:清单本身永远可计算(各项缺失诚实体现在 checks/verdict,不是数据不可用);
        # 不设的话 verdict=BLOCKED 会被通用推断误标成 DATA_BLOCKED(那是"数据被质量门拦"的语义,
        # 与"实盘未就绪"是两回事)。
        "snapshot_status": "REAL",
        "snapshot_present": True,
        "verdict": verdict,
        "venue": "MEXC",
        "readiness": "L2_DRY_RUN_ONLY",
        "n_ready": len(checks) - n_blocked,
        "n_blocked": n_blocked,
        "checks": checks,
        "kill_switch": {"tripped": tripped, "reason": ks_reason},
        "honesty_note": (
            "MEXC 实盘就绪清单:全部 READY 也只是'具备提交人工评审的材料',绝不代表可以实盘——"
            "MEXC 无现货测试网,毕业路径 = 干跑/paper + 多方人工 mandate 下的微小真实金额;"
            "credential/mandate/attestation 固定 NOT_ASSESSED/BLOCKED,不检查路径、内容或环境变量;"
            "kill-switch 缺失或不可读即 TRIPPED fail-closed。"
            "mainnet_smoke_evidence 在未跑只读 smoke 前诚实报 UNVERIFIED_MAINNET_RESPONSE;"
            "凭证权限验证属于隔离的人工作业,不属于本端点。"
            "本端点只读,不授予任何能力。"
        ),
    }


@router.get("/api/v1/console/mexc-readiness")
async def mexc_readiness() -> Dict[str, Any]:
    return with_snapshot_metadata(_mexc_readiness_payload())


__all__ = ["router"]
