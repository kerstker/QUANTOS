import asyncio
import json

import pytest

from tests.research.test_ai_factory_provider import LOCAL, Transport
from tools.ai_factory_provider import propose
from tools.ai_factory_collaboration import bounded_proposal
from tools.gold_initial_proposal import freeze_proposal


def proposal(path, catalog='GOLD_BAR_CANDIDATES_V1'):
    def local(*args, **kwargs):
        return propose(*args, environ=LOCAL,
                       transport=Transport({'candidate_id': 0, 'reason': 'next hypothesis'}), **kwargs)
    bounded_proposal('Choose a future hypothesis', [0, 1, 2], [],
        protocol='SINGLE_V1', output=path, options={'candidate_catalog': catalog}, propose_fn=local)


def test_other_catalog_cannot_seed_gold_bar_experiment(tmp_path):
    proposal(tmp_path/'origin', 'GOLD_ABSORPTION_V1')
    with pytest.raises(ValueError, match='CATALOG_OR_SCOPE'):
        freeze_proposal(tmp_path/'origin', tmp_path/'copy')
    assert not (tmp_path/'copy').exists()


def test_changed_role_receipt_is_rejected(tmp_path):
    proposal(tmp_path/'origin')
    path = tmp_path/'origin/roles/0.json'
    value = json.loads(path.read_text())
    value['proposal']['candidate_id'] = 1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        freeze_proposal(tmp_path/'origin', tmp_path/'copy')


def test_seed_cannot_silently_enable_ai_mode(tmp_path):
    from tools.gold_bar_program import run_program
    with pytest.raises(ValueError, match='REQUIRES_AI_PLAN'):
        asyncio.run(run_program(program_id=1, first_attempt=1, rounds=1,
                                initial_proposal=tmp_path/'anything'))


def test_memory_bound_proposal_requires_exact_memory_and_preserves_it(tmp_path):
    from services.research.gold_portfolio_memory import build_memory
    from tests.research.test_gold_portfolio_memory import completed
    memory = build_memory(completed()[:1], next_attempt=2, as_of_ms=200)
    path = tmp_path/'memory.json'
    path.write_text(json.dumps(memory))
    def local(*args, **kwargs):
        return propose(*args, environ=LOCAL,
                       transport=Transport({'candidate_id': 2, 'reason': 'continue'}), **kwargs)
    bounded_proposal('Choose future experiment', [0, 1, 2], [], protocol='SINGLE_V1',
        output=tmp_path/'origin', options=dict(candidate_catalog='GOLD_BAR_CANDIDATES_V1',
                                            research_memory=memory), propose_fn=local)
    with pytest.raises(ValueError):
        freeze_proposal(tmp_path/'origin', tmp_path/'missing')
    result, files = freeze_proposal(tmp_path/'origin', tmp_path/'copy', memory=path)
    assert result['candidate_id'] == 2
    assert (tmp_path/'copy/research-memory.json').read_bytes() == path.read_bytes()
    assert 'research-memory.json' in files
    other = build_memory(completed()[:1], next_attempt=2, as_of_ms=201)
    path.write_text(json.dumps(other))
    with pytest.raises(ValueError, match='CATALOG_OR_SCOPE'):
        freeze_proposal(tmp_path/'origin', tmp_path/'changed', memory=path)
    assert not (tmp_path/'changed').exists()


def test_memory_cannot_be_supplied_without_proposal():
    from tools.gold_bar_program import run_program
    with pytest.raises(ValueError, match='MEMORY_REQUIRES_PROPOSAL'):
        asyncio.run(run_program(program_id=1, first_attempt=3, rounds=1,
                                ai_plan=True, initial_memory='unused'))
