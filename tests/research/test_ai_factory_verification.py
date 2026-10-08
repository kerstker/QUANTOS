import pytest
from tests.governance.test_artifact_receipt_auth import _signed_envelope
from services.governance.ai_factory_verification import verify_research_receipt


def test_unconfigured_authority_does_not_admit_local_evidence():
    r=verify_research_receipt('a'*64,None,None,now=1700000000000)
    assert r['status']=='INDEPENDENT_AUTHORITY_UNCONFIGURED' and r['economic_eligible'] is False


def test_valid_signature_only_establishes_engineering_binding():
    envelope,key=_signed_envelope()
    r=verify_research_receipt('a'*64,envelope,key,now=1700000000000)
    assert r['status']=='VERIFIED_SIGNATURE_ENGINEERING_ONLY' and not r['economic_eligible']
    with pytest.raises(ValueError):verify_research_receipt('f'*64,envelope,key,now=1700000000000)
    with pytest.raises(ValueError):verify_research_receipt('a'*64,envelope,key,now=1700200000000)
    with pytest.raises(ValueError):verify_research_receipt('a'*64,envelope,key,now=1699000000000)


@pytest.fixture
def completed_factory(tmp_path, monkeypatch):
    import numpy as np
    from services.research.ai_factory_store import FactoryStore
    from tools.ai_quant_factory import execute, validate_config

    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    root = tmp_path / 'data/cache/ai_factory'
    root.mkdir(parents=True)
    times = np.arange(600, dtype=np.int64) * 3600000
    features = np.random.default_rng(3).normal(size=(600, 5))
    # Synthetic scores exercise package identity, not economic replay correctness.
    with FactoryStore(root / 'factory.db') as store:
        store.create(validate_config({'attempt': 1, 'mode': 'baseline', 'objective': 'BTC', 'budget': 3}))
        execute(store, 1, (times, features, features[:, 0] * .001),
                lambda spec, t, p: {'score': float(spec['id'])}, {'input': 'fixture'}, root)
    return root


def test_review_package_preserves_bound_research_without_granting_authority(completed_factory):
    from tools.ai_factory_verification import run_verification
    first = run_verification({'attempt': 1})
    second = run_verification({'attempt': 1})
    assert first['artifact_sha256'] == second['artifact_sha256']
    assert first['verification']['status'] == 'INDEPENDENT_AUTHORITY_UNCONFIGURED'
    assert first['economic_eligible'] is False


@pytest.mark.parametrize('target', ['plan', 'model', 'result', 'missing_trial'])
def test_review_rejects_internally_hashed_but_unbound_material(completed_factory, target):
    from services.research.ai_factory_store import FactoryStore
    from tools.ai_factory_verification import run_verification
    from tools.btc_factory_research import seal, verified
    from tools.cache_io import atomic_write_json

    dest = completed_factory / '1'
    if target == 'missing_trial':
        with FactoryStore(completed_factory / 'factory.db') as store:
            with store.c:
                store.c.execute('DELETE FROM trials WHERE attempt=1 AND ordinal=0')
    else:
        path = dest / (target + '.json')
        document = verified(path)
        document.pop('result_hash')
        if target == 'plan':
            document['identity']['config']['objective'] = 'different research'
        elif target == 'model':
            document['identity_hash'] = 'f' * 64
        else:
            document['model_hash'] = 'f' * 64
        atomic_write_json(str(path), seal(document))
    with pytest.raises(ValueError, match='research package binding'):
        run_verification({'attempt': 1})
    assert not (dest / 'independent-request.json').exists()
    assert not (dest / 'independent-trial-history.json').exists()
    assert not (dest / 'verification.json').exists()


def test_review_rejects_receipt_conflicting_with_canonical_trial_ledger(completed_factory):
    from services.research.ai_factory_store import FactoryStore, digest, encode
    from tools.ai_factory_verification import run_verification

    with FactoryStore(completed_factory / 'factory.db') as store:
        receipt = store.trials(1)[0]
        assert receipt['status'] == 'PASSED'
        receipt['status'] = 'FAILED'
        # Simulate internally consistent corruption of the workflow projection.
        # The canonical trial ledger still records PASSED.
        with store.c:
            store.c.execute('UPDATE trials SET payload=?,sha256=? WHERE attempt=1 AND ordinal=0',
                            (encode(receipt), digest(receipt)))
    with pytest.raises(ValueError, match='research package binding: trial ledger status'):
        run_verification({'attempt': 1})
    assert not (completed_factory / '1/independent-request.json').exists()


@pytest.mark.parametrize('change', ['model', 'request', 'history', 'stage_hash', None])
def test_completed_pipeline_rechecks_retained_review_without_new_experiments(completed_factory, monkeypatch, change):
    from tools import ai_factory_pipeline as pipeline
    from tools.ai_factory_verification import run_verification
    from tools.btc_factory_research import seal, verified
    from tools.cache_io import atomic_write_json
    from tools import ai_quant_factory

    dest = completed_factory / '1'
    review = run_verification({'attempt': 1})
    atomic_write_json(str(dest / 'pipeline.json'), seal({
        'status': 'COMPLETED', 'stage': 'CHECKPOINT',
        'completed': [{'stage': 'INDEPENDENT_PACKAGE', 'result_hash': review['result_hash']}]}))
    monkeypatch.setattr(ai_quant_factory, 'fit_predict', lambda *a, **k: pytest.fail('must not refit'))
    monkeypatch.setattr(pipeline, 'run_observation', lambda *a, **k: pytest.fail('must not reactivate'))
    if change == 'model':
        value = verified(dest / 'model.json'); value.pop('result_hash')
        value['identity_hash'] = 'e' * 64
        atomic_write_json(str(dest / 'model.json'), seal(value))
    elif change in ('request', 'history'):
        (dest / ('independent-request.json' if change == 'request' else 'independent-trial-history.json')).unlink()
    elif change == 'stage_hash':
        value = verified(dest / 'pipeline.json'); value.pop('result_hash')
        value['completed'][0]['result_hash'] = 'd' * 64
        atomic_write_json(str(dest / 'pipeline.json'), seal(value))
    before = {p.name: p.read_bytes() for p in dest.iterdir() if p.is_file()}
    if change is None:
        assert pipeline.run_pipeline({'attempt': 1})['state'] == 'COMPLETED'
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            pipeline.run_pipeline({'attempt': 1})
    assert before == {p.name: p.read_bytes() for p in dest.iterdir() if p.is_file()}
