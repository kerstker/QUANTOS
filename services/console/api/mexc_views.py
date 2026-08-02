"""Read-only MEXC (zero-fee BTC spot) integration console routes.

Three surfaces, all BACKEND_COMPUTED_SNAPSHOT + honest provenance, failing
closed to an explicit synthetic/absent marker when the underlying artifact has
not been produced yet:

- ``/api/v1/console/mexc-market`` — BTC market snapshot built from
  ``data/cache/mexc_btc_klines.json`` and/or the PIT ``crypto_mexc.db``.
- ``/api/v1/console/mexc-strategy`` — the ``btc_spot_trend`` dry-run results
  from ``dry_run.db`` (equity curve, sharpe, drawdown, turnover) plus a
  buy-and-hold benchmark computed from the same market data.
- ``/api/v1/console/mexc-readiness`` — a live-readiness checklist.  Existence
  of mandate/attestation files is reported WITHOUT reading their contents;
  the kill-switch state is read through the risk-owned store (fail-closed).

This module never touches the network, never reads credentials, and never
grants any live capability.  L2_DRY_RUN_ONLY.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter

from services.console.api.common import with_snapshot_metadata
from services.execution.dry_run_store import DryRunStore


router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MEXC_KLINES_JSON = os.path.join(_REPO_ROOT, "data", "cache", "mexc_btc_klines.json")
_MEXC_DB = os.path.join(_REPO_ROOT, "data", "cache", "crypto_mexc.db")
_DRY_RUN_DB = os.path.join(_REPO_ROOT, "data", "cache", "dry_run.db")
_MEXC_ADAPTER = os.path.join(_REPO_ROOT, "live", "exchange", "mexc_spot.py")
_MEXC_MANDATE = os.path.join(_REPO_ROOT, "key", "live_mandate_mexc.json")
_MEXC_KEY_FILE = os.path.join(_REPO_ROOT, "key", "mexc_spot.json")
_MEXC_SMOKE_EVIDENCE = os.path.join(_REPO_ROOT, "evidence", "mexc_mainnet_smoke.json")
_ATTESTATIONS = os.path.join(_REPO_ROOT, "key", "live_validation_attestations.json")
_LIVE_KILL_SWITCH_STATE = os.path.join(_REPO_ROOT, "data", "cache", "live_kill_switch.json")
_GOVERNANCE_TEST_DIR = os.path.join(_REPO_ROOT, "tests", "governance")
_MEXC_STRATEGY_ID = "btc_spot_trend"
_FRESHNESS_LIMIT_HOURS = 72.0


# ---------------------------------------------------------------------------
# dependency injection (composition root wires the kill-switch loader so this
# module does not import the console root, and services/ never imports live/)
# ---------------------------------------------------------------------------

class MexcViewRuntime:
    def __init__(self) -> None:
        self._kill_switch_loader: Optional[Callable[[], Any]] = None

    def configure(self, *, kill_switch_state: Callable[[], Any]) -> None:
        if self._kill_switch_loader is None:
            self._kill_switch_loader = kill_switch_state
            return
        if self._kill_switch_loader is not kill_switch_state:
            raise RuntimeError("mexc view runtime is already configured")

    def kill_switch_state(self) -> Any:
        if self._kill_switch_loader is None:
            raise RuntimeError(
                "mexc view runtime must be configured by the console composition root"
            )
        return self._kill_switch_loader()


mexc_view_runtime = MexcViewRuntime()


def configure_mexc_views(*, kill_switch_state: Callable[[], Any]) -> None:
    mexc_view_runtime.configure(kill_switch_state=kill_switch_state)


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
    if not os.path.exists(path):
        return {"bars": [], "fetched_at": None}
    try:
        with open(path, encoding="utf-8") as fh:
            snap = json.load(fh)
    except Exception:
        return {"bars": [], "fetched_at": None}
    fetched_at = None
    raw: Any = snap
    if isinstance(snap, dict):
        fetched_at = _to_seconds(
            snap.get("fetched_at", snap.get("generated_at", snap.get("observed_at")))
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
    if fetched_at is None:
        try:
            fetched_at = os.path.getmtime(path)
        except OSError:
            fetched_at = None
    return {"bars": bars, "fetched_at": fetched_at}


def _bars_from_pit_db(path: str) -> Dict[str, Any]:
    """Read BTC OHLCV bars from the PIT sqlite (read-only), latest knowledge."""
    if not os.path.exists(path):
        return {"present": False, "bars": []}
    bars: List[Dict[str, float]] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT event_time, payload_json FROM bars WHERE key='BTC' "
                "GROUP BY event_time HAVING MAX(knowledge_time) ORDER BY event_time"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {"present": False, "bars": []}
    for event_time, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except Exception:
            continue
        bar = _bar_from_row({"t": event_time, "close": payload.get("close")})
        if bar is not None:
            bars.append(bar)
    return {"present": True, "bars": bars}


_MARKET_BARS_LIMIT = 120


def _ohlcv_bars_from_pit_db(path: str, limit: int = _MARKET_BARS_LIMIT) -> List[Dict[str, Any]]:
    """PIT sqlite 里 BTC 的最近 `limit` 根日线 OHLCV(供 CandleChart;t=**秒**,字段名镜像
    前端 `MexcMarketPayload.bars` / `Candle` 契约:open/high/low/close 必填、volume 可选)。
    库缺/读不出/行形状坏 → 诚实空列表(页面在无 bars 时本就诚实降级,绝不硬凑)。"""
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT event_time, payload_json FROM bars WHERE key='BTC' "
                "GROUP BY event_time HAVING MAX(knowledge_time) "
                "ORDER BY event_time DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for event_time, payload_json in rows:
        try:
            payload = json.loads(payload_json)
            bar: Dict[str, Any] = {
                "t": float(event_time) / 1000.0,
                "open": float(payload["open"]),
                "high": float(payload["high"]),
                "low": float(payload["low"]),
                "close": float(payload["close"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        volume = payload.get("volume")
        if isinstance(volume, (int, float)) and not isinstance(volume, bool) and volume > 0:
            bar["volume"] = float(volume)
        if bar["open"] <= 0 or bar["close"] <= 0 or not all(
            math.isfinite(bar[k]) for k in ("open", "high", "low", "close")
        ):
            continue
        out.append(bar)
    out.reverse()                      # DESC 取尾部 N 根后还原时间升序
    return out


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
    db_side = _bars_from_pit_db(_MEXC_DB)
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
        try:
            fetched_at = os.path.getmtime(_MEXC_DB)
        except OSError:
            fetched_at = None
    age_hours = (
        round((time.time() - fetched_at) / 3600.0, 2) if fetched_at is not None else None
    )
    db_bars = db_side["bars"]
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": (
            "REAL_MEXC_MARKET_SNAPSHOT" if present else "SYNTHETIC_DETERMINISTIC"
        ),
        "snapshot_present": present,
        "verdict": "MEXC_MARKET_OK" if present else "NO_SNAPSHOT",
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
            "present": db_side["present"],
            "n_rows": len(db_bars),
            "date_start": _iso(db_bars[0]["t"]) if db_bars else None,
            "date_end": _iso(db_bars[-1]["t"]) if db_bars else None,
        },
        # 逐日 OHLCV(最近 ~120 根,PIT 库只读;klines JSON 快照只存元数据、无逐行数据,
        # 故 bars 只能来自 crypto_mexc.db)。库缺 → 诚实空数组,页面降级渲染(见前端
        # MexcMarketPayload.bars 可选契约)。
        "bars": _ohlcv_bars_from_pit_db(_MEXC_DB),
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
    if os.path.exists(_DRY_RUN_DB):
        try:
            store = DryRunStore(_DRY_RUN_DB, create=False)
            try:
                runs = [
                    r
                    for r in store.all_runs()
                    if r.get("strategy_id") == _MEXC_STRATEGY_ID
                ]
            finally:
                store.close()
        except Exception:
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
    db_side = _bars_from_pit_db(_MEXC_DB)
    bars = json_side["bars"] or db_side["bars"]
    bnh = _buy_and_hold(bars, curve) if has else {"present": False}
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT",
        "read_only": True,
        "api_version": "0.1.0",
        "data_provenance": (
            "REAL_DRY_RUN_OFFLINE_SNAPSHOT" if has else "SYNTHETIC_DETERMINISTIC"
        ),
        "snapshot_present": has,
        "verdict": "BTC_SPOT_TREND_OK" if has else "NO_RUN",
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
# live-readiness checklist (existence checks only — never reads file contents
# of mandate/attestation; kill-switch read fail-closed via composition root)
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

    mandate_exists = os.path.exists(_MEXC_MANDATE)
    checks.append(
        _readiness_check(
            "human_mandate",
            "人工签署 mandate(key/live_mandate_mexc.json,仅检查存在性)",
            mandate_exists,
            "mandate 文件存在(内容不读取、不泄露)"
            if mandate_exists
            else "缺少人工签署的 mandate 文件——真人未签字授权任何真实金额上限",
        )
    )

    attestation_exists = os.path.exists(_ATTESTATIONS)
    checks.append(
        _readiness_check(
            "validation_attestation",
            "验证 attestation(key/live_validation_attestations.json,仅检查存在性)",
            attestation_exists,
            "attestation 文件存在(内容不读取、不泄露)"
            if attestation_exists
            else "缺少验证 attestation——无 PASS 验证记录或可审计人工 override",
        )
    )

    try:
        ks = mexc_view_runtime.kill_switch_state()
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
        try:
            fetched_at = os.path.getmtime(_MEXC_DB)
        except OSError:
            fetched_at = None
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
    if os.path.exists(_DRY_RUN_DB):
        try:
            store = DryRunStore(_DRY_RUN_DB, create=False)
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
            strategy_runs = 0
            mexc_data_runs = 0
    checks.append(
        _readiness_check(
            "dry_run_evidence",
            "btc_spot_trend 干跑执行证据(dry_run.db,MEXC 数据口径)",
            mexc_data_runs > 0,
            (
                f"{mexc_data_runs} 条 MEXC 数据干跑 run 在案"
                if mexc_data_runs > 0
                else (
                    f"无 MEXC 数据干跑 run(仅 {strategy_runs} 条 LEGACY/CoinGecko 切换前旧口径,"
                    "不作数)——先跑 tools/live_data_refresh 再跑 tools/dry_run_btc_spot_multi"
                    if strategy_runs
                    else "无 btc_spot_trend 干跑 run——执行现实未检验"
                )
            ),
        )
    )

    credential_configured = bool(os.environ.get("MEXC_API_KEY")) or os.path.exists(_MEXC_KEY_FILE)
    checks.append(
        _readiness_check(
            "credential_configured",
            "MEXC 凭证已放置(MEXC_API_KEY env 或 key/mexc_spot.json,仅检查存在性)",
            credential_configured,
            (
                "凭证已配置(内容不读取、不泄露)"
                if credential_configured
                else "缺少 MEXC 凭证——人工放 key/mexc_spot.json 或设 env(提现必须在交易所侧关闭)"
            ),
        )
    )

    smoke_ready = False
    smoke_reason = ("UNVERIFIED_MAINNET_RESPONSE — 只读 smoke 未跑(人工步骤: "
                    "python -m tools.mexc_readonly_smoke)")
    if os.path.exists(_MEXC_SMOKE_EVIDENCE):
        try:
            with open(_MEXC_SMOKE_EVIDENCE, encoding="utf-8") as fh:
                smoke = json.load(fh)
        except Exception:
            smoke = None
        if isinstance(smoke, dict):
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
            smoke_reason = "smoke 证据文件损坏 — 重跑 tools/mexc_readonly_smoke.py"
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
            "mandate/attestation 仅检查存在性、绝不读取内容;kill-switch 读取 fail-closed。"
            "mainnet_smoke_evidence 在未跑只读 smoke 前诚实报 UNVERIFIED_MAINNET_RESPONSE;"
            "credential_configured 仅检查 key 存在性,权限位以 verify_key_permissions 为准。"
            "本端点只读,不授予任何能力。"
        ),
    }


@router.get("/api/v1/console/mexc-readiness")
async def mexc_readiness() -> Dict[str, Any]:
    return with_snapshot_metadata(_mexc_readiness_payload())


__all__ = ["router", "configure_mexc_views", "mexc_view_runtime"]
