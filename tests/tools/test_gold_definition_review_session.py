"""One local prepared definition, with every original review boundary retained."""
import json

import pytest

from tests.tools import test_gold_execution_delegation_enable as native

enabled_case = native.enabled_case
installation = native.installation
case = native.case
local = native.local
original_evidence = native.original_evidence


def test_one_locked_enable_does_not_repeat_the_full_native_replay(enabled_case, monkeypatch):
    from tools.gold_bar_definition import PreparedBarDefinition
    builds = []; checks = []
    initialize = PreparedBarDefinition.__init__; snapshot = PreparedBarDefinition._snapshot

    def build(self, *args, **kwargs):
        builds.append(True)
        return initialize(self, *args, **kwargs)

    def check(self):
        checks.append(True)
        return snapshot(self)

    monkeypatch.setattr(PreparedBarDefinition, '__init__', build)
    monkeypatch.setattr(PreparedBarDefinition, '_snapshot', check)
    result = enabled_case.tool.enable_approved(**enabled_case.args)
    assert result['status'] == 'DELEGATION_ENABLED_REQUIRES_ORIGINAL_GATES'
    assert len(enabled_case.confirmations) == 1
    assert len(checks) >= 25, 'Every original definition use must still recheck its complete bytes.'
    assert len(builds) == 1, f'Repeated full native replay inside one locked enable: {len(builds)}'


@pytest.mark.parametrize('moment,damage', [
    ('confirmation', 'source'),
    ('enable.json', 'archive'),
    ('complete.json', 'trials'),
])
def test_reusing_prepared_math_never_reuses_a_previous_original_byte_check(
        enabled_case, monkeypatch, moment, damage):
    f = enabled_case
    runtime = json.loads((f.value.controller / 'strategy-runtime.json').read_bytes())
    trial = f.value.executor / 'strategy-bar-trials' / runtime['trial_manifest_sha256']
    source = f.value.executor / 'strategy-bar-sources' / runtime['archive_source_commit']
    paths = {'source': next((source / 'services').rglob('*.py')), 'archive': trial / 'input.json',
             'trials': trial / 'trials.db'}
    mutations = []
    # Bytes change after the initial original replay, inside the same operation.
    def mutate():
        path = paths[damage]
        path.write_bytes(path.read_bytes() + b'\n')
        mutations.append(path)

    if moment == 'confirmation':
        original = f.tool.confirmation
        def confirmation(*args):
            result = original(*args); mutate(); return result
        monkeypatch.setattr(f.tool, 'confirmation', confirmation)
    else:
        original = f.tool.installer.write_new
        def write(path, body, **kwargs):
            result = original(path, body, **kwargs)
            if path.name == moment:
                mutate()
            return result
        monkeypatch.setattr(f.tool.installer, 'write_new', write)
    with pytest.raises(ValueError):
        f.tool.enable_approved(**f.args)
    assert mutations == [paths[damage]], 'The original bytes must actually change, not fail a fixture path.'
    with pytest.raises((ValueError, OSError)):
        native.read(f)
