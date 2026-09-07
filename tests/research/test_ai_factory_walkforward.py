import numpy as np
from services.research.ai_factory_walkforward import folds


def test_walkforward_never_trains_on_current_or_later_fold_labels():
    t=np.arange(1200)*3600000;available=t+4*3600000+300000
    result=folds(t,available)
    assert len(result)==3
    for train,valid,test in result:
        assert available[train].max()<t[valid].min()
        assert available[valid].max()<t[test].min()
        assert set(train).isdisjoint(test) and len(test)>30
