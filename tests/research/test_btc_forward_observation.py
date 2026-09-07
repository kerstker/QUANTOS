import pytest
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from services.research.btc_forward_observation import freeze_models, record_forecast, record_outcome, observation_summary

DAY=86400000;MINUTE=60000;SLOT=1788825600000


def fits():
    return {name:{'mean':[0.]*n,'scale':[1.]*n,'beta':[.01]+[0.]*n,
                  'train_n':200,'latest_label_ms':SLOT-DAY} for name,n in
            [('PRICE',3),('PRICE_FLOW',4),('PRICE_FUNDING',4),('PRICE_FLOW_FUNDING',5)]}


def test_forecast_before_label_restart_idempotency_and_maturity(tmp_path):
    path=str(tmp_path/'forward.db')
    with RealtimeAlphaShadowStore(path,create=True) as store:
        ids=freeze_models(store,fits(),source_hash='a'*64,now_ms=SLOT-DAY)
        candidate=ids['PRICE']
        args=dict(features=[.1]*5,information_end_ms=SLOT,available_at_ms=SLOT+1000,
                  now_ms=SLOT+2000,source_hash='b'*64)
        assert record_forecast(store,candidate,**args)
        assert not record_forecast(store,candidate,**args)
        decision=store.decisions(candidate)[0]['payload']
        with pytest.raises(ValueError,match='mature'):
            record_outcome(store,candidate,decision,entry={'event_ms':SLOT+5*MINUTE,'available_at_ms':SLOT+6*MINUTE,'open':100},
                           exit={'event_ms':SLOT+DAY+5*MINUTE,'available_at_ms':SLOT+DAY+6*MINUTE,'open':101},now_ms=SLOT+DAY)
    with RealtimeAlphaShadowStore(path) as store:
        assert store.chain_valid()
        args=dict(entry={'event_ms':SLOT+5*MINUTE,'available_at_ms':SLOT+6*MINUTE,'open':100},
                  exit={'event_ms':SLOT+DAY+5*MINUTE,'available_at_ms':SLOT+DAY+6*MINUTE,'open':101},now_ms=SLOT+DAY+7*MINUTE)
        assert record_outcome(store,candidate,decision,**args)
        assert not record_outcome(store,candidate,decision,**args)
        row=observation_summary(store,candidate)
        assert row['completed']==1 and row['pending']==0
        assert row['rmse_bps']==pytest.approx(0,abs=1e-10)
        assert 'net_return' not in store.realizations(candidate)[0]['payload']


@pytest.mark.parametrize('change', [dict(now_ms=SLOT+5*MINUTE),dict(available_at_ms=SLOT+3000),
                                    dict(information_end_ms=SLOT+DAY),dict(features=[float('nan')]*5)])
def test_no_backfill_or_future_or_nonfinite_features(tmp_path,change):
    with RealtimeAlphaShadowStore(str(tmp_path/'forward.db'),create=True) as store:
        ids=freeze_models(store,fits(),source_hash='a'*64,now_ms=SLOT-DAY)
        args=dict(features=[0.]*5,information_end_ms=SLOT,available_at_ms=SLOT+1000,now_ms=SLOT+2000,source_hash='b'*64)
        args.update(change)
        with pytest.raises(ValueError):record_forecast(store,ids['PRICE'],**args)
        assert not store.decisions(ids['PRICE'])


def test_freeze_rejects_immature_model_and_future_decision(tmp_path):
    with RealtimeAlphaShadowStore(str(tmp_path/'forward.db'),create=True) as store:
        bad=fits();bad['PRICE']['latest_label_ms']=SLOT+1
        with pytest.raises(ValueError):freeze_models(store,bad,source_hash='a'*64,now_ms=SLOT)
        assert store.event_count()==0
