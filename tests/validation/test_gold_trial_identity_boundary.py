"""Original archive identities retain their historical canonical bytes."""
import importlib

import pytest


@pytest.mark.parametrize('fields,expected', [
    (dict(input_sha256='a' * 64, candidate='gold_trend', source={'commit': 'b' * 40, 'note': '金'}),
     'f7749dbc463fa9162de0314a10a24d4c7054440cf7b8c0bb8ec17c347bdbf7a9'),
    (dict(input_sha256='c' * 64, candidate='gold_reversion', source={'z': 1, 'a': {'quantity': '0.40'}}),
     '0f955550cca69b03fcbcdfaf66a4302c26a9788084c1f749e08525364266c7f6'),
])
def test_original_archive_hash_preimage_is_unchanged(fields, expected):
    from tools import gold_bar_experiment as native
    module = importlib.import_module('quantos.contracts.gold_trial_identity')
    assert module.gold_trial_definition_id(**fields) == native._hash(fields) == expected
    assert native.gold_trial_definition_id is module.gold_trial_definition_id
