from concurrent.futures import ThreadPoolExecutor

import pytest

from services.research.ai_factory_store import FactoryStore, encode, digest
from tests.research.test_gold_factory_campaign import policy


def config(cid, raw=b'same', **extra):
    return {**policy(raw), 'campaign_id': cid, 'max_cycles': 1, **extra}


def test_renaming_campaign_or_input_alias_does_not_reset_choices(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(config(1), operator='test', now=0)
        store.create_campaign(config(2, input_id=99, objective='renamed objective'), operator='test', now=0)
        first = store.reserve_campaign(1, now=0)
        assert store.reserve_campaign(2, now=0) is None
        store.record_trial(first['attempt'], 0, {'candidate_id': 2, 'status': 'FAILED'})
        store.finish_campaign_round(1, 0, 'FAILED', now=1)
        assert store.reserve_campaign(2, now=2) is None
        second = store.reserve_campaign(2, now=3600001)
        assert store.get(second['attempt'])['config']['remaining_candidates'] == [0, 1, 3, 4, 5]
        store.verify_campaigns()


def test_unknown_failed_rounds_count_across_campaign_ids(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        for cid in range(1, 8):
            store.create_campaign(config(cid), operator='test', now=0)
            row = store.reserve_campaign(cid, now=cid*3600000)
            if cid <= 6:
                assert row is not None
                store.finish_campaign_round(cid, 0, 'FAILED', now=cid*3600000)
            else:
                assert row is None
        assert len(store.campaign_snapshot()['rounds']) == 6
        assert store.campaign_snapshot()['campaigns'][0]['state'] == 'EXHAUSTED'


def test_different_input_hash_can_reserve_independently(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        for cid, raw in ((1, b'a'), (2, b'b')):
            store.create_campaign(config(cid, raw), operator='test', now=0)
            row = store.reserve_campaign(cid, now=0)
            assert store.get(row['attempt'])['config']['remaining_candidates'] == list(range(6))


def test_two_connections_cannot_reserve_same_input_concurrently(tmp_path):
    path = tmp_path/'factory.db'
    with FactoryStore(path) as store:
        for cid in (1, 2):
            store.create_campaign(config(cid), operator='test', now=0)
    def reserve(cid):
        with FactoryStore(path) as store:
            return store.reserve_campaign(cid, now=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(reserve, (1, 2)))
    assert sum(row is not None for row in rows) == 1


def test_rehashed_session_cannot_reset_global_exclusions(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        for cid in (1, 2):
            store.create_campaign(config(cid), operator='test', now=0)
        first = store.reserve_campaign(1, now=0)
        store.record_trial(first['attempt'], 0, {'candidate_id': 0, 'status': 'FAILED'})
        store.finish_campaign_round(1, 0, 'FAILED', now=0)
        second = store.reserve_campaign(2, now=3600000)
        changed = store.get(second['attempt'])['config']
        changed['remaining_candidates'] = list(range(6))
        with store.c:
            store.c.execute('UPDATE sessions SET config=?,config_hash=? WHERE attempt=?',
                            (encode(changed), digest(changed), second['attempt']))
        with pytest.raises(ValueError, match='binding'):
            store.verify_campaigns()
