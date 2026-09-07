from datetime import datetime, timezone
import pytest
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from services.research.btc_forward_observation import COLUMNS, DAY, MINUTE, freeze_models, record_forecast, record_outcome
from tools import btc_online_factory as online


def at(value):return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()*1000)


def test_month_waiting_no_backfill_and_immutable_restart(tmp_path):
    now=at('2026-09-07')
    fits={name:{'mean':[0.]*len(cols),'scale':[1.]*len(cols),'beta':[0.]*(len(cols)+1),
                'train_n':180,'latest_label_ms':now-DAY} for name,cols in COLUMNS.items()}
    with RealtimeAlphaShadowStore(str(tmp_path/'store.db'),create=True) as store:
        ids=freeze_models(store,fits,source_hash='a'*64,now_ms=now-DAY)
        online.enable(tmp_path,'b'*64,now)
        assert online.prepare_month(tmp_path,store,ids,'b'*64,now+6*MINUTE)==([],[])
        months,groups=online.prepare_month(tmp_path,store,ids,'b'*64,now+MINUTE)
        assert len(months)==1 and months[0]['status']=='WAITING_TRAINING' and groups==[]
        before=(tmp_path/'months/2026-09.json').read_bytes()
        assert online.prepare_month(tmp_path,store,ids,'b'*64,now+DAY+MINUTE)==(months,groups)
        assert (tmp_path/'months/2026-09.json').read_bytes()==before
        online.record_factory_day(tmp_path,store,months,groups,now+MINUTE,source_hash='d'*64)
        online.record_factory_day(tmp_path,store,months,groups,now+2*MINUTE,source_hash='d'*64)
        report=online.report(tmp_path,store,ids,'b'*64)
        assert report['factory_decisions']==1 and report['latest_decision']['selected']=='CASH'
        assert report['latest_decision']['target_weight']==0
        with pytest.raises(ValueError,match='freeze changed'):online.versions(tmp_path,'c'*64)


def test_real_store_monthly_refit_version_restart_and_old_pending_labels(tmp_path):
    start=at('2026-01-01');now=at('2026-08-01')
    fits={name:{'mean':[0.]*len(cols),'scale':[1.]*len(cols),'beta':[0.]*(len(cols)+1),
                'train_n':180,'latest_label_ms':start-DAY} for name,cols in COLUMNS.items()}
    with RealtimeAlphaShadowStore(str(tmp_path/'store.db'),create=True) as store:
        ids=freeze_models(store,fits,source_hash='a'*64,now_ms=start-DAY)
        for i in range(180):
            day=start+i*DAY;entry=day+5*MINUTE;exit=entry+DAY
            record_forecast(store,ids['PRICE'],features=[i/100,i%7,i%11,i%3,i%5],
                            information_end_ms=day,available_at_ms=day+MINUTE,now_ms=day+MINUTE,source_hash='b'*64)
            decision=store.decisions(ids['PRICE'])[-1]['payload']
            record_outcome(store,ids['PRICE'],decision,
                entry={'event_ms':entry,'available_at_ms':entry+MINUTE,'open':100},
                exit={'event_ms':exit,'available_at_ms':exit+MINUTE,'open':100+i/100},now_ms=exit+MINUTE)
        online.enable(tmp_path,'c'*64,now)
        months,groups=online.prepare_month(tmp_path,store,ids,'c'*64,now+MINUTE)
        assert months[0]['status']=='READY' and months[0]['training_n']==180
        assert months[0]['selection']['selected']=='CASH' and months[0]['selection']['n']==0
        old_ids=groups[0][1]
        decision_day=at('2026-08-31')
        for candidate in old_ids.values():
            record_forecast(store,candidate,features=[1,2,3,4,5],information_end_ms=decision_day,
                            available_at_ms=decision_day+MINUTE,now_ms=decision_day+MINUTE,source_hash='d'*64)
        online.record_factory_day(tmp_path,store,months,groups,decision_day+2*MINUTE,source_hash='d'*64)
    with RealtimeAlphaShadowStore(str(tmp_path/'store.db')) as store:
        later=at('2026-09-01')
        months,groups=online.prepare_month(tmp_path,store,ids,'c'*64,later+MINUTE)
        assert len(groups)==2 and groups[0][1]==old_ids and groups[1][1]!=old_ids
        assert months[1]['selection']['n']==0
        for candidate in old_ids.values():
            decision=store.pending(candidate)[0]['payload'];entry=decision['entry_time_ms'];exit=entry+DAY
            record_outcome(store,candidate,decision,
                entry={'event_ms':entry,'available_at_ms':entry+MINUTE,'open':100},
                exit={'event_ms':exit,'available_at_ms':exit+MINUTE,'open':101},now_ms=exit+MINUTE)
        rows=online._selection_rows(store,groups)
        assert len(rows)==1 and rows[0]['available_at_ms']==later+6*MINUTE
        assert online.prepare_month(tmp_path,store,ids,'c'*64,later+DAY+MINUTE)==(months,groups)
        assert store.chain_valid()


def test_month_not_published_when_training_overruns_window(tmp_path):
    now=at('2026-09-07')
    fits={name:{'mean':[0.]*len(cols),'scale':[1.]*len(cols),'beta':[0.]*(len(cols)+1),
                'train_n':180,'latest_label_ms':now-DAY} for name,cols in COLUMNS.items()}
    with RealtimeAlphaShadowStore(str(tmp_path/'store.db'),create=True) as store:
        ids=freeze_models(store,fits,source_hash='a'*64,now_ms=now-DAY)
        online.enable(tmp_path,'b'*64,now)
        with pytest.raises(ValueError,match='missed'):
            online.prepare_month(tmp_path,store,ids,'b'*64,now+MINUTE,clock=lambda:now+5*MINUTE)
        assert not list((tmp_path/'months').glob('*.json'))


def test_online_rules_cannot_silently_change_after_enable(tmp_path,monkeypatch):
    online.enable(tmp_path,'a'*64,at('2026-09-07'))
    monkeypatch.setattr(online,'implementation_hash',lambda:'f'*64)
    with pytest.raises(ValueError,match='implementation changed'):
        online.versions(tmp_path,'a'*64)
