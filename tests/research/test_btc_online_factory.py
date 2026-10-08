import numpy as np
import pytest
from services.research.btc_forward_observation import online_month_fit, online_month_selection


def sample(i):
    return {'entry_time_ms':i*86400000,'available_at_ms':(i+2)*86400000,
            'features':[i/100, i%7, i%11, i%3, i%5], 'label':i/10000,
            'predictions':{name:i/10000 for name in ('PRICE','PRICE_FLOW','PRICE_FUNDING','PRICE_FLOW_FUNDING')}}


def test_training_excludes_late_received_and_future_labels():
    rows=[sample(i) for i in range(181)]
    cutoff=181*86400000
    fits=online_month_fit(rows,cutoff)
    assert fits['PRICE']['train_n']==180
    assert fits==online_month_fit(rows+[sample(999)],cutoff)
    rows[179]['available_at_ms']=cutoff+1
    assert online_month_fit(rows,cutoff)=={}


def test_training_recovers_linear_signal_without_future_rows():
    fit=online_month_fit([sample(i) for i in range(180)],200*86400000)['PRICE']
    x=np.asarray(sample(190)['features'][:3])
    prediction=np.r_[1.,(x-fit['mean'])/fit['scale']]@fit['beta']
    assert prediction==pytest.approx(.019)


def test_selection_needs_common_mature_online_errors_and_fixed_tie_order():
    rows=[sample(i) for i in range(100)]
    choice=online_month_selection(rows,200*86400000)
    assert choice['selected']=='PRICE' and choice['n']==90
    assert online_month_selection(rows[:59],200*86400000)['selected']=='CASH'
    rows[-1]['available_at_ms']=201*86400000
    assert online_month_selection(rows,200*86400000)==online_month_selection(rows[:-1],200*86400000)
    for row in rows:
        row['predictions']={key:10 for key in row['predictions']}
    assert online_month_selection(rows,200*86400000)['selected']=='CASH'


def test_duplicate_training_days_rejected():
    with pytest.raises(ValueError,match='duplicate'):
        online_month_fit([sample(0),sample(0)],200*86400000)
