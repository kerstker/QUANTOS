"""Causal candidate screening and canonical USDC accounting, without network."""
from decimal import Decimal as D
import base64
import hashlib
import json
import pytest

from tools.btc_usdc_research import DAY, alpha_hac, candidate_signals, load_capture, replay, summarize


def bars(prices):
    return [{"time": (20000+i)*DAY, "close_time": (20001+i)*DAY-1,
             "close": D(str(p)), "mark": D(str(p)), "high": D(str(p))*D("1.01"),
             "low": D(str(p))*D(".99"), "volume": D("1000000")}
            for i,p in enumerate(prices)]


@pytest.mark.parametrize("name", ["trend_20_60", "breakout_20_10", "funding_reversal"])
def test_future_prices_and_funding_cannot_change_signal_prefix(name):
    old = bars([100+i for i in range(80)])
    extended = bars([100+i for i in range(80)]+[1,10000])
    future = [{"time":extended[-1]["time"], "rate":D(".05"), "mark":D("10000")}]
    assert candidate_signals(old, [], name) == candidate_signals(extended, future, name)[:80]


def test_funding_reversal_uses_settled_funding_only():
    rows=bars([100]*61+[103,104,105,106])
    event={"time":rows[61]["close_time"]+1,"rate":D(".001"),"mark":D("103")}
    assert candidate_signals(rows,[event],"funding_reversal")[61] == 0
    event["time"]-=1
    signals=candidate_signals(rows,[event],"funding_reversal")
    assert signals[61:65] == [-1,-1,-1,0]


def test_next_bar_fill_and_real_funding_fee_accounting():
    rows=bars([100]*62+[110,110])
    signals=[0]*60+[1,1,0,0]
    events=[{"time":rows[62]["time"],"rate":D(".001"),"mark":D("110")}]
    result=replay(rows,events,signals,start=rows[60]["time"],end=rows[-1]["time"]+DAY,
                  slippage_bps=0,taker_fee_bps=5)
    assert result["fills"][0]["time"] == rows[61]["close_time"]
    assert result["fills"][0]["quantity"] == "3"
    assert D(result["reconciliation"]["fee"]) == D(".315")
    assert D(result["reconciliation"]["funding"]) == D("-.33")
    assert D(result["reconciliation"]["ending_equity"]) == D("1529.355")
    assert result["reconciliation"]["residual"] == "0"
    assert result["ending_positions"] == {}


def test_flat_benchmark_has_no_profit_or_cost():
    rows=bars([100]*64)
    r=replay(rows,[],[0]*64,start=rows[60]["time"],end=rows[-1]["time"]+DAY,slippage_bps=2)
    assert r["fills"] == []
    assert summarize(r)["net_pnl_usdc"] == 0


def test_short_pays_cost_and_receives_positive_funding():
    rows=bars([100]*62+[90,90])
    events=[{"time":rows[62]["time"],"rate":D(".001"),"mark":D("90")}]
    r=replay(rows,events,[0]*60+[-1,-1,0,0],start=rows[60]["time"],end=rows[-1]["time"]+DAY,
             slippage_bps=0,taker_fee_bps=5)
    assert D(r["reconciliation"]["ending_equity"]) == D("1529.985")
    assert r["reconciliation"]["residual"] == "0"


def test_explicit_taker_fee_uses_executed_notional_and_preserves_funding():
    rows=bars([100]*62+[110,110])
    signals=[0]*60+[1,1,0,0]
    events=[{"time":rows[62]["time"],"rate":D(".001"),"mark":D("110")}]
    kwargs=dict(start=rows[60]["time"],end=rows[-1]["time"]+DAY,slippage_bps=0)
    revised=replay(rows,events,signals,**kwargs)
    historical=replay(rows,events,signals,taker_fee_bps=5,**kwargs)
    assert D(revised['reconciliation']['fee'])==D('.252')
    assert D(revised['reconciliation']['ending_equity'])==D('1529.418')
    assert revised['reconciliation']['funding']==historical['reconciliation']['funding']
    assert D(historical['reconciliation']['fee'])==D('.315')


@pytest.mark.parametrize('fee',[True,-1,'NaN','Infinity',10000])
def test_invalid_taker_fee_rejected(fee):
    rows=bars([100]*64)
    with pytest.raises(ValueError):
        replay(rows,[],[0]*64,start=rows[60]['time'],end=rows[-1]['time']+DAY,
               slippage_bps=2,taker_fee_bps=fee)


def test_unknown_candidate_rejected():
    with pytest.raises(ValueError):
        candidate_signals(bars([100]*65), [], "unknown")


def test_price_at_fill_cannot_change_already_decided_quantity():
    first=bars([100]*64)
    changed=bars([100]*61+[120]*3)
    kwargs=dict(start=first[60]["time"],end=first[-1]["time"]+DAY,slippage_bps=2)
    a=replay(first,[],[0]*60+[1]*4,**kwargs)
    b=replay(changed,[],[0]*60+[1]*4,**kwargs)
    assert a["fills"][0]["quantity"] == b["fills"][0]["quantity"] == "3"
    assert a["fills"][0]["price"] != b["fills"][0]["price"]


def test_benchmark_exposure_is_not_misreported_as_alpha():
    x=[((i%11)-5)/100 for i in range(100)]
    r=alpha_hac([.5*v for v in x],x)
    assert r["beta"] == pytest.approx(.5)
    assert r["daily_alpha"] == pytest.approx(0,abs=1e-15)


@pytest.mark.parametrize("damage", [None,"missing_last_funding","wrong_symbol","raw_tamper"])
def test_capture_rejects_missing_cashflows_wrong_instrument_and_tampering(tmp_path, damage):
    candles=[[day*DAY,"100","101","99","100","1",(day+1)*DAY-1,"1000"] for day in (20000,20001)]
    funding=[{"symbol":"BTCUSDC","fundingTime":20000*DAY+i*DAY//3,
              "fundingRate":".0001","markPrice":"100"} for i in range(6)]
    if damage == "missing_last_funding":
        funding.pop()
    if damage == "wrong_symbol":
        funding[0]["symbol"]="BTCUSDT"
    records=[]
    for kind,rows in (("bars",candles),("marks",candles),("funding",funding)):
        raw=json.dumps(rows).encode()
        records.append({"kind":kind,"raw_base64":base64.b64encode(raw).decode(),
                        "sha256":hashlib.sha256(raw).hexdigest(),
                        "requested_at_ms":1,"received_at_ms":2})
    if damage == "raw_tamper":
        records[0]["sha256"]="0"*64
    doc={"symbol":"BTCUSDC","end_ms":20002*DAY,"records":records,
         "preregistration_base64":base64.b64encode(b"frozen").decode(),
         "preregistration_sha256":hashlib.sha256(b"frozen").hexdigest()}
    path=tmp_path/"capture.json"
    path.write_text(json.dumps(doc))
    if damage:
        with pytest.raises(ValueError):
            load_capture(path)
    else:
        actual_bars,actual_funding,_=load_capture(path)
        assert len(actual_bars)==2 and len(actual_funding)==6
