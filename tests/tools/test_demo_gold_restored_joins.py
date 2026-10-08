"""Original signed consumption and retained mirrors on isolated private copies."""
import importlib
import shutil

import pytest

from tests.tools.test_gold_issuer_executor_join import original_pair as original_pair, original_bytes
from tests.tools.test_gold_pilot_retention import enrolled as enrolled, bundle, original, store, complete, packet, NOW
from tools import demo_namespace_checkpoint as checkpoint


def module():
    return importlib.import_module('tools.demo_gold_restored_joins')


@pytest.fixture
def restored_pair(original_pair, tmp_path):
    f = original_pair
    roots = {name: tmp_path / ('restored-' + name) for name in ('executor', 'issuer')}
    for root in roots.values():
        root.mkdir(mode=0o700)
    for name, key, leaf in [('executor', 'executor_database', 'dispatch.sqlite3'),
            ('issuer', 'issuer_database', 'issuance.sqlite3')]:
        root = roots[name]
        snapshot, _, _ = checkpoint.snapshot_readonly_sqlite(f['arguments'][key], root)
        snapshot.rename(root / leaf)
    shutil.copytree(f['arguments']['evidence_directory'], roots['executor'] / 'evidence')
    public = dict(account_ref_sha256=f['arguments']['account_ref_sha256'], registry=f['registry'])
    return dict(f=f, roots=roots, public=public)


def test_original_signatures_and_reservations_join_from_private_restore_copies_without_mutation(restored_pair):
    f = restored_pair
    before = {name: original_bytes(root) for name, root in f['roots'].items()}
    with module()._grants(f['roots']['executor'], f['roots']['issuer'], f['public']) as joined:
        report = joined.report()
        assert report['issuer_record_count'] == 2 and report['executor_reservation_count'] == 1
        assert len(report['bindings']) == 1 and report['unmatched_reservations'] == []
        assert report['economic_authority'] is report['submission_enabled'] is False
    assert before == {name: original_bytes(root) for name, root in f['roots'].items()}


def test_executor_grant_with_empty_restored_issuer_is_rejected(restored_pair):
    from live.execution.demo_issuer_store import IssuerStore
    f = restored_pair
    path = f['roots']['issuer'] / 'issuance.sqlite3'
    path.unlink()
    IssuerStore(path, create=True).close()
    with pytest.raises(ValueError, match='SIGNED_ANCESTOR'):
        with module()._grants(f['roots']['executor'], f['roots']['issuer'], f['public']):
            pass


def test_signature_without_original_restored_proposal_is_rejected(restored_pair):
    f = restored_pair
    for path in (f['roots']['executor'] / 'evidence').iterdir():
        path.unlink()
    with pytest.raises(FileNotFoundError):
        with module()._grants(f['roots']['executor'], f['roots']['issuer'], f['public']):
            pass


def test_restored_proposal_replacement_invalidates_held_join(restored_pair):
    f = restored_pair
    with pytest.raises(ValueError, match='ORIGINAL_CHANGED'):
        with module()._grants(f['roots']['executor'], f['roots']['issuer'], f['public']) as joined:
            path = next((f['roots']['executor'] / 'evidence').iterdir())
            temporary = path.with_suffix('.replacement')
            temporary.write_bytes(path.read_bytes())
            temporary.chmod(0o600)
            temporary.replace(path)
            joined.recheck()


@pytest.fixture
def retained_pair(enrolled, tmp_path):
    controller = tmp_path / 'retained-controller'
    controller.mkdir(mode=0o700)
    with store(enrolled) as db:
        claim, result = complete(enrolled, db, NOW)
    with store(enrolled, root=controller, role='controller') as db:
        db.adopt(claim)
        db.put(claim, 'result', result)
    public = dict(base_runtime=bundle(), current_runtime=bundle(), source_commit=bundle()['source_commit'],
        account_ref_sha256=enrolled.account, records={})
    return dict(f=enrolled, executor=enrolled.root, controller=controller, original=original(enrolled),
        public=public, claim=claim, result=result)


def test_nonempty_retention_mirror_is_verified_readonly_without_adoption(retained_pair):
    f = retained_pair
    before = original_bytes(f['executor']), original_bytes(f['controller'])
    with module()._retained(f['executor'], f['controller'], public=f['public'], original=f['original']) as handle:
        report = handle['report']
        assert report['executor_checkpoint']['frames'] == report['controller_checkpoint']['frames'] == 1
        assert report['verified_partial'] == []
        assert report['mutation_permitted'] is False
    assert before == (original_bytes(f['executor']), original_bytes(f['controller']))


@pytest.mark.parametrize('feedback', ['absent', 'retained', 'delivered'])
def test_settled_retained_frame_audits_original_feedback_without_rotation(enrolled, tmp_path, feedback):
    from tools.gold_pilot_retention_store import canonical
    from live.execution.demo_batch_risk import _digest
    controller = tmp_path / 'settled-controller'
    controller.mkdir(mode=0o700)
    # Select the test record before its first immutable native write.
    result = packet(enrolled, NOW)
    result['decision'].update(status='STRATEGY_EPISODE_SETTLED', position_flat=True)
    result['decision_sha256'] = _digest(result['decision'])
    if feedback != 'absent':
        result['feedback'] = dict(status='BAR_STRATEGY_FEEDBACK_RETAINED', cash_reconciled=True,
            feedback_sha256='a' * 64, artifact_sha256='b' * 64,
            submission_enabled=False, economic_authority=False)
    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        db.put(claim, 'decision', result['decision'])
        db.put(claim, 'result', result)
    with store(enrolled, root=controller, role='controller') as db:
        db.adopt(claim)
        db.put(claim, 'result', result)
        if feedback == 'delivered':
            db.put(claim, 'delivery', dict(status='RESEARCH_FEEDBACK_DELIVERED',
                cycle_sha256=claim['bundle_sha256'], feedback_sha256=result['feedback']['feedback_sha256'],
                source_verified=False, submission_enabled=False, economic_authority=False))
    before = original_bytes(enrolled.root), original_bytes(controller)
    public = dict(base_runtime=bundle(), current_runtime=bundle(), source_commit=bundle()['source_commit'],
        account_ref_sha256=enrolled.account, records={})
    with module()._retained(enrolled.root, controller, public=public, original=original(enrolled)) as handle:
        report = handle['report']
        expected = dict(absent='FEEDBACK_NOT_RETAINED', retained='PENDING_DELIVERY', delivered='DELIVERED')
        assert report['feedback'] == [dict(claim_sha256=_digest(claim), result_sha256=_digest(result),
            bundle_sha256=claim['bundle_sha256'], status=expected[feedback], delivery_sha256=(
                _digest(dict(status='RESEARCH_FEEDBACK_DELIVERED', cycle_sha256=claim['bundle_sha256'],
                    feedback_sha256=result['feedback']['feedback_sha256'], source_verified=False,
                    submission_enabled=False, economic_authority=False)) if feedback == 'delivered' else None))]
        assert report['verified_partial'] == [] and report['mutation_permitted'] is False
        assert handle['stores'][0].read_claim(claim, 'result') == canonical(result)
    assert before == (original_bytes(enrolled.root), original_bytes(controller))


def test_pending_mirror_keeps_original_native_partial_state(enrolled, tmp_path):
    controller = tmp_path / 'pending-controller'
    controller.mkdir(mode=0o700)
    with store(enrolled) as db:
        complete(enrolled, db, NOW)
    with store(enrolled, root=controller, role='controller'):
        pass
    public = dict(base_runtime=bundle(), current_runtime=bundle(), source_commit=bundle()['source_commit'],
        account_ref_sha256=enrolled.account, records={})
    with module()._retained(enrolled.root, controller, public=public, original=original(enrolled)) as handle:
        assert handle['report']['verified_partial'] == [dict(status='CONTROLLER_MIRROR_PENDING',
            first_ordinal=1, last_ordinal=1)]


def test_conflicting_controller_claim_is_rejected_by_original_native_validator(retained_pair):
    f = retained_pair
    # A complete native controller history with a different first claim cannot
    # mirror the source. Keep both stores individually valid and contiguous.
    other = f['controller'].parent / 'other-controller'
    other.mkdir(mode=0o700)
    other_source = f['controller'].parent / 'other-source'
    other_source.mkdir(mode=0o700)
    with store(f['f'], root=other_source) as db:
        claim, result = complete(f['f'], db, NOW + 1000)
    with store(f['f'], root=other, role='controller') as db:
        db.adopt(claim)
        db.put(claim, 'result', result)
    with pytest.raises(ValueError):
        with module()._retained(f['executor'], other, public=f['public'], original=f['original']):
            pass


def test_public_runtime_without_bound_rotation_cannot_hide_an_episode(retained_pair):
    f = retained_pair
    f['public']['current_runtime'] = dict(bundle(), episode_id='f' * 64)
    with pytest.raises(ValueError, match='ROTATION'):
        with module()._retained(f['executor'], f['controller'], public=f['public'], original=f['original']):
            pass
