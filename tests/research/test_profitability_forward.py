import pytest
from services.research.profitability_forward import forward_summary


def forecast(slot=0):
    return {'slot_ms':slot,'issued_at_ms':slot+1,'exit_ms':slot+14400000,
            'prediction':.01,'model_hash':'a'*64}


def test_common_mature_labels_pending_and_gaps_are_separate():
    forecasts=[forecast(0),forecast(7200000)]
    labels=[{'slot_ms':0,'available_at_ms':14400001,'prediction':.01,'realized_return':.02}]
    report=forward_summary(forecasts,labels,as_of_ms=15000000)
    assert report['common_mature_labels']==1
    assert report['missing_forecast_slots']==[3600000]
    assert report['pending_slots']==[7200000]
    assert report['cost_adjusted_pnl'] is None and report['economic_eligible'] is False


def test_forecasts_published_after_outcome_or_changed_predictions_rejected():
    with pytest.raises(ValueError):forward_summary([{**forecast(),'issued_at_ms':15000000}],[],as_of_ms=16000000)
    with pytest.raises(ValueError):forward_summary([forecast()],
        [{'slot_ms':0,'available_at_ms':14400001,'prediction':.02,'realized_return':.02}],as_of_ms=16000000)


def test_forecast_cannot_be_published_after_its_claimed_entry_or_before_input_availability():
    with pytest.raises(ValueError):forward_summary([{**forecast(),'entry_ms':100,'issued_at_ms':101}],[],as_of_ms=1000)
    with pytest.raises(ValueError):forward_summary([{**forecast(),'available_at_ms':2,'issued_at_ms':1}],[],as_of_ms=1000)


def test_existing_label_document_binds_complete_sealed_forecast():
    from services.research.ai_factory_forward import label_document
    from services.research.ai_factory_store import digest
    frozen={**forecast(),'entry_ms':300000}
    frozen['result_hash']=digest(frozen)
    entry={'event_ms':300000,'available_at_ms':360000,'source_hash':'b'*64,'open':100.0}
    exit={'event_ms':14400000,'available_at_ms':14460000,'source_hash':'c'*64,'open':101.0}
    label=label_document(frozen,entry,exit,now=15000000)
    assert label['forecast_hash']!=frozen['result_hash']
    assert forward_summary([frozen],[label],as_of_ms=15000000)['common_mature_labels']==1
    with pytest.raises(ValueError):
        forward_summary([{**frozen,'input_hash':'d'*64}],[label],as_of_ms=15000000)
