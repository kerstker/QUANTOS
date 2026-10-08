import json
import pytest
from tests.research.test_gold_trade_observation import document
from services.research.gold_history_view import project_history


def archive():
 return dict(schema='GOLD_ACCOUNT_BACKFILL_V1',status='QUERIED',start_ms=1000,end_ms=2000,observed_at_ms=3000,
  completed_tasks=4,total_tasks=4,fills=document(),income=[],orders=[],algos=[],failures=[],
  history_complete=False,order_lifecycle_complete=False,net_pnl=None,submission_enabled=False)


def test_history_view_redacts_identity_and_keeps_scope_and_cash_separate():
 result=project_history(archive(),now_ms=4000)
 assert result['trade_count']==1 and len(result['trades'])==1
 assert result['daily'][0]['buy_count']==1
 assert result['history_complete'] is False
 assert all(s not in json.dumps(result) for s in ('trade_id','account_ref','order_id'))


def test_history_rejects_false_completeness_or_future_observations():
 d=archive();d['history_complete']=True
 with pytest.raises(ValueError):project_history(d,now_ms=4000)
 with pytest.raises(ValueError):project_history(archive(),now_ms=2000)
