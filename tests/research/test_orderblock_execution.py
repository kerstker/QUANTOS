from decimal import Decimal as D
import numpy as np
from services.research.perpetual_alpha import replay


def data():
    start=1735689900000
    times=np.arange(start,start+86400000+60000,60000,dtype=np.int64)
    return dict(times=times,prices=np.full(len(times),100.),marks=np.full(len(times),100.),
        funding=[],start=start,end=int(times[-1]),daily_volume={int(t//86400000):D('10000000') for t in times})


def test_wick_stop_uses_next_minute_quote_and_real_fee_not_stop_price():
    m=data();lows=np.full(len(m['times']),100.);highs=lows.copy()
    lows[10]=98.;lows[11]=96.;m['prices'][11]=97.;m['marks'][11]=97.
    r=replay(**m,decision=lambda at,held:.25 if at==m['start'] else None,slip_bps=5,stop=True,
        taker_fee_bps=D('3.6'),price_stop=D('.005'),trade_lows=lows,trade_highs=highs,max_holding_ms=80*3600000)
    assert len(r['fills'])==2
    assert r['fills'][-1]['time']==int(m['times'][11])
    assert D(r['fills'][-1]['price'])<D(97)
    assert all(D(f['fee'])==abs(D(f['quantity']))*D(f['price'])*D('.00036') for f in r['fills'])


def test_timeout_and_future_wick_cannot_change_prefix():
    m=data();lows=np.full(len(m['times']),100.);highs=lows.copy()
    kwargs=dict(decision=lambda at,held:.25 if at==m['start'] else None,slip_bps=5,stop=False,
        price_stop=D('.005'),trade_lows=lows,trade_highs=highs,max_holding_ms=120000)
    a=replay(**m,**kwargs)
    assert a['fills'][-1]['time']==m['start']+120000
    lows[100:]=1.
    b=replay(**m,**kwargs)
    assert a['fills']==b['fills']


def test_expired_timeout_cannot_close_a_later_episode():
    m=data();lows=np.full(len(m['times']),100.);highs=lows.copy();lows[0]=98.
    r=replay(**m,decision=lambda at,held:.25 if at in (m['start'],m['start']+120000) else None,
        slip_bps=5,stop=False,decision_interval_ms=60000,
        price_stop=D('.005'),trade_lows=lows,trade_highs=highs,max_holding_ms=120000)
    assert [f['time']-m['start'] for f in r['fills']]==[0,60000,120000,240000]


def test_completed_wick_is_not_visible_at_its_open():
    m=data();lows=np.full(len(m['times']),100.);highs=lows.copy();lows[0]=98.
    r=replay(**m,decision=lambda at,held:.25 if at==m['start'] else None,slip_bps=5,stop=True,
        price_stop=D('.005'),trade_lows=lows,trade_highs=highs)
    assert r['fills'][1]['time']==m['start']+60000
    assert r['fills'][1]['reason']=='PROTECTIVE_STOP_NEXT_QUOTE'
