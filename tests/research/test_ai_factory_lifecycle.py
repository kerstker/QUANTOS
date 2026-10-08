import pytest
from services.research.ai_factory_lifecycle import model_diagnostics,paired_models


def test_bad_model_requires_mature_history_and_preserves_no_economic_claim():
    labels=[{'slot_ms':i*3600000,'prediction':.02,'realized_return':0.} for i in range(168)]
    assert model_diagnostics(labels[:167])['recommendation']=='KEEP_OBSERVING'
    d=model_diagnostics(labels)
    assert d['recommendation']=='RESEARCH_CHALLENGER' and d['economic_eligible'] is False
    assert d['zero_baseline_rmse']==0 and d['rmse']==pytest.approx(.02)
    with pytest.raises(ValueError):model_diagnostics(labels+[labels[0]])


def test_pair_only_uses_shared_labels_and_rejects_different_truth():
    a=[{'slot_ms':1,'prediction':.1,'realized_return':.2}]
    b=[{'slot_ms':2,'prediction':.2,'realized_return':.3}]
    assert paired_models(a,b)['mse_delta'] is None
    with pytest.raises(ValueError):paired_models(a,[{**a[0],'realized_return':.3}])


def test_drift_trigger_requires_fresh_mature_labels():
    from services.research.ai_factory_lifecycle import research_trigger
    labels=[{'slot_ms':i*3600000,'prediction':.1,'realized_return':0.} for i in range(168)]
    for row in labels:row['available_at_ms']=row['slot_ms']+4*3600000+360000
    now = labels[-1]['available_at_ms']
    policy={'trigger':'drift'};forward={'observed_at_ms':now,'recent_labels':labels}
    assert research_trigger(policy,forward,now=now)['ready']
    assert not research_trigger(policy,forward,now=now+180001)['ready']
    assert not research_trigger(policy,{**forward,'recent_labels':labels[:10]},now=now)['ready']


def test_calibration_reports_actual_errors_and_explicit_zero_direction_exclusion():
    rows=[{'slot_ms':i,'prediction':p,'realized_return':a} for i,(p,a) in enumerate([
        (.01,.02),(-.01,.01),(0.,.01),(.02,0.)])]
    d=model_diagnostics(rows)
    assert d['mae']==pytest.approx(.015)
    assert d['bias']==pytest.approx(-.005)
    assert d['directional_count']==2 and d['directional_accuracy']==.5
    assert sum(b['count'] for b in d['calibration'])==4
    assert d['observations'][0]=={'time':0,'prediction':.01,'actual':.02,'residual':.01}
    assert d['economic_eligible'] is False
    empty=model_diagnostics([])
    assert empty['directional_accuracy'] is None and empty['mae'] is None
    assert empty['calibration']==[] and empty['observations']==[]


@pytest.mark.parametrize('bad',[
    {'slot_ms':-1,'prediction':.1,'realized_return':.2},
    {'slot_ms':1,'prediction':float('nan'),'realized_return':.2},
    {'slot_ms':1,'prediction':1e308,'realized_return':-1e308},
    {'slot_ms':1,'prediction':True,'realized_return':.2},
])
def test_paired_models_rejects_invalid_inputs_even_without_shared_slots(bad):
    with pytest.raises(ValueError):paired_models([bad],[])


def test_pair_rejects_duplicate_slots_instead_of_overwriting_them():
    row={'slot_ms':1,'prediction':.1,'realized_return':.2}
    with pytest.raises(ValueError):paired_models([row,row],[row])


@pytest.mark.parametrize('case', ['future_slot', 'immature_label', 'after_snapshot'])
def test_drift_does_not_schedule_from_labels_unavailable_at_observation(case):
    from services.research.ai_factory_lifecycle import research_trigger
    hour = 3600000
    now = 200 * hour
    observed = now - 1000
    labels = [{'slot_ms': i * hour, 'prediction': .1, 'realized_return': 0.,
               'available_at_ms': (i + 4) * hour + 360000} for i in range(168)]
    if case == 'future_slot':
        labels[-1]['slot_ms'] = now + hour
    elif case == 'immature_label':
        labels[-1]['available_at_ms'] = labels[-1]['slot_ms'] + hour
    else:
        labels[-1]['available_at_ms'] = now
    result = research_trigger({'trigger': 'drift'},
                              {'observed_at_ms': observed, 'recent_labels': labels}, now=now)
    assert result == {'ready': False, 'reason': 'LABEL_TIME_UNVERIFIED', 'key': None}
