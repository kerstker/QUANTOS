from copy import deepcopy
import pytest
from services.research import demo_functional_status as model

NOW=1789350000000


def report():
    return model.build(phase='OPENING_CASH_CAPTURE',source_commit='c'*40,cycle_sha256='a'*64,
        observed_at_ms=NOW)


def test_incomplete_fills_or_cash_cannot_be_displayed_as_a_completed_cycle():
    result={'orders_attempted':2,'final_quantity':'0','fill_count':2,
        'cash_status':'MATCHED_REPORTED_INPUTS','cash_reconciled':True}
    raw=model.build(phase='DEMO_CYCLE_COMPLETED',source_commit='c'*40,cycle_sha256='a'*64,
        observed_at_ms=NOW,result=result)
    for name,value in [('final_quantity','0.001'),('fill_count',0),('cash_status','UNRESOLVED'),('orders_attempted',0)]:
        changed=deepcopy(raw);changed[name]=value
        with pytest.raises(ValueError):model.validate(changed)


def test_completed_cycle_remains_technical_and_round_trips_without_authority(tmp_path):
    raw=report();model.save(tmp_path,raw)
    assert model.load_latest(tmp_path)==raw
    with pytest.raises(ValueError):model.validate(dict(raw,economic_authority=True))
