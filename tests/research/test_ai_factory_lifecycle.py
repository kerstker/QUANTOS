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
    for row in labels:row['available_at_ms']=1000
    policy={'trigger':'drift'};forward={'observed_at_ms':1000,'recent_labels':labels}
    assert research_trigger(policy,forward,now=1000)['ready']
    assert not research_trigger(policy,forward,now=181001)['ready']
    assert not research_trigger(policy,{**forward,'recent_labels':labels[:10]},now=1000)['ready']
