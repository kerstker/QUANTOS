import pytest
from services.research.ai_factory_store import FactoryStore


def test_input_versions_are_bounded_immutable_and_identical_retry_is_idempotent(tmp_path):
    import sqlite3
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
        store.update(1,'COMPLETED',{})
        data={'quote':'USDC','observations':[]}
        with pytest.raises(ValueError):store.add_profitability_input(1,'basis',data,'test')
        from tests.research.test_profitability_basis_panel import observation
        data['observations']=[observation(3600000)]
        first=store.add_profitability_input(1,'basis',data,'test')
        assert store.add_profitability_input(1,'basis',data,'test')==first
        for slot in (7200000,10800000):store.add_profitability_input(1,'basis',{'quote':'USDC','observations':[observation(slot)]},'test')
        with pytest.raises(ValueError):store.add_profitability_input(1,'basis',{'quote':'USDC','observations':[observation(14400000)]},'test')
        with pytest.raises(sqlite3.IntegrityError):store.c.execute('DELETE FROM profitability_inputs')
    versions=FactoryStore.profitability_inputs(tmp_path/'factory.db',1)
    assert len(versions)==3 and all('data' not in row for row in versions)
    latest=FactoryStore.profitability_inputs(tmp_path/'factory.db',1,include_data=True)
    assert len(latest)==1 and latest[0]['revision']==3
