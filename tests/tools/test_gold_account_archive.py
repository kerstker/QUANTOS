import json

import pytest

from tests.tools.test_gold_account_chain import setup
from tools.gold_account_chain import advance
from tools import gold_account_archive as archive
from services.research.trial_ledger import TrialLedger


def test_archive_registers_retrospective_reproduction_and_owns_inputs(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    result = archive.archive_review(tmp_path/'chain', tmp_path/'archive')
    assert result['status'] == 'TECHNICAL_REPRODUCTION_REGISTERED'
    assert result['preregistered'] is False and result['economic_eligible'] is False
    assert result['submission_enabled'] is False
    manifest = json.loads((tmp_path/'archive/manifest.json').read_text())
    with TrialLedger(str(tmp_path/'archive/trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        assert ledger.load(result['trial_id']).status == 'PASSED'
    assert manifest['policy']['success'] == 'SAME_ENGINE_REPRODUCTION_ONLY'
    assert (tmp_path/'archive/inputs/000001/inputs.json').read_bytes() == (tmp_path/'chain/000001/inputs.json').read_bytes()
    with pytest.raises(FileExistsError):
        archive.archive_review(tmp_path/'chain', tmp_path/'archive')


def test_reproduction_failure_is_a_failed_trial_with_original_inputs(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    def fail(_):
        raise ValueError('reproduction failed')
    monkeypatch.setattr(archive, 'review_chain', fail)
    with pytest.raises(ValueError, match='reproduction failed'):
        archive.archive_review(tmp_path/'chain', tmp_path/'archive')
    manifest = json.loads((tmp_path/'archive/manifest.json').read_text())
    with TrialLedger(str(tmp_path/'archive/trials.db'), create=False, read_only=True) as ledger:
        assert ledger.load(manifest['trial_id']).status == 'FAILED'
    assert (tmp_path/'archive/inputs/000001/report.json').exists()
    assert not (tmp_path/'archive/review.json').exists()


def test_cannot_archive_into_the_source_chain(tmp_path, monkeypatch):
    args = setup(tmp_path, monkeypatch)
    advance(tmp_path/'chain', tmp_path/'first', **args)
    with pytest.raises(ValueError, match='OVERLAPPING'):
        archive.archive_review(tmp_path/'chain', tmp_path/'chain/archive')
