"""Offline, preregistered BTCUSDC screening using the canonical Decimal ledger.

Daily-close model fills lag signals by a full bar. They are not venue fills or
an executable-book replay. No network, credentials, promotion or submission.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import statistics
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING

from services.execution.perpetual_ledger_v2 import (
    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID, CapacityAction, CapacityPolicy,
    ContractSpec, FundingEvent, LinearPerpetualLedger, MarginBracket, MarginMode, MarkEvent,
    TradeEvent, canonical_decimal, ledger_decimal_context,
)

DAY = 86_400_000
CANDIDATES = ("trend_20_60", "breakout_20_10", "funding_reversal")
BENCHMARKS = ("flat", "long_hold")


def _decimal(raw, *, positive=False):
    if isinstance(raw, bool):
        raise ValueError("boolean economic value")
    value = D(str(raw))
    if not value.is_finite() or (positive and value <= 0):
        raise ValueError("invalid economic value")
    return value


def load_capture(path):
    document = json.loads(Path(path).read_text())
    if document["symbol"] != "BTCUSDC":
        raise ValueError("only BTCUSDC data is supported")
    plan = base64.b64decode(document["preregistration_base64"], validate=True)
    if hashlib.sha256(plan).hexdigest() != document["preregistration_sha256"]:
        raise ValueError("preregistration hash mismatch")
    collected = {"bars": [], "marks": [], "funding": []}
    for record in document["records"]:
        raw = base64.b64decode(record["raw_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("raw response hash mismatch")
        if record["received_at_ms"] < record["requested_at_ms"]:
            raise ValueError("capture clock moved backwards")
        collected[record["kind"]].extend(json.loads(raw))
    marks = {}
    for row in collected["marks"]:
        timestamp = int(row[0])
        if timestamp in marks:
            raise ValueError("duplicate mark bar")
        marks[timestamp] = _decimal(row[4], positive=True)
    bars = []
    for row in collected["bars"]:
        timestamp = int(row[0])
        if timestamp % DAY or int(row[6]) != timestamp+DAY-1:
            raise ValueError("unexpected daily bar boundaries")
        if bars and timestamp != bars[-1]["time"]+DAY:
            raise ValueError("missing, repeated or unordered daily bar")
        bar = {"time": timestamp, "close_time": int(row[6]),
               "close": _decimal(row[4], positive=True),
               "high": _decimal(row[2], positive=True),
               "low": _decimal(row[3], positive=True),
               "volume": _decimal(row[7], positive=True), "mark": marks[timestamp]}
        if not bar["low"] <= bar["close"] <= bar["high"]:
            raise ValueError("invalid OHLC bar")
        bars.append(bar)
    funding = []
    for row in collected["funding"]:
        if row["symbol"] != "BTCUSDC":
            raise ValueError("wrong funding instrument")
        event = {"time": int(row["fundingTime"]), "rate": _decimal(row["fundingRate"]),
                 "mark": _decimal(row["markPrice"], positive=True)}
        if funding and event["time"] <= funding[-1]["time"]:
            raise ValueError("duplicate or unordered funding event")
        funding.append(event)
    if not bars or not funding or bars[-1]["close_time"] != document["end_ms"]-1:
        raise ValueError("incomplete observation window")
    # BTCUSDC's observed eight-hour settlement grid must have no missing slots.
    slots = [f["time"]//(8*3_600_000) for f in funding]
    if any(b != a+1 for a,b in zip(slots, slots[1:])):
        raise ValueError("funding settlement slot missing or duplicated")
    if (funding[0]["time"] > bars[0]["time"]+8*3_600_000
            or slots[-1] != (document["end_ms"]-1)//(8*3_600_000)):
        raise ValueError("funding history does not cover candles")
    return bars, funding, document


def candidate_signals(bars, funding, name):
    if name not in CANDIDATES+BENCHMARKS:
        raise ValueError("unknown preregistered candidate")
    signals, state, entered = [], 0, -1
    for i, bar in enumerate(bars):
        if i < 60 or name == "flat":
            signals.append(0)
            continue
        if name == "long_hold":
            state = 1
        elif name == "trend_20_60":
            fast = sum(b["close"] for b in bars[i-19:i+1])/20
            slow = sum(b["close"] for b in bars[i-59:i+1])/60
            state = (fast > slow)-(fast < slow)
        elif name == "breakout_20_10":
            close = bar["close"]
            if state and ((state == 1 and close < min(b["low"] for b in bars[i-10:i]))
                          or (state == -1 and close > max(b["high"] for b in bars[i-10:i]))):
                state = 0
            elif state == 0:
                if close > max(b["high"] for b in bars[i-20:i]):
                    state = 1
                elif close < min(b["low"] for b in bars[i-20:i]):
                    state = -1
        elif state:
            if i-entered >= 3:
                state = 0
        else:
            rate = sum((f["rate"] for f in funding
                        if bar["close_time"]-DAY < f["time"] <= bar["close_time"]), D(0))
            change = bar["close"]/bars[i-1]["close"]-1
            if rate >= D(".0003") and change >= D(".02"):
                state, entered = -1, i
            elif rate <= D("-.0003") and change <= D("-.02"):
                state, entered = 1, i
        signals.append(state)
    return signals


@ledger_decimal_context()
def replay(bars, funding, signals, *, start, end, slippage_bps):
    if len(signals) != len(bars) or any(type(s) is not int or s not in (-1,0,1) for s in signals):
        raise ValueError("invalid signal sequence")
    if slippage_bps not in (0,2,7,17):
        raise ValueError("unregistered cost scenario")
    indices = [i for i,b in enumerate(bars) if start <= b["time"] < end]
    if not indices or indices[0] < 60:
        raise ValueError("missing trading window or warm-up")
    spec = ContractSpec("BTCUSDC", "LINEAR_USDC_PERPETUAL", "USDC", D(1), D(".001"),
                        D(".001"), D("50"), D(".0125"), D("1e-24"),
                        (MarginBracket(D(0),None,D(1),D(".5"),D(0)),))
    ledger = LinearPerpetualLedger((spec,),initial_collateral=D(1500))
    events = [f for f in funding if start <= f["time"] < end]
    cursor, last_signal = 0, 0
    daily, fills = [], []
    for i in indices:
        bar, now = bars[i], bars[i]["close_time"]
        while cursor < len(events) and events[cursor]["time"] <= now:
            f = events[cursor]
            ledger.apply_event(FundingEvent(f["time"],"BTCUSDC",f["rate"],f["mark"]))
            cursor += 1
        ledger.apply_event(MarkEvent(now,"BTCUSDC",bar["mark"]))
        # First day starts flat. The final scheduled liquidation is known in advance.
        signal = 0 if i in (indices[0],indices[-1]) else signals[i-1]
        position = ledger.position("BTCUSDC")
        quantity = D(0) if position is None else position.quantity
        if signal != last_signal or (i == indices[-1] and quantity):
            # Size at the preceding decision price, never at the future fill price.
            target = (D(300)/bars[i-1]["close"]/D(".001")).to_integral_value(rounding=ROUND_FLOOR)*D(".001")*signal
            delta = target-quantity
            if delta:
                rate = D(slippage_bps)/10000
                execution = bar["close"]*(1+rate if delta>0 else 1-rate)
                execution = (execution/D(".1")).to_integral_value(
                    rounding=ROUND_CEILING if delta>0 else ROUND_FLOOR)*D(".1")
                adv = (sum(b["volume"] for b in bars[i-30:i])/30).quantize(D(".00000001"))
                capacity = CapacityPolicy("BINANCE_USDM",str(bar["time"]//DAY),
                    BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,bar["time"]//DAY,adv,D(".01"),CapacityAction.REJECT)
                fee = abs(delta)*execution*D(".0005")
                result = ledger.apply_event(TradeEvent(now,"BTCUSDC",delta,execution,MarginMode.CROSS,
                                                        fee=fee,capacity=capacity))
                if result.filled_quantity != delta:
                    raise ValueError("model fill rejected; cannot silently omit trade")
                fills.append({"time":now,"decision_time":bars[i-1]["close_time"],
                              "quantity":canonical_decimal(delta),"price":canonical_decimal(execution),
                              "fee":canonical_decimal(fee)})
            last_signal = signal
        snapshot = ledger.reconciliation_snapshot()
        if snapshot.residual != 0:
            raise ValueError("cash-flow reconciliation failed")
        position = ledger.position("BTCUSDC")
        daily.append({"time":now,"equity":canonical_decimal(ledger.account_equity()),
                      "quantity":canonical_decimal(D(0) if position is None else position.quantity)})
    return {"daily":daily,"fills":fills,
            "reconciliation":ledger.reconciliation_snapshot().as_canonical_dict(),
            "ending_positions":ledger.state_as_canonical_dict()["positions"],
            "ledger_events":list(ledger.event_records)}


def summarize(result):
    equities = [1500.0]+[float(r["equity"]) for r in result["daily"]]
    returns = [b/a-1 for a,b in zip(equities,equities[1:])]
    peak, drawdown = equities[0], 0.0
    for value in equities:
        peak = max(peak,value)
        drawdown = max(drawdown,1-value/peak)
    sd = statistics.stdev(returns) if len(returns)>1 else 0
    r = result["reconciliation"]
    return {"net_pnl_usdc":equities[-1]-1500,"max_drawdown":drawdown,
            "sharpe":statistics.fmean(returns)/sd*math.sqrt(365) if sd else None,
            "fills":len(result["fills"]),"fee_usdc":float(r["fee"]),
            "funding_usdc":float(r["funding"]),"daily_returns":returns}


def alpha_hac(y, x):
    n = len(y)
    if n != len(x) or n < 30:
        raise ValueError("unaligned or insufficient returns")
    sx, sx2 = sum(x), sum(v*v for v in x)
    det = n*sx2-sx*sx
    if det <= 0:
        return {"daily_alpha":None,"beta":None,"hac_t":None}
    beta = (n*sum(a*b for a,b in zip(x,y))-sx*sum(y))/det
    alpha = (sum(y)-beta*sx)/n
    scores = [(sx2/det-sx/det*a)*(b-alpha-beta*a) for a,b in zip(x,y)]
    variance = sum(g*g for g in scores)
    for lag in range(1,8):
        variance += 2*(1-lag/8)*sum(scores[i]*scores[i-lag] for i in range(lag,n))
    variance *= n/(n-2)
    return {"daily_alpha":alpha,"beta":beta,"hac_t":alpha/math.sqrt(variance) if variance>0 else None}


def _timestamp(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()*1000)


def run_screen(path):
    bars,funding,document = load_capture(path)
    windows = {"development":("2024-04-01","2025-01-01"),
               "validation":("2025-01-01","2026-01-01"),
               "holdout":("2026-01-01","2026-09-01")}
    signals = {name:candidate_signals(bars,funding,name) for name in BENCHMARKS+CANDIDATES}
    results = {}
    for name in signals:
        results[name] = {}
        for label,(start,end) in windows.items():
            results[name][label] = {}
            for slip in (2,7,17):
                r = replay(bars,funding,signals[name],start=_timestamp(start),end=_timestamp(end),slippage_bps=slip)
                results[name][label][str(slip)] = {"metrics":summarize(r),"replay":r}
        print("completed",name,flush=True)
    admission = {}
    for name in CANDIDATES:
        value = results[name]
        oos = value["holdout"]["2"]["metrics"]
        alpha = alpha_hac(oos["daily_returns"],results["long_hold"]["holdout"]["2"]["metrics"]["daily_returns"])
        checks = {"validation_net_positive":value["validation"]["2"]["metrics"]["net_pnl_usdc"]>0,
                  "holdout_net_positive":oos["net_pnl_usdc"]>0,
                  "stress_cost_positive":value["holdout"]["17"]["metrics"]["net_pnl_usdc"]>0,
                  "at_least_ten_fills":oos["fills"]>=10,
                  "positive_alpha_hac_t":alpha["daily_alpha"] is not None and alpha["daily_alpha"]>0
                      and alpha["hac_t"] is not None and alpha["hac_t"]>2.4}
        admission[name] = {"checks":checks,"benchmark_regression":alpha,
                           "worth_forward_research":all(checks.values()),"live_eligible":False}
    return {"status":"TECHNICAL_VALIDATION_ONLY","authoritative":False,"live_eligible":False,
            "symbol":"BTCUSDC","margin_asset":"USDC","preregistration_sha256":document["preregistration_sha256"],
            "capture_sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "trial_count":3,"cost_scenarios":3,"windows":windows,"results":results,"screen":admission,
            "limitations":["HISTORICAL_RECONSTRUCTION_NOT_RECEIPT_TIME_PROOF","DAILY_CLOSE_MODEL_NOT_EXECUTABLE_QUOTES",
                "HISTORICAL_RULES_AND_ACCOUNT_FEES_UNVERIFIED","DAILY_RISK_NOT_CONTINUOUS_LOSS_BUDGET",
                "NO_SIGNED_AUTHORITY_OR_FORWARD_APPROVAL"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("refusing to overwrite frozen research result")
    result = run_screen(args.capture)
    root = Path(__file__).resolve().parents[1]
    result["source_sha256"] = {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in (
        "tools/btc_usdc_research.py","services/execution/perpetual_ledger_v2.py")}
    with args.output.open("x") as stream:
        json.dump(result,stream,sort_keys=True,allow_nan=False)
    print(json.dumps(result["screen"],sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
