import hashlib
import json

import pytest

from tests.research.test_gold_cost_runner import setup
from tools.gold_cost_review import review_request


def completed(tmp_path, monkeypatch, *, broken=False):
    registry, queue, dest = setup(tmp_path, monkeypatch, broken=broken)
    uid = queue.submit('gold_cost_replay', {'attempt': 1}, 1, invocation_id='review')
    queue.join()
    assert registry.get(uid).status == ('error' if broken else 'done')
    return dest


def test_completed_trial_has_bound_review_request_without_admission(tmp_path, monkeypatch):
    dest = completed(tmp_path, monkeypatch)
    request = review_request(dest)
    assert request['status'] == 'INDEPENDENT_REVIEW_REQUIRED'
    assert request['economic_eligible'] is request['authoritative'] is False
    assert request['terminal'] == 'PASSED'
    assert request == json.loads((dest/'independent-request.json').read_bytes())
    before = (dest/'trials.db').read_bytes()
    assert review_request(dest) == request
    assert (dest/'trials.db').read_bytes() == before


def test_failed_trial_is_included_in_review_material(tmp_path, monkeypatch):
    dest = completed(tmp_path, monkeypatch, broken=True)
    request = review_request(dest)
    assert request['terminal'] == 'FAILED'
    assert request['economic_eligible'] is False


@pytest.mark.parametrize('name', ['input.json', 'report.json'])
def test_modified_artifact_is_rejected(tmp_path, monkeypatch, name):
    dest = completed(tmp_path, monkeypatch)
    (dest/name).write_bytes(b'{}')
    with pytest.raises(ValueError):
        review_request(dest)


def test_rehashed_report_cannot_retain_old_internal_result_hash(tmp_path, monkeypatch):
    dest = completed(tmp_path, monkeypatch)
    report = json.loads((dest/'report.json').read_bytes())
    report['outcome'] = 'NO_SIGNAL'
    raw = json.dumps(report).encode()
    (dest/'report.json').write_bytes(raw)
    manifest = json.loads((dest/'manifest.json').read_bytes())
    manifest['report_sha256'] = hashlib.sha256(raw).hexdigest()
    (dest/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        review_request(dest)


def test_manifest_cannot_relabel_failed_ledger_as_passed(tmp_path, monkeypatch):
    dest = completed(tmp_path, monkeypatch, broken=True)
    manifest = json.loads((dest/'manifest.json').read_bytes())
    manifest['terminal'] = 'PASSED'
    (dest/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        review_request(dest)


def test_existing_request_is_never_replaced_to_hide_changed_material(tmp_path, monkeypatch):
    dest = completed(tmp_path, monkeypatch)
    path = dest/'independent-request.json'
    path.write_bytes(b'{}')
    with pytest.raises(ValueError, match='REQUEST_CHANGED'):
        review_request(dest)
    assert path.read_bytes() == b'{}'


def test_missing_manifest_is_not_treated_as_a_finished_trial(tmp_path, monkeypatch):
    dest = completed(tmp_path, monkeypatch)
    (dest/'manifest.json').rename(dest/'manifest-interrupted.json')
    with pytest.raises(ValueError):
        review_request(dest)
