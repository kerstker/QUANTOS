from copy import deepcopy

import pytest

from services.research import demo_resident_status as status

NOW = 1800000000000


def report():
    return status.build_status({'status':'WAITING_QUERY_INTERVAL', 'reason':None, 'observed_at_ms':NOW,
        'attempt_count':2, 'last_attempt_at_ms':NOW-1000, 'next_query_at_ms':NOW+29000,
        'reservation_sha256':None, 'query':None, 'resolution':None},
        executor_boot_sha256='a'*64, source_commit='b'*40)


@pytest.mark.parametrize('change', [{'cash_reconciled':True}, {'attempt_count':True}, {'attempt_count':0},
    {'next_query_at_ms':NOW+1}, {'last_attempt_at_ms':NOW+1}, {'query_status':'ORDER_SNAPSHOTS_OBSERVED'},
    {'phase':'LIVE_READY'}, {'extra':'field'}, {'resolution_issues':['untrusted detail']}, {'attempt_count':-1}])
def test_display_rejects_bad_schedule_and_authority(change):
    value = deepcopy(report()); value.update(change)
    with pytest.raises(ValueError): status.validate(value)


def test_immutable_report_roundtrip_and_original_hash_failure(tmp_path):
    value = report(); path = status.save(tmp_path, value)
    assert status.load_latest(tmp_path) == value
    before = path.read_bytes()
    assert status.save(tmp_path, value) == path and path.read_bytes() == before
    path.write_text('{}')
    with pytest.raises(ValueError): status.load_latest(tmp_path)
