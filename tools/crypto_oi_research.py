"""Out-of-gate: does perp POSITIONING (open interest + long/short) add a SECOND edge ORTHOGONAL to carry?

The funding-carry edge is established (eco_SR ~2.0). This asks the honest follow-up: on the SAME liquid
universe, does any open-interest / long-short positioning factor add INCREMENTAL information ratio after
the carry return stream is already in the book — or is it redundant / null? It reuses the real machinery:
PIT-leakage proof, the carry strategy's exact dollar-neutral book + price/funding P&L, rank-IC, no-peek
walk-forward SELECTION OOS, and Gram-Schmidt orthogonalization (explained_r2 + marginal_ir vs carry).

Data: data/cache/crypto_oi_metrics.json (Binance Data Vision metrics archive, eod per UTC day, PIT-safe)
aligned to the existing price+funding panel. Liquid majors only. Observed numbers only — no fabrication;
the answer (edge / redundant / null) is whatever the machine reports.
"""
from __future__ import annotations

import json
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.expression_leakage import compute_leakage_ok
from services.research.oos_validation import walk_forward_selection_oos
from services.alpha.orthogonalize import orthogonalize
from services.research.factor_set import incremental_ir

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_OI = os.path.join("data", "cache", "crypto_oi_metrics.json")
_OUT = os.path.join("data", "cache", "crypto_oi_research.json")

QUANTILE = 0.3            # top/bottom 30% — the OI universe is ~24 liquid majors (thinner than the 115)
MIN_DAYS = 300           # only include a ticker with at least this many OI days
OI_FIELDS = ("oi", "oi_value", "top_ls", "global_ls", "taker_ls")

FUND = ("field", "funding")
def _zneg(op, w): return ("cs_zscore", ("neg", (op, FUND, w)))
CARRY = ("div", ("add", ("add", _zneg("decay_linear", 3), _zneg("decay_linear", 7)),
                 ("add", _zneg("decay_linear", 14), _zneg("ts_mean", 7))), ("const", 4.0))

# Positioning-factor hypotheses (economic priors), both directions where ambiguous. cs root => PIT-safe.
def _f(name, expr, thesis): return {"name": name, "expr": expr, "thesis": thesis}
FACTORS = [
    _f("oi_mom5",      ("cs_rank", ("ts_delta", ("field", "oi"), 5)),                 "rising OI = conviction/trend"),
    _f("d_oi_value5",  ("cs_zscore", ("ts_delta", ("field", "oi_value"), 5)),         "notional OI build"),
    _f("crowd_contra", ("cs_rank", ("neg", ("field", "global_ls"))),                  "fade crowded retail longs"),
    _f("crowd_follow", ("cs_rank", ("field", "global_ls")),                           "follow retail longs"),
    _f("top_follow",   ("cs_zscore", ("field", "top_ls")),                            "follow top-trader (smart money)"),
    _f("top_contra",   ("cs_zscore", ("neg", ("field", "top_ls"))),                   "fade top-trader"),
    _f("taker_imb",    ("cs_zscore", ("field", "taker_ls")),                          "aggressive taker buy pressure"),
    _f("taker_fade",   ("cs_zscore", ("neg", ("field", "taker_ls"))),                 "fade taker imbalance"),
    _f("oi_px_div",    ("cs_zscore", ("sub", ("ts_delta", ("log", ("field", "oi")), 5),
                                       ("ts_delta", ("log", ("field", "close")), 5))), "OI up while price flat/down = short build"),
    _f("dtop_ls5",     ("cs_zscore", ("ts_delta", ("field", "top_ls"), 5)),           "change in top-trader positioning"),
]


def _legs(sig_row, q):
    idx = [i for i, v in enumerate(sig_row) if v is not None]
    if len(idx) < 4:
        return {}
    idx.sort(key=lambda i: sig_row[i])
    k = max(1, int(round(len(idx) * q)))
    longs, shorts = idx[-k:], idx[:k]
    w = {}
    for i in longs:
        w[i] = 0.5 / len(longs)
    for i in shorts:
        w[i] = w.get(i, 0.0) - 0.5 / len(shorts)
    return w


def _returns(sig, px, fund_mat, q=QUANTILE):
    """Full-length aligned per-day streams for one signal: price-LS, +funding (economic), turnover.
    One value per row r in [0, T-1) (0.0 when the cross-section is too thin) so all factors stay
    index-aligned for orthogonalization / OOS. PIT-safe: signal<=t, P&L uses t+1."""
    T = len(sig)
    price_r, econ_r, turn_r = [], [], []
    prev = {}
    for r in range(T - 1):
        w = _legs(sig[r], q)
        pr = fu = 0.0
        for i, wi in w.items():
            p0, p1 = px[r][i], px[r + 1][i]
            f1 = fund_mat[r + 1][i] if fund_mat else None
            if p0 is None or p1 is None or p0 <= 0:
                continue
            pr += wi * (p1 / p0 - 1.0)
            if f1 is not None:
                fu += -wi * f1
        names = set(w) | set(prev)
        turn_r.append(sum(abs(w.get(i, 0.0) - prev.get(i, 0.0)) for i in names))
        price_r.append(pr); econ_r.append(pr + fu); prev = w
    return price_r, econ_r, turn_r


def _rank_ic(sig, px):
    """Mean cross-sectional Spearman-ish rank IC of signal[t] vs forward return t->t+1."""
    ics = []
    for r in range(len(sig) - 1):
        pairs = []
        for i in range(len(sig[r])):
            s = sig[r][i]; p0 = px[r][i]; p1 = px[r + 1][i]
            if s is None or p0 is None or p1 is None or p0 <= 0:
                continue
            pairs.append((s, p1 / p0 - 1.0))
        if len(pairs) < 4:
            continue
        sr = {v: i for i, v in enumerate(sorted(p[0] for p in pairs))}
        rr = {v: i for i, v in enumerate(sorted(p[1] for p in pairs))}
        a = [sr[p[0]] for p in pairs]; b = [rr[p[1]] for p in pairs]
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5; db = sum((y - mb) ** 2 for y in b) ** 0.5
        if da > 0 and db > 0:
            ics.append(num / (da * db))
    return round(statistics.fmean(ics), 4) if ics else None


def main() -> None:
    oi_cache = json.load(open(_OI, encoding="utf-8"))
    oi_data = oi_cache.get("data", oi_cache)
    tickers = sorted(tk for tk, d in oi_data.items() if not tk.startswith("_") and len(d) >= MIN_DAYS)
    snap_insts = set(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    instruments = sorted(tk for tk in tickers if tk in snap_insts)
    print(f"OI universe: {len(instruments)} liquid majors with >= {MIN_DAYS} days: {instruments}")

    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, -10**18, 10**18)})
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND, encoding="utf-8")).items()}

    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(inst, {}).get(t) for inst in panel.instruments]
                                 for t in panel.event_times]
    # add OI metric matrices, PIT-aligned by event_time (eod value knowable at t)
    oi_by_inst = {tk: {int(k): v for k, v in d.items()} for tk, d in oi_data.items() if not tk.startswith("_")}

    def _oi_cell(inst, t, fld):
        rec = oi_by_inst.get(inst, {}).get(t)
        return rec.get(fld) if isinstance(rec, dict) else None

    for fld in OI_FIELDS:
        panel.matrices[fld] = [[_oi_cell(inst, t, fld) for inst in panel.instruments]
                               for t in panel.event_times]
    px = panel.matrices["close"]; fund_mat = panel.matrices["funding"]

    # baseline: carry on THIS universe
    carry_sig = ax.evaluate(CARRY, panel)
    c_price, c_econ, c_turn = _returns(carry_sig, px, fund_mat)
    print(f"\nCARRY baseline (this {len(instruments)}-coin universe): "
          f"price SR {sharpe(c_price):.3f} · economic SR {sharpe(c_econ):.3f} · "
          f"funding {statistics.fmean([e-p for e,p in zip(c_econ,c_price)])*1e4:.2f} bps/day")

    rows = []
    price_streams = {"carry": tuple(c_price)}
    econ_streams = {"carry": tuple(c_econ)}
    for fac in FACTORS:
        sig = ax.evaluate(fac["expr"], panel)
        leak = compute_leakage_ok(fac["expr"], panel, rows=list(range(50, len(sig), max(1, len(sig)//40))))
        p_r, e_r, _t = _returns(sig, px, fund_mat)
        ic = _rank_ic(sig, px)
        price_streams[fac["name"]] = tuple(p_r)
        econ_streams[fac["name"]] = tuple(e_r)
        # orthogonality of the PRICE-prediction stream vs carry's price stream
        orth = orthogonalize(tuple(p_r), {"carry": tuple(c_price)}, min_overlap=120)
        inc = incremental_ir(p_r, {"carry": tuple(c_price)}, min_overlap=120)
        rows.append({"name": fac["name"], "thesis": fac["thesis"], "leakage_ok": leak,
                     "ic": ic, "price_sharpe": round(sharpe(p_r), 3), "econ_sharpe": round(sharpe(e_r), 3),
                     "explained_r2_by_carry": None if orth.explained_r2 is None else round(orth.explained_r2, 3),
                     "marginal_ir_vs_carry": None if orth.marginal_ir is None else round(orth.marginal_ir, 3),
                     "incremental_ir_vs_carry": None if inc is None else round(inc, 3)})

    rows.sort(key=lambda r: (r["incremental_ir_vs_carry"] is None, -(r["incremental_ir_vs_carry"] or -9)))
    print(f"\n{'factor':14s} {'leak':5s} {'IC':>8s} {'pxSR':>6s} {'ecoSR':>6s} {'R2|carry':>9s} {'mIR':>6s} {'incIR':>6s}  thesis")
    for r in rows:
        ic = "" if r["ic"] is None else f"{r['ic']:+.4f}"
        r2 = "" if r["explained_r2_by_carry"] is None else f"{r['explained_r2_by_carry']:.3f}"
        mir = "" if r["marginal_ir_vs_carry"] is None else f"{r['marginal_ir_vs_carry']:+.2f}"
        inc = "" if r["incremental_ir_vs_carry"] is None else f"{r['incremental_ir_vs_carry']:+.2f}"
        print(f"{r['name']:14s} {str(r['leakage_ok']):5s} {ic:>8s} "
              f"{r['price_sharpe']:>6.2f} {r['econ_sharpe']:>6.2f} {r2:>9s} {mir:>6s} {inc:>6s}  {r['thesis']}")

    # no-peek walk-forward selection OOS over carry + all positioning factors (price streams)
    T = len(c_price)
    oos = walk_forward_selection_oos(price_streams, train_size=max(252, T//4),
                                     test_size=max(120, T//8), embargo=1, top_k=1)
    print(f"\nno-peek selection OOS (carry + {len(FACTORS)} positioning, price streams): "
          f"folds={oos['n_folds']} mean_IS={oos.get('mean_is_sharpe')} mean_OOS={oos.get('mean_oos_sharpe')} "
          f"haircut={oos.get('oos_minus_is')}")
    for fold in oos.get("folds", []):
        print(f"  fold {fold['window']}: picked {fold['selected']} IS {fold['is_sharpe']:+.2f} OOS {fold['oos_sharpe']:+.2f}")

    best = [r for r in rows if r["incremental_ir_vs_carry"] is not None]
    verdict = ("NO orthogonal positioning edge over carry (all incremental IR ~0 / negative)"
               if not best or max(r["incremental_ir_vs_carry"] for r in best) < 0.3
               else "candidate orthogonal positioning factor(s) — needs adversarial verification")
    print(f"\nVERDICT: {verdict}")

    json.dump({"universe": instruments, "n_days": T, "quantile": QUANTILE,
               "carry_price_sharpe": round(sharpe(c_price), 3), "carry_econ_sharpe": round(sharpe(c_econ), 3),
               "factors": rows, "oos": oos, "verdict": verdict},
              open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUT}")


if __name__ == "__main__":
    main()
