"""Native V2 reviews retain every original check inside one maintenance operation."""
import json

import pytest

from tests.tools.test_gold_execution_delegation_v2_install import (
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, local as local,
    scope_records as scope_records, native_scope as native_scope,
    native_install as native_install, budget_case as budget_case,
    signed_native as signed_native, v2_installation as v2_installation,
)


def operation(f, mode):
    if mode == 'register':
        return f['install'], f['args'], 'DELEGATION_REGISTERED_CONSUMER_DISABLED'
    f['install'].install_approved(**f['args'])
    return f['enable'], dict(source_commit=f['args']['source_commit'],
        delegation_sha256=f['signed']['delegation_sha256']), 'DELEGATION_ENABLED_REQUIRES_ORIGINAL_GATES'


@pytest.mark.parametrize('mode,minimum_checks', [('register', 8), ('enable', 25)])
def test_one_original_operation_replays_definition_once(v2_installation, monkeypatch, mode, minimum_checks):
    from tools.gold_bar_definition import PreparedBarDefinition
    f = v2_installation
    module, args, status = operation(f, mode)
    invoke = module.install_approved if mode == 'register' else module.enable_approved
    before = f['case']['tool'].issuer_rows(f['case']['registry'])
    builds, checks, confirmations = [], [], []
    initialize, snapshot, confirm = PreparedBarDefinition.__init__, PreparedBarDefinition._snapshot, module.confirmation

    def build(self, *args, **kwargs):
        builds.append((args, kwargs))
        return initialize(self, *args, **kwargs)

    def check(self):
        checks.append(True)
        return snapshot(self)

    def confirmation(*args):
        confirmations.append(True)
        return confirm(*args)

    monkeypatch.setattr(PreparedBarDefinition, '__init__', build)
    monkeypatch.setattr(PreparedBarDefinition, '_snapshot', check)
    monkeypatch.setattr(module, 'confirmation', confirmation)
    result = invoke(**args)
    assert result['status'] == status
    assert result['submission_enabled'] is False and result['economic_authority'] is False
    assert f['case']['tool'].issuer_rows(f['case']['registry']) == before
    assert confirmations == [True]
    assert len(checks) >= minimum_checks, 'Every original use must reread its complete snapshot.'
    assert len(builds) == 1, f'{mode} rebuilt the full native definition {len(builds)} times'


@pytest.mark.parametrize('mode,moment,damage', [
    ('register', 'confirmation', 'source'),
    ('register', 'candidate.json', 'archive'),
    ('register', 'registration.json', 'trials'),
    ('enable', 'confirmation', 'source'),
    ('enable', 'enable.json', 'archive'),
    ('enable', 'complete.json', 'trials'),
])
def test_same_operation_still_rejects_changed_original_bytes(
        v2_installation, monkeypatch, mode, moment, damage):
    f = v2_installation
    module, args, _ = operation(f, mode)
    invoke = module.install_approved if mode == 'register' else module.enable_approved
    runtime = json.loads((f['tool'].supervisor.STATE / 'strategy-runtime.json').read_bytes())
    root = f['case']['local']['root']
    trial = root / 'strategy-bar-trials' / runtime['trial_manifest_sha256']
    source = root / 'strategy-bar-sources' / runtime['archive_source_commit']
    paths = dict(source=next((source / 'services').rglob('*.py')),
                 archive=trial / 'input.json', trials=trial / 'trials.db')
    mutations = []

    def mutate():
        path = paths[damage]
        path.write_bytes(path.read_bytes() + b'\n')
        mutations.append(path)

    if moment == 'confirmation':
        original = module.confirmation

        def confirmation(*args):
            result = original(*args)
            mutate()
            return result

        monkeypatch.setattr(module, 'confirmation', confirmation)
    else:
        original = module.installer.write_new

        def write(path, body, **kwargs):
            result = original(path, body, **kwargs)
            if path.name == moment:
                mutate()
            return result

        monkeypatch.setattr(module.installer, 'write_new', write)
    with pytest.raises(ValueError):
        invoke(**args)
    assert mutations == [paths[damage]], 'The real retained bytes must change before rejection.'
    if mode == 'enable':
        with pytest.raises((ValueError, OSError)):
            f['enable'].contract.load_enabled(registry=f['case']['registry'],
                source_commit=args['source_commit'], account_ref_sha256=f['case']['scope']['account_ref_sha256'],
                now_ms=f['case']['local']['clock'][0], role='controller')
    else:
        with pytest.raises((ValueError, OSError)):
            f['enable']._registration(args['source_commit'], f['signed']['delegation_sha256'])


def test_cached_definition_cannot_cross_runtime_identity(v2_installation, monkeypatch):
    f = v2_installation
    module, args, _ = operation(f, 'enable')
    path = f['tool'].supervisor.STATE / 'strategy-runtime.json'
    before = path.read_bytes()
    original = module.confirmation

    def confirmation(*args):
        result = original(*args)
        runtime = json.loads(before)
        runtime['candidate'] = 'trend' if runtime['candidate'] != 'trend' else 'channel'
        path.write_text(json.dumps(runtime, sort_keys=True))
        return result

    monkeypatch.setattr(module, 'confirmation', confirmation)
    with pytest.raises(ValueError):
        module.enable_approved(**args)
    assert path.read_bytes() != before
    assert not module.contract.PUBLIC_ROOT.exists()
