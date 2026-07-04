"""真前向 paper-track 周度驱动器(Stage 0,实盘路线)。设计:
docs/superpowers/specs/2026-07-04-forward-track-live-design.md §2.3。

每次运行(幂等,错过可 catch-up):
1. 增量刷新三个数据源(tools/live_data_refresh);
2. build_panel(OI 大币宇宙)→ 与干跑驱动器**同一条信号路径**算 CROWD_CONTRA(主边)与
   CARRY(对照,同宇宙变体,诚实命名 carry_oi_majors)→ _legs 权重;
3. 距上次决策 ≥7 天则 record_decision(决策先落库,墙钟 recorded_at 证明先后);
4. realize_due 兑现所有到期决策(gross+资金费口径,与干跑一致,不含交易成本——source 诚实标注);
5. 写 data/cache/forward_track_status.json + 告警(alert_sink)。

墙钟/网络只在此驱动器边界;核心(forward.py)纯确定性。这是证据积累,非晋级门禁——
真钱 Go/No-Go 是 Phase 3A 的人工评审(≥8 周门,用户已拍板)。
调度(Windows,每周一 09:00,错过下次自动补;必须 -m 方式跑——repo 根要在 sys.path):
  schtasks /Create /SC WEEKLY /D MON /TN QUANTOS_ForwardTrack /ST 09:00 ^
    /TR "cmd /c cd /d H:\\code\\QUANTOS && python -m tools.forward_track_advance"
"""
from __future__ import annotations

import json
import os
import sys
import time

from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.paper_trading.forward import ForwardLedger, realize_due
from services.research.paper_trading.store import PaperTrackStore
from tools.alert_sink import emit
from tools.crypto_oi_research import CARRY, QUANTILE, _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.live_data_refresh import STALE_LIMITS, run_refresh, staleness_days

_DB = os.path.join("data", "cache", "paper_track.db")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_STATUS = os.path.join("data", "cache", "forward_track_status.json")
_DAY_MS = 86_400_000
_WEEK_MS = 7 * _DAY_MS
_DECISION_DUE_MS = _WEEK_MS - 12 * 3600 * 1000      # 7 天 − 12h 容差(数据网格抖动)
_SYSTEMIC_STALE_FRAC = 0.2                          # >20% ticker 落后 = 系统性问题(≠个别退市)
_ALPHAS = {"crowd_contra": CROWD_CONTRA, "carry_oi_majors": CARRY}


def _weekly_rows(event_times) -> list:
    rows, last = [], None
    for r in range(len(event_times)):
        if last is None or event_times[r] - last >= _WEEK_MS:
            rows.append(r)
            last = event_times[r]
    return rows


def _weights_and_signals(sig_row, insts):
    legs = _legs(sig_row, QUANTILE)
    weights = {insts[i]: w for i, w in legs.items()}
    signals = {insts[i]: sig_row[i] for i in range(len(insts)) if sig_row[i] is not None}
    return weights, signals


def _reference_sharpe(sig, rows, ev, insts, prices, funding_sum, *, before_ts) -> tuple:
    """冻结的历史参照:对 before_ts 之前的周度网格,用与兑现完全相同的公式重算周度序列的 sharpe。
    与 paper_sharpe 同单位(周度、非年化)——apples-to-apples。返回 (sharpe, n_weeks)。"""
    rets = []
    for k in range(len(rows) - 1):
        r0, r1 = rows[k], rows[k + 1]
        t0, t1 = int(ev[r0]), int(ev[r1])
        if t1 >= before_ts:
            break
        legs = _legs(sig[r0], QUANTILE)
        if not legs:
            continue
        weights = {insts[i]: w for i, w in legs.items()}
        p0, p1 = prices.get(t0, {}), prices.get(t1, {})
        names = [n for n in weights if (p0.get(n) or 0) > 0 and (p1.get(n) or 0) > 0]
        if len(names) < 0.8 * len(weights):
            continue
        r = sum(weights[n] * (p1[n] / p0[n] - 1.0) for n in names)
        r += sum(-weights[n] * funding_sum(n, t0, t1) for n in names)
        rets.append(r)
    return (sharpe(rets) if len(rets) >= 10 else None), len(rets)


def main() -> None:
    now_ms = int(time.time() * 1000)
    alerts: list = []
    do_refresh = "--no-refresh" not in sys.argv

    if do_refresh:
        try:
            refresh = run_refresh(now_ms=now_ms)
            for rep in refresh["reports"]:
                for f in rep.get("failed", []):
                    alerts.append(f"refresh {rep['source']}: {f}")
        except Exception as exc:
            emit([f"refresh CRASHED: {exc}"], context="forward_track", now_ms=now_ms)
            raise
    stale = staleness_days(now_ms)
    for src, limit in STALE_LIMITS.items():
        s = stale.get(src) or {}
        fresh, n_stale, n_total = s.get("freshest_days"), s.get("n_stale"), s.get("n_total", 0)
        if fresh is None or fresh > limit:
            alerts.append(f"staleness {src}: source stopped updating (freshest {fresh}d, limit {limit})")
        elif n_stale and n_total and n_stale / n_total > _SYSTEMIC_STALE_FRAC:
            alerts.append(f"staleness {src}: systemic lag {n_stale}/{n_total} tickers > {limit}d")

    panel, _ = build_panel()
    insts = panel.instruments
    ev = panel.event_times
    close = panel.matrices["close"]
    rows = _weekly_rows(ev)
    prices = {int(ev[r]): {insts[i]: close[r][i] for i in range(len(insts))
                           if close[r][i] and close[r][i] > 0} for r in rows}
    fund = {tk: {int(k): v for k, v in d.items()}
            for tk, d in json.load(open(_FUND, encoding="utf-8")).items()}

    def funding_sum(name, t0, t1):
        return sum(v for day, v in fund.get(name, {}).items() if t0 < day <= t1)

    ledger = ForwardLedger(_DB, create=True)
    store = PaperTrackStore(_DB, create=True)
    status = {"ran_at": now_ms, "staleness_days": stale, "alphas": {}}

    for alpha_id, expr in _ALPHAS.items():
        sig = ax.evaluate(expr, panel)
        r_last = rows[-1]
        t_last = int(ev[r_last])
        decs = ledger.decisions(alpha_id)
        due = (not decs) or (t_last - decs[-1]["decided_ts"] >= _DECISION_DUE_MS)
        recorded_new = False
        if due:
            weights, signals = _weights_and_signals(sig[r_last], insts)
            if weights:
                recorded_new = ledger.record_decision(
                    alpha_id, t_last, recorded_at=now_ms, weights=weights, signals=signals)
            else:
                alerts.append(f"{alpha_id}: no legs at {t_last} — decision skipped")

        first_dec_ts = (decs[0]["decided_ts"] if decs
                        else t_last if recorded_new else int(ev[rows[-1]]) + 1)
        ref_sharpe, ref_weeks = _reference_sharpe(sig, rows, ev, insts, prices, funding_sum,
                                                  before_ts=first_dec_ts)
        out = realize_due(ledger, store, alpha_id, timestamps=[int(ev[r]) for r in rows],
                          prices=prices, funding_sum=funding_sum, backtest_sharpe=ref_sharpe)
        alerts.extend(out["alerts"])

        track = store.track(alpha_id) or {}
        status["alphas"][alpha_id] = {
            "n_forward_periods": len([p for p in store.periods(alpha_id)
                                      if str(p["source"]).startswith("FORWARD_LIVE")]),
            "paper_sharpe": track.get("paper_sharpe"), "paper_ic": track.get("paper_ic"),
            "cum_return": track.get("cum_return"),
            "backtest_reference_sharpe": ref_sharpe, "reference_weeks": ref_weeks,
            "pending_decisions": len(ledger.pending(alpha_id)),
            "last_decision_ts": (ledger.decisions(alpha_id)[-1]["decided_ts"]
                                 if ledger.decisions(alpha_id) else None),
            "recorded_new_decision": recorded_new, "n_realized_this_run": out["n_realized"],
        }

    status["alerts"] = alerts
    json.dump(status, open(_STATUS, "w", encoding="utf-8"), indent=2)
    emit(alerts, context="forward_track", now_ms=now_ms)

    print(f"=== 前向 paper-track · {time.strftime('%Y-%m-%d %H:%M', time.gmtime(now_ms/1000))}Z "
          f"· staleness {stale} ===", flush=True)
    for aid, s in status["alphas"].items():
        print(f"  {aid}: 决策{'+1' if s['recorded_new_decision'] else '·'} "
              f"兑现+{s['n_realized_this_run']} · 前向周数 {s['n_forward_periods']} "
              f"· paper_sharpe {s['paper_sharpe']} (参照 {s['backtest_reference_sharpe']}"
              f"/{s['reference_weeks']}周) · pending {s['pending_decisions']}", flush=True)
    if alerts:
        print(f"  告警 {len(alerts)}: " + " | ".join(alerts[:5]), flush=True)
    print(f"-> {_STATUS}", flush=True)


if __name__ == "__main__":
    main()
