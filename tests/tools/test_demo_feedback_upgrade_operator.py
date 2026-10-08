"""Local human confirmation and saved-operation recovery; no host actions."""
from contextlib import contextmanager
import base64
import importlib
import io
import json
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_feedback_upgrade import materials


def test_feedback_upgrade_has_distinct_local_operator_entrypoint():
    module = importlib.import_module('tools.demo_feedback_upgrade_operator')
    assert callable(module.main)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    op = importlib.import_module('tools.demo_feedback_upgrade_operator')
    calls = []
    state = {'operator': 'fixture-human', 'fail': None, 'phase': 'PREPARED', 'review': None}
    monkeypatch.setattr(op, 'ROOT_PARENT', tmp_path)
    monkeypatch.setattr(op, 'local_operator', lambda: state['operator'])
    monkeypatch.setattr(op.maintenance, '_admit', lambda source: calls.append('source'))

    @contextmanager
    def exclusive():
        calls.append('lock')
        yield
    monkeypatch.setattr(op.maintenance, 'exclusive', exclusive)
    monkeypatch.setattr(op.supervisor, 'controller_lease', lambda **kw: exclusive())

    def prepare_window(root, operation):
        calls.append('prepare-window')
        root.mkdir(mode=0o700)
        op.write_new(root / 'window.json', b'{}')
        op.write_new(root / 'services.json', op.canonical({'units': {
            op.services.UNITS[0]: {'active': 'active'},
            op.services.UNITS[-1]: {'active': 'inactive'},
        }}))
    monkeypatch.setattr(op.window, 'prepare', prepare_window)
    monkeypatch.setattr(op.window, 'pause', lambda root: calls.append('pause'))
    def restore(root):
        calls.append('restore')
        state['restored'] = True
    monkeypatch.setattr(op.window, 'restore', restore)
    monkeypatch.setattr(op, '_canceled_state', lambda *a: 'RESTORED' if state.get('restored') else None)
    def check_unprepared(*args):
        assert state.get('restored') is True
    monkeypatch.setattr(op, '_unprepared_restored', check_unprepared)
    monkeypatch.setattr(op.services, 'inspect_journal', lambda path: json.loads(path.read_bytes()))
    monkeypatch.setattr(op.files_tx, 'capture', lambda: {'fixture': 'fixed-four-files'})
    monkeypatch.setattr(op, 'capture_consumer', lambda **kw: {'fixture': 'native-consumer'})

    def prepare(root, **kw):
        calls.append('prepare-upgrade')
        review = {'schema': op.transaction.SCHEMA, 'source_commit': kw['source_commit'],
                  'operation_sha256': kw['operation_sha256'],
                  'files_base64': {n: base64.b64encode(b).decode() for n, b in kw['files'].items()},
                  'original_journal': json.loads((root / 'services.json').read_bytes()),
                  'approved_active': sorted(kw['approved_active']),
                  'consumer_preflight': {'observed_at_ms': op._now()},
                  'recovery_actions': ['continue', 'rollback_to_original']}
        area = root / op.transaction.AREA
        area.mkdir(mode=0o700)
        op.write_new(area / 'review.json', op.canonical(review))
        state['review'] = review
        result = dict(review_sha256=op.transaction._digest(review), source_commit=kw['source_commit'],
                      operation_sha256=kw['operation_sha256'], approved_active=review['approved_active'],
                      original_active=[op.services.UNITS[0]], old_scope=list(op.services.UNITS),
                      new_scope=list(op.services.ALLOWED_UNITS), targets={},
                      recovery_actions=review['recovery_actions'])
        if state['fail'] == 'after-native-review':
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(op.transaction, 'prepare', prepare)

    def advance(root, **kw):
        calls.append(kw['action'])
        saved = json.loads((root.parent / 'approval.json').read_bytes())
        assert saved == kw['approval']
        assert saved['review_sha256'] == kw['expected_digest']
        assert saved['purpose'] == op.transaction.PURPOSE
        assert saved['operator_id'] == 'fixture-human'
        if state['fail'] == 'apply' and kw['action'] == 'apply':
            raise RuntimeError('private fixture detail')
        state['phase'] = 'COMPLETED' if kw['action'] == 'apply' else 'ROLLED_BACK'
        return {'status': state['phase']}
    monkeypatch.setattr(op.transaction, 'advance', advance)
    monkeypatch.setattr(op.transaction, 'query', lambda *a, **kw: {'status': state['phase']})

    def cancel(*a, **kw):
        calls.append('cancel')
        state['phase'] = 'CANCELED_UNCHANGED'
        return {'status': state['phase']}
    monkeypatch.setattr(op.transaction, 'cancel_prepared', cancel)
    return SimpleNamespace(op=op, calls=calls, state=state, root=tmp_path)


def start(f, confirm, *, activate=False):
    return f.op.start(source_commit='a' * 40, files=materials(),
                      activate_feedback_timer=activate, confirm=confirm)


def test_decline_cancels_unchanged_transaction_and_restores_without_approval(runtime):
    result = start(runtime, lambda review, operator: False)
    assert result['status'] == 'UPGRADE_DECLINED' and result['services_restored']
    assert runtime.calls[-2:] == ['lock', 'restore']
    assert 'cancel' in runtime.calls and 'apply' not in runtime.calls
    assert not list(runtime.root.glob('*/approval.json'))


def test_approval_is_persisted_before_native_transition(runtime):
    seen = []
    result = start(runtime, lambda review, operator: seen.append(review) or True)
    assert result['status'] == 'COMPLETED' and result['services_restored']
    assert seen[0]['approved_active'] == [runtime.op.services.UNITS[0]]
    assert seen[0]['recovery_actions'] == ['continue', 'rollback_to_original']
    assert 'restore' not in runtime.calls
    saved = json.loads(next(runtime.root.glob('*/approval.json')).read_bytes())
    assert saved['service_activation_authorized'] is True
    assert saved['submission_enabled'] is False and saved['economic_authority'] is False


def test_new_timer_requires_explicit_activation_choice(runtime):
    result = start(runtime, lambda *a: True, activate=True)
    assert result['status'] == 'COMPLETED'
    assert set(runtime.state['review']['approved_active']) == {
        runtime.op.services.UNITS[0], runtime.op.services.FEEDBACK_UNITS[-1]}


def test_partial_apply_retains_approval_and_never_calls_ordinary_restore(runtime):
    runtime.state['fail'] = 'apply'
    result = start(runtime, lambda *a: True)
    assert result['status'] == 'RECOVERY_REQUIRED' and not result['services_restored']
    assert 'restore' not in runtime.calls
    assert list(runtime.root.glob('*/approval.json'))
    assert 'private' not in json.dumps(result)


def test_operation_identity_is_visible_before_an_interrupted_action(runtime, capsys):
    runtime.state['fail'] = 'apply'
    result = start(runtime, lambda *a: True)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]['operation_id'] == result['operation_id']
    assert records[0]['status'] == 'PREPARING_WINDOW'


@pytest.mark.parametrize('action,expected', [('rollback', 'ROLLED_BACK'), ('apply', 'COMPLETED')])
def test_recovery_uses_original_saved_approval_without_new_confirmation(runtime, action, expected):
    runtime.state['fail'] = 'apply'
    failed = start(runtime, lambda *a: True)
    approval_path = next(runtime.root.glob('*/approval.json'))
    before = approval_path.read_bytes()
    runtime.state['fail'] = None
    runtime.calls.clear()
    result = runtime.op.recover(source_commit='a' * 40, operation=failed['operation_id'], action=action)
    assert result['status'] == expected and result['services_restored']
    assert approval_path.read_bytes() == before
    assert action in runtime.calls and 'prepare-window' not in runtime.calls and 'restore' not in runtime.calls


def test_unapproved_native_review_survives_lost_outer_receipt(runtime):
    runtime.state['fail'] = 'after-native-review'
    failed = start(runtime, lambda *a: pytest.fail('no confirmation before native prepare returns'))
    assert failed['status'] == 'RECOVERY_REQUIRED'
    runtime.calls.clear()
    result = runtime.op.recover(source_commit='a' * 40, operation=failed['operation_id'], action='rollback')
    assert result['status'] == 'CANCELED_UNCHANGED' and result['services_restored']
    assert 'cancel' in runtime.calls and 'restore' in runtime.calls
    assert not list(runtime.root.glob('*/approval.json'))


@pytest.mark.parametrize('change', ['source', 'operator', 'review'])
def test_recovery_binding_changes_reject_before_native_actions(runtime, change):
    runtime.state['fail'] = 'apply'
    failed = start(runtime, lambda *a: True)
    source = 'a' * 40
    if change == 'source':
        source = 'b' * 40
    elif change == 'operator':
        runtime.state['operator'] = 'different-human'
    else:
        path = next(runtime.root.glob('*/window/feedback-upgrade/review.json'))
        doc = json.loads(path.read_bytes()); doc['approved_active'] = []
        path.write_bytes(runtime.op.canonical(doc))
    runtime.calls.clear()
    with pytest.raises(ValueError, match='FEEDBACK_OPERATOR_'):
        runtime.op.recover(source_commit=source, operation=failed['operation_id'], action='rollback')
    assert not {'apply', 'rollback', 'cancel', 'restore'} & set(runtime.calls)


def test_identity_change_during_confirmation_cannot_create_approval(runtime):
    def confirm(*args):
        runtime.state['operator'] = 'changed-human'
        return True
    result = start(runtime, confirm)
    assert result['status'] == 'RECOVERY_REQUIRED'
    assert not list(runtime.root.glob('*/approval.json')) and 'apply' not in runtime.calls


@pytest.mark.parametrize('text', ['ARCHIVE\n', 'UPGRADE FEEDBACK ' + 'b' * 64 + '\n', ''])
def test_confirmation_cannot_reuse_archive_or_another_review(runtime, monkeypatch, text):
    review = {'review_sha256': 'a' * 64, 'source_commit': 'a' * 40,
              'operation_sha256': 'c' * 64, 'old_scope': [], 'new_scope': [],
              'original_active': [], 'approved_active': [], 'targets': {},
              'recovery_actions': ['continue', 'rollback_to_original']}
    monkeypatch.setattr(runtime.op, 'sys', SimpleNamespace(stdin=io.StringIO(text)))
    monkeypatch.setattr(runtime.op, 'select', SimpleNamespace(select=lambda *a: ([1], [], [])))
    assert runtime.op.confirm_upgrade(review, 'fixture-human') is False


def test_source_or_tty_admission_failure_precedes_window_and_private_writes(runtime, monkeypatch):
    def denied():
        raise ValueError('FEEDBACK_OPERATOR_LOCAL_REQUIRED')
    monkeypatch.setattr(runtime.op, 'local_operator', denied)
    with pytest.raises(ValueError, match='LOCAL_REQUIRED'):
        start(runtime, lambda *a: True)
    assert list(runtime.root.iterdir()) == [] and not runtime.calls
