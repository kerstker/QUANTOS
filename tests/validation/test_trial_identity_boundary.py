"""Independent readers and the native ledger must share exact event identities."""
from dataclasses import asdict
import hashlib

import pytest

from services.research.trial_ledger import TrialLedger, TrialRecord


def test_original_persisted_trial_hash_vector_is_unchanged():
    from quantos.contracts.trial_identity import trial_event_hash
    record = dict(trial_id='1'*64, alpha_id='2'*64, family_id='3'*64,
        exact_definition_id='4'*64, validation_policy_digest='5'*64,
        status='PLANNED', event_index=0, at=123)
    assert trial_event_hash(record, global_seq=1, previous_hash='', global_previous_hash='') == (
        '0289941199376da89e63d73df5d67f1d84c946c779dc07101490f0eb5358a4f9')


def test_native_interleaved_trials_and_read_only_reopen_share_exact_hashes(tmp_path):
    from quantos.contracts.trial_identity import trial_event_hash
    path = tmp_path / 'trials.db'
    events = []
    with TrialLedger(str(path)) as ledger:
        for index, status in enumerate(('PLANNED', 'RUNNING', 'FAILED')):
            for n in (1, 2):
                record = TrialRecord(str(n)*64, '3'*64, '4'*64, '5'*64, '6'*64,
                                     status, index, 100+index)
                events.append(ledger.append(record).events[-1])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    previous = ''
    for event in events:
        value = asdict(event)
        seq, prev, expected = (value.pop(k) for k in ('global_seq', 'prev_hash', 'event_hash'))
        assert trial_event_hash(value, global_seq=seq, previous_hash=prev,
                                global_previous_hash=previous) == expected
        previous = expected
    with TrialLedger(str(path), create=False, read_only=True) as ledger:
        assert ledger.verify()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('field,value', [('at', True), ('event_index', False), ('status', 1)])
def test_typed_aliases_do_not_get_a_trial_identity(field, value):
    from quantos.contracts.trial_identity import trial_event_hash
    record = dict(trial_id='1'*64, alpha_id='2'*64, family_id='3'*64,
        exact_definition_id='4'*64, validation_policy_digest='5'*64,
        status='PLANNED', event_index=0, at=123)
    record[field] = value
    with pytest.raises(ValueError):
        trial_event_hash(record, global_seq=1, previous_hash='', global_previous_hash='')
