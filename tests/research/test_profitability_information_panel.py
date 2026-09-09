import numpy as np
import pytest
from services.research.profitability_information import information_panel, fit_information


def test_information_panel_requires_each_real_available_slot_and_keeps_oi_unsigned():
    times=np.array([1000,2000])
    rows=[{'slot_ms':int(t),'available_ms':int(t),'flow':.1,'funding':.001,'oi_change':-.2,'source_hash':'a'*64,'funding_event_ms':int(t)-1,'funding_interval_ms':28800000,'oi_event_ms':int(t)-1,'oi_previous_ms':int(t)-2} for t in times]
    x=information_panel(times,np.zeros((2,5)),rows,quote='USDC')
    assert x[0,3]==-.2
    with pytest.raises(ValueError):information_panel(times,np.zeros((2,5)),rows[:1],quote='USDC')
    with pytest.raises(ValueError):information_panel(times,np.zeros((2,5)),[{**r,'available_ms':9999999} for r in rows],quote='USDC')


def test_information_fit_uses_training_scale_only():
    x=np.arange(80,dtype=float).reshape(20,4)/100;y=x[:,0]*.01
    a=fit_information(x[:10],y[:10],x[10:11])
    b=fit_information(x[:10],y[:10],np.r_[x[10:11],x[11:]*1000])
    assert a[0]==pytest.approx(b[0])
