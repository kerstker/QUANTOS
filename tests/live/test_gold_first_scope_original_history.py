"""Original BTC issuer and executor evidence, before any Gold installation.

All account/venue and host-custody boundaries are the existing isolated test
substitutes. The native issuer, signature, risk, receipt and ledger checks run.
This module initially covers one source identity; it is not a version bridge.
"""
from contextlib import ExitStack, closing, contextmanager
from copy import deepcopy
from decimal import Decimal
from functools import partial
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from live.execution.demo_issuer_store import IssuerStore
from live.execution.demo_pre_submit import _state
from tests.live.test_demo_market_partition import book as native_book


def issuer_claims(store):
    return tuple(tuple(row) for row in store.db.execute(
        'SELECT account_ref_sha256,purpose,client_order_id,proposal_sha256 '
        'FROM issuer_order_claims ORDER BY account_ref_sha256,purpose,client_order_id'))


def read_json(path):
    return json.loads(path.read_bytes())


def build_original_btc_history(tmp_path, monkeypatch, *, include_unsigned=False,
                               materials_factory=None):
    """Return native book material and its original issuer, with explicit close."""
    from live.execution import demo_authority, demo_issuer, demo_pre_submit, demo_prepared_execution
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution.demo_issuer_policy import review_submission
    from live.exchange import binance_demo_exact
    from tests.live.test_demo_pre_submit import setup, stores, NOW
    from tests.live.test_demo_execution_session import request
    from tests.live.test_demo_issuer import policy_for
    from tests.live import test_demo_reported_recovery as recovery_fixture

    stack = ExitStack()
    issuer_root = tmp_path/'original-issuer'
    path = issuer_root/'issuance.sqlite3'
    captured = {}
    opened_stores = []

    def ready(root, patch):
        if materials_factory is None:
            issuer_root.mkdir(mode=0o700)
            private, _, values = setup(root, patch)
            registry = deepcopy(demo_authority.load_registry_for_executor())
            material = dict(private=private, values=values, registry=registry, policy=None)
        else:
            # Factory runs before the first proposal, reserve or signature.
            material = materials_factory(root=root, monkeypatch=patch)
            private, values, registry = (material[name] for name in ('private', 'values', 'registry'))
        ds, rs, oms = stores(root)
        opened_stores.extend((ds, rs, oms))
        opened = dict(dispatch=ds, recovery=rs, oms=oms)
        existing_path = material.get('issuer_path')
        if material.get('installed_materials') is not None:
            assert existing_path is not None, 'installed materials must identify their existing issuer'
            assert Path(existing_path).is_file()
        log = IssuerStore(Path(existing_path) if existing_path is not None else path,
                          create=existing_path is None)
        stack.callback(log.close)
        session = ExecutionSession(**opened, evidence_directory=root/'evidence')
        try:
            handoff = session.propose(request(values), now_ms=values['now_ms'])
            policy = material['policy'] or policy_for(handoff['proposal'], registry)
            grant = demo_issuer.issue_submission(handoff, policy=policy, registry=registry,
                key=private, store=log, now_ms=values['now_ms'])
            unsigned = None
            if include_unsigned:
                other = deepcopy(values)
                other['orders'][0]['newClientOrderId'] = 'QDoriginalUnsigned001'
                second = ExecutionSession(**opened, evidence_directory=root/'unsigned-evidence')
                try:
                    unsigned = second.propose(request(other), now_ms=values['now_ms'])
                    plan = review_submission(unsigned, policy=policy, registry=registry, now_ms=values['now_ms'])
                    # Native crash boundary: reserve committed, signer never called.
                    log.reserve(plan, policy=policy, registry=registry)
                finally:
                    second.close()
            batch = demo_pre_submit.prepare(json.dumps(grant['envelope']).encode(), **values,
                **opened, evidence_directory=root/'evidence')
        finally:
            session.close()
        captured.update(issuer_store=log, old_registry=deepcopy(registry), old_policy=deepcopy(policy),
            old_grant=deepcopy(grant), old_handoff=deepcopy(handoff), unsigned_handoff=unsigned,
            installed_materials=material.get('installed_materials'))
        # The same isolated outside-world substitutes as original ready().
        patch.setattr(demo_prepared_execution, 'now_ms', lambda: values['now_ms']+1)
        patch.setattr(demo_prepared_execution, '_require_state_custody', lambda *a, **kw: None)
        patch.setattr(binance_demo_exact, '_credentials', lambda ref: ('isolated-fixture-key', 'isolated-fixture-secret'))
        return batch, dict(**opened, **{name: values[name] for name in ('risk_inputs', 'mandate', 'attestations')})

    try:
        with monkeypatch.context() as seam:
            seam.setattr(recovery_fixture, 'ready', ready)
            original_inputs = recovery_fixture.inputs

            def account_inputs(directory):
                value = original_inputs(directory)
                if captured.get('installed_materials') is not None:
                    # External fixture receipt declares the actual compiled
                    # LIMIT price; execution price, fees and cash stay original.
                    from tests.research.test_binance_order_receipts import receipt
                    row = value['windows'][0]['messages'][0]
                    event = json.loads(row['raw_utf8'])
                    order = captured['old_handoff']['proposal']['orders'][0]
                    assert order['symbol'] == 'BTCUSDC' and order['quantity'] == event['o']['q']
                    event['o']['p'] = order['price']
                    value['windows'][0]['messages'][0] = receipt(event, row['received_at_ms'])
                return value

            seam.setattr(recovery_fixture, 'inputs', account_inputs)
            book_tuple = stack.enter_context(contextmanager(native_book.__wrapped__)(tmp_path, monkeypatch))
        state, _, _, _, events = book_tuple
        if captured['installed_materials'] is not None:
            # Generate the later synthetic account observations after this
            # genuinely installed one-hour key has naturally expired. These
            # are new observations, never rewritten signed/executed originals.
            from tests.live.test_demo_market_partition_runtime import shift
            observations = book_tuple[2]
            delta = max(key['valid_until_ms'] for key in captured['old_registry']['keys'])-NOW
            for name in ('legacy_origin', 'gold_origin'):
                observations[name] = shift(observations[name], delta)
            observations['now_ms'] += delta
        log = captured['issuer_store']
        evidence = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('book/evidence', 'book/receipt-reconciliation')
            for p in sorted((tmp_path/folder).rglob('*.json')) if p.is_file()}
        return dict(book_tuple=book_tuple, issuer_root=log.path.parent, issuer_path=log.path,
            issuer_prefix=deepcopy(log.snapshot()), issuer_claims=issuer_claims(log),
            executor_state=deepcopy(state), event_rows=events.events(), evidence_hashes=evidence,
            close=stack.close, **captured)
    except BaseException:
        stack.close()
        for store in opened_stores:
            store.close()
        raise


def native_old_operator_materials(*, root, monkeypatch, source_commit='c'*40):
    """Run the real old setup CLI/importer before the original BTC proposal.

    The synthetic TTY, account capture, source acceptance and UID metadata are
    explicit isolated-host substitutes. Compiler/manifest/installer/registry,
    durable empty issuer creation and all later trading checks are native.
    source_commit is fixed before assembly; this still runs current test code,
    not the historical binary named by a supplied ancestor identity.
    """
    import pwd
    import sys
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from live.execution import demo_authority, demo_current_kill
    from live.execution.demo_session_inputs import decode
    from services.validation.demo_submission import _decode
    from tests.live.test_demo_pre_submit import setup, NOW
    from tests.live.test_demo_execution_session import request
    from tools import demo_operator_setup as operator, demo_operating_bundle as installer

    inputs = root/'input-materials'
    root.chmod(0o700)
    inputs.mkdir(mode=0o700)
    _, _, values = setup(inputs, monkeypatch)
    origin = deepcopy(values['origin'])
    registry = deepcopy(demo_authority.load_registry_for_executor())
    registry['keys'] = []
    base = root.parent/'old-installation'
    base.mkdir(mode=0o700)
    stage, config, controller = (base/name for name in ('setup', 'config', 'controller'))
    public = base/'public-registry.json'
    public.write_bytes(operator.canonical(registry))
    public.chmod(0o600)
    issuer_root = root.parent/'original-issuer'
    stop = (inputs/'kill-switch.sqlite3').read_bytes()
    ownership = {root: (registry['executor_uid'], registry['executor_uid'])}
    original_lstat, original_init = Path.lstat, Path.__init__
    native_metadata = installer.private_metadata
    calls = []

    class FixtureTTY(io.StringIO):
        def isatty(self):
            return True

    output = FixtureTTY()

    def translated_path(path, *args, **kwargs):
        if args == ('/etc/quantos/demo-operator-setup',):
            args = (str(stage),)
        original_init(path, *args, **kwargs)

    def info(path, *args, **kwargs):
        actual = original_lstat(path, *args, **kwargs)
        if path not in ownership:
            return actual
        fields = {name: getattr(actual, name) for name in dir(actual) if name.startswith('st_')}
        return SimpleNamespace(**(fields | dict(st_uid=ownership[path][0], st_gid=ownership[path][1])))

    def chown(path, uid, gid):
        path = Path(path)
        path.relative_to(root.parent)
        ownership[path] = (uid, gid)

    def metadata(actual, *, maximum):
        # Preserve the native regular-file/mode/link/size gates; map only owner.
        native_metadata(SimpleNamespace(st_mode=actual.st_mode, st_nlink=actual.st_nlink,
            st_size=actual.st_size, st_uid=0, st_gid=0), maximum=maximum)

    def capture(received_source, *, symbol):
        assert received_source == source_commit and symbol == 'BTCUSDC'
        calls.append((received_source, symbol))
        return deepcopy(registry), deepcopy(origin)

    with monkeypatch.context() as patch:
        for name, path in dict(EXECUTOR=root, ISSUER=issuer_root, CONFIG=config,
                               CONTROLLER=controller, REGISTRY=public).items():
            patch.setattr(installer, name, path)
        patch.setattr(Path, '__init__', translated_path)
        patch.setattr(Path, 'lstat', info)
        patch.setattr(os, 'chown', chown)
        patch.setattr(os, 'fchown', lambda fd, uid, gid: chown(Path('/proc/self/fd/'+str(fd)).resolve(), uid, gid))
        patch.setattr(os, 'geteuid', lambda: 0)
        patch.setattr(installer, '_require_protected', lambda item, **kw:
            None if stat.S_ISDIR(item.st_mode) else pytest.fail('non-directory parent'))
        patch.setattr(installer, 'private_metadata', metadata)
        patch.setattr(installer, '_read_root_owned_json', lambda path: path.read_bytes())
        patch.setattr(installer, 'read_account', lambda received, source:
            origin['account_ref_sha256'] if received == registry and source == source_commit
            else pytest.fail('account/source substitution'))
        patch.setattr(installer, 'verify_source', lambda source:
            None if source == source_commit else pytest.fail('unaccepted source'))
        patch.setattr(installer, 'now_ms', lambda: NOW)
        patch.setattr(operator, '_runtime_inputs', capture)
        patch.setattr(operator, '_stop_image', lambda: stop)
        patch.setattr(sys, 'stdin', FixtureTTY())
        patch.setattr(sys, 'stdout', output)
        patch.setenv('SUDO_UID', '12345')
        patch.setenv('SUDO_USER', 'isolated-test-operator')
        patch.setattr(pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_name='isolated-test-operator')
            if uid == 12345 else pytest.fail('unexpected operator UID'))
        patch.setattr('builtins.input', lambda prompt: prompt.split('请输入 ', 1)[1].strip())
        status = operator.main(['--source-commit', source_commit, '--enroll'])
    assert status == 0, output.getvalue()
    assert len(calls) == 2
    read = read_json
    spec, result = read(stage/'spec.json'), read(stage/'result.json')
    policy, profile, current = read(config/'policy.json'), read(config/'profile.json'), read(public)
    manifest = read(stage/'bundle/manifest.json')
    task = read(controller/'tasks'/(manifest['tasks'][0]+'.json'))
    key = read(config/'signing-key.json')
    private = Ed25519PrivateKey.from_private_bytes(_decode(key['private_key_base64url'], 32))
    with closing(IssuerStore(issuer_root/'issuance.sqlite3', read_only=True)) as store:
        assert store.snapshot() == [] and issuer_claims(store) == ()
    # Same native request decoder and risk pipeline, using the compiler's exact
    # permissions and actual compiled task. No signed object exists yet.
    raw = json.loads(request(values))
    raw.update(orders=task['orders'], mandate=profile['mandate'], attestations=profile['attestations'])
    raw['risk'].update(native_limits=profile['native_limits'], strategy=profile['strategy'],
        kill_switch=task['kill_switch'], plan_turnover=profile['minimum_plan_turnover'])
    raw['risk']['account'].update(day_pnl=profile['initial_day_pnl'], peak_equity=profile['initial_peak_equity'])
    values = decode(operator.canonical(raw), now_ms=NOW)
    monkeypatch.setattr(demo_authority, '_read_root_owned_json', lambda path: public.read_bytes())
    monkeypatch.setattr(demo_current_kill, '_PATH', root/'kill-switch.sqlite3')
    material = dict(setup=stage, config=config, controller=controller, executor=root, public_registry=public,
        issuer_root=issuer_root, spec=spec, spec_sha256=operator.digest(spec), result=result,
        policy=policy, profile=profile, registry=current, original_registry=registry,
        enrollment=read(stage/'operator-enrollment.json'), manifest=manifest, task=task,
        installer_result=read(controller/'installation.json'), setup_output=output.getvalue(),
        empty_issuer_inode=(issuer_root/'issuance.sqlite3').stat().st_ino,
        stop_sha256=hashlib.sha256(stop).hexdigest())
    public_files = [stage/'spec.json', stage/'result.json', stage/'operator-enrollment.json',
        stage/'bundle/manifest.json', public, controller/'installation.json',
        *[config/name for name in ('policy.json', 'profile.json', 'authority.json')],
        *[controller/'tasks'/(identity+'.json') for identity in manifest['tasks']]]
    material['public_file_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in public_files}
    return dict(private=private, values=values, registry=current, policy=policy,
                issuer_path=issuer_root/'issuance.sqlite3', installed_materials=material)


@pytest.fixture
def original_btc_history(tmp_path, monkeypatch):
    value = build_original_btc_history(tmp_path, monkeypatch)
    try:
        yield value
    finally:
        value['close']()


def test_original_btc_recovery_has_its_actual_issuer_reservation_signature_and_claim(original_btc_history):
    value = original_btc_history
    state, context, _, stores, _ = value['book_tuple']
    assert value['issuer_path'].is_file(), 'native BTC recovery must retain its originating issuer database'
    with closing(IssuerStore(value['issuer_path'], read_only=True)) as reopened:
        rows = reopened.snapshot()
        assert [row['kind'] for row in rows] == ['RESERVED', 'SIGNED']
        claim = reopened.db.execute('SELECT * FROM issuer_order_claims').fetchone()
        assert claim is not None and claim['proposal_sha256'] == rows[0]['proposal_sha256']
        assert rows == value['issuer_prefix'] and issuer_claims(reopened) == value['issuer_claims']
        assert rows[1]['payload']['envelope'] == value['old_grant']['envelope']
        assert state['reservations'][0]['approval_id'] == value['old_grant']['envelope']['payload']['approval_id']
        assert rows[0]['payload']['plan']['context'] == state['reservations'][0]['payload']['context']
    assert _state(stores['dispatch'], stores['recovery'], stores['oms']) == state
    assert context['initial_equity'] == '5000' and context['canonical_wallet'] == '4999.6801'
    assert value['event_rows'] and value['evidence_hashes']


def test_native_unsigned_btc_reservation_survives_recovery_and_restart(tmp_path, monkeypatch):
    value = build_original_btc_history(tmp_path, monkeypatch, include_unsigned=True)
    try:
        before = value['issuer_prefix']
        assert [row['kind'] for row in before] == ['RESERVED', 'SIGNED', 'RESERVED']
        assert len(value['issuer_claims']) == 2
        reservations = [row['payload'] for row in before if row['kind'] == 'RESERVED']
        assert sum(Decimal(row['plan']['notional']) for row in reservations) == Decimal('240')
        assert len(value['executor_state']['reservations']) == 1
        assert value['book_tuple'][1]['canonical_wallet'] == '4999.6801'
        with closing(IssuerStore(value['issuer_path'])) as reopened:
            assert reopened.snapshot() == before and issuer_claims(reopened) == value['issuer_claims']
            # Review at its original valid timestamp isolates the durable claim
            # gate; no private signer is needed to demonstrate reserve rejection.
            from live.execution.demo_issuer_policy import review_submission
            plan = review_submission(value['unsigned_handoff'], policy=value['old_policy'],
                registry=value['old_registry'], now_ms=reservations[-1]['plan']['issued_at_ms'])
            with pytest.raises(ValueError, match='ISSUER_PROPOSAL_ALREADY_RESERVED'):
                reopened.reserve(plan, policy=value['old_policy'], registry=value['old_registry'])
            assert reopened.snapshot() == before
    finally:
        value['close']()


def test_original_registry_is_unchanged_and_history_reopens_after_old_key_expiry(original_btc_history):
    value = original_btc_history
    _, _, observations, _, _ = value['book_tuple']
    assert observations['registry'] == value['old_registry']
    assert all(key['valid_until_ms'] <= observations['now_ms'] for key in value['old_registry']['keys'])
    with closing(IssuerStore(value['issuer_path'], read_only=True)) as reopened:
        assert reopened.snapshot() == value['issuer_prefix']
        assert issuer_claims(reopened) == value['issuer_claims']
    reserved = value['issuer_prefix'][0]['payload']
    assert reserved['registry'] == value['old_registry']
    assert reserved['policy'] == value['old_policy']
    assert reserved['plan']['issued_at_ms'] < value['old_registry']['keys'][0]['valid_until_ms']


@pytest.mark.parametrize('include_unsigned', [False, True])
def test_native_old_operator_install_precedes_original_btc_issuer_history(tmp_path, monkeypatch, include_unsigned):
    value = build_original_btc_history(tmp_path, monkeypatch, include_unsigned=include_unsigned,
                                       materials_factory=native_old_operator_materials)
    try:
        material = value['installed_materials']
        read = read_json
        assert material['result'] == read(material['setup']/'result.json')
        assert material['result']['status'] == 'INSTALLED_PENDING_CYCLE'
        assert material['result']['spec_sha256'] == material['spec_sha256']
        assert material['policy'] == value['old_policy'] == read(material['config']/'policy.json')
        assert material['registry'] == value['old_registry'] == read(material['public_registry'])
        assert material['issuer_root'] == value['issuer_root']
        assert value['issuer_path'].stat().st_ino == material['empty_issuer_inode']
        expected = ['RESERVED', 'SIGNED'] + (['RESERVED'] if include_unsigned else [])
        assert [row['kind'] for row in value['issuer_prefix']] == expected
        assert len(value['issuer_claims']) == (2 if include_unsigned else 1)
        assert value['book_tuple'][1]['canonical_wallet'] == '4999.6801'
        assert all(key['valid_until_ms'] < value['book_tuple'][2]['now_ms'] for key in value['old_registry']['keys'])
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
            for path, digest in material['public_file_sha256'].items())
        assert material['installer_result']['orders_submitted'] is False
        assert material['task']['orders'] == value['old_handoff']['proposal']['orders']
        with closing(IssuerStore(value['issuer_path'], read_only=True)) as reopened:
            assert reopened.snapshot() == value['issuer_prefix']
            assert issuer_claims(reopened) == value['issuer_claims']
    finally:
        value['close']()


def test_ancestor_source_is_compiled_before_issuer_history_and_never_rewritten(tmp_path, monkeypatch):
    ancestor = '4673bc0a785a67b13d35402dd801e7984c69f363'
    value = build_original_btc_history(tmp_path, monkeypatch,
        materials_factory=partial(native_old_operator_materials, source_commit=ancestor))
    try:
        material = value['installed_materials']
        assert material['spec']['source_commit'] == ancestor
        assert material['result']['source_commit'] == ancestor
        assert material['manifest']['source_commit'] == ancestor
        assert value['old_policy']['executor_release_commit'] == ancestor
        assert value['issuer_prefix'][0]['payload']['policy']['executor_release_commit'] == ancestor
        assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
            for path, digest in material['public_file_sha256'].items())
    finally:
        value['close']()
