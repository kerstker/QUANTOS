import pytest

from tools.gold_session_io import read, SESSION_INPUT_BYTES
from tools.gold_cost_inputs import MAX_BYTES


def test_session_input_can_exceed_short_experiment_limit(tmp_path):
    payload = b' '*(MAX_BYTES+1)
    (tmp_path/'inputs.json').write_bytes(payload)
    assert read(tmp_path, 'inputs.json') == payload
    (tmp_path/'report.json').write_bytes(payload)
    with pytest.raises(ValueError, match='FILE_TOO_LARGE'):
        read(tmp_path, 'report.json')


def test_session_input_limit_remains_finite(tmp_path):
    with (tmp_path/'inputs.json').open('wb') as handle:
        handle.truncate(SESSION_INPUT_BYTES+1)
    with pytest.raises(ValueError, match='SESSION_INPUT_TOO_LARGE'):
        read(tmp_path, 'inputs.json')


def test_session_input_symlink_is_rejected(tmp_path):
    original = tmp_path/'original'
    original.write_text('{}')
    (tmp_path/'inputs.json').symlink_to(original)
    with pytest.raises(ValueError, match='UNSAFE_PATH'):
        read(tmp_path, 'inputs.json')


def test_total_chain_budget_blocks_publication_and_retains_previous_head(tmp_path, monkeypatch):
    from tests.tools.test_gold_account_chain import setup
    from tools import gold_account_chain as chain
    args = setup(tmp_path, monkeypatch)
    root = tmp_path/'chain'
    chain.advance(root, tmp_path/'first', **args)
    previous = (root/'000001/transition.json').read_bytes()
    size = sum(p.stat().st_size for p in (root/'000001').iterdir())
    monkeypatch.setattr(chain, 'ACCOUNT_CHAIN_BYTES', size+1)
    with pytest.raises(ValueError, match='ACCOUNT_CHAIN_BYTE_BUDGET'):
        chain.advance(root, tmp_path/'full', **args)
    assert not (root/'000002').exists()
    assert (root/'000001/transition.json').read_bytes() == previous
    assert len(list(root.glob('attempt-*/failure.json'))) == 1
