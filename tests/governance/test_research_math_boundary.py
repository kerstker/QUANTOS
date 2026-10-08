"""Offline research computations remain usable without the alpha service."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('computation', [
    '''from services.research.factor_set import select_synergistic_set
r = select_synergistic_set({'a': [0.01, 0.02, 0.03], 'clone': [0.01, 0.02, 0.03]}, max_k=1)
assert r['selected_ids'] == ['a']
assert r['dropped_redundant'] == ['clone']
''',
    '''from services.research.portfolio_evidence import evaluate_portfolio_executable_value
r = evaluate_portfolio_executable_value({"candidate_id": "test"}, reference_aum=1000, executable_metrics={}, evidence_hashes={})
assert r.status.value == 'INCOMPLETE'
assert r.score_usd is None
''',
    '''from types import SimpleNamespace
from services.research.validation_dag import OOSValidationAdapter
r = OOSValidationAdapter().evaluate(SimpleNamespace(panel={}))
assert r.outcome == 'BLOCK'
assert r.reason == 'missing oos inputs'
''',
])
def test_research_math_without_alpha_service(computation):
    code = '''import importlib.abc, sys
class NoAlpha(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'services.alpha' or fullname.startswith('services.alpha.'):
            raise ImportError('alpha service unavailable')
sys.meta_path.insert(0, NoAlpha())
''' + computation
    result = subprocess.run([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[2],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('operation', [
    '''from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.lifecycle.catalog_snapshot import CatalogSnapshot, AlphaStateRow
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from services.lifecycle import project
definition = AlphaDefinition(alpha_id='example', name='example', factor_ids=('f',))
row = AlphaStateRow('example', definition, AlphaState.DRAFT, (0.01, 0.02), None, project(AlphaState.DRAFT))
class Catalog:
    def __init__(self): self.saved = CatalogSnapshot((definition.definition_hash,), (row,))
    def sharpe(self, alpha_id):
        assert alpha_id == 'example'
        return 0.25
    def snapshot(self): return self.saved
    def restore(self, snapshot): self.saved = snapshot
registry = ExperimentRegistry(SqliteExperimentStore(':memory:', create=True))
source = Catalog()
registry.save(source, now=1)
assert registry._store.all_alphas()[0]['sharpe'] == 0.25
target = Catalog()
target.saved = None
registry.load_into(target)
assert target.saved == source.saved
''',
    '''from types import SimpleNamespace
from services.research.session import ResearchSearchRuntime, _inner_loop
runtime = ResearchSearchRuntime(agent_loop=lambda **kw: kw,
    guided_search_loop=None, combined_hypothesis_space=None,
    evolutionary_search_loop=None, make_evolution_provider=None, mcts_alpha_search=None,
    catalog_factory=lambda **kw: object(), config_factory=lambda **kw: SimpleNamespace(**kw))
result = _inner_loop('catalog', True, fwd_horizon=4, search_runtime=runtime)
assert result['catalog'] == 'catalog'
assert result['config'].wf_embargo == 3
assert result['config'].compute_regime_evidence is True
''',
])
def test_research_composition_without_alpha_service(operation):
    test_research_math_without_alpha_service(operation)


def test_pipeline_uses_explicit_runtime_without_alpha_service():
    test_research_math_without_alpha_service('''
from services.research.alpha_pipeline import run_alpha_asset_pipeline
from services.research.alpha_factory import run_alpha_factory
from services.research.alpha_expression import FieldPanel
from services.research.ports.alpha_runtime import AlphaResearchRuntime
from services.validation.selection_authority import SelectionAuthorityBlocked
class Catalog:
    def all_ids(self): return []
runtime = AlphaResearchRuntime(catalog_factory=Catalog, evaluator_factory=lambda c: None,
    registry_factory=lambda: None, make_child_asset=None, optimize_population=None)
panel = FieldPanel(('A', 'B'), (1, 2), {'close': [[100, 100], [100, 100]]})
result = run_alpha_asset_pipeline(panel, [], diagnostic_only=True, research_runtime=runtime)
assert result['n_evaluated'] == 0
assert result['diagnostic_only'] is True
for action, args in ((run_alpha_asset_pipeline, (panel, [])), (run_alpha_factory, (panel, [], []))):
    try: action(*args, research_runtime=runtime)
    except SelectionAuthorityBlocked: pass
    else: raise AssertionError('legacy selection unexpectedly enabled')
''')


@pytest.mark.parametrize('legacy, shared, names', [
    ('services.alpha.family_testing', 'quantos.analytics.family_testing',
     ('FamilyAssignment', 'HierarchicalFdrResult', 'hierarchical_fdr', 'marginal_ir')),
    ('services.alpha.orthogonalize', 'quantos.analytics.orthogonalize',
     ('OrthogonalizationResult', 'orthogonalize')),
    ('services.alpha.executable_value', 'quantos.analytics.executable_value',
     ('ExecutableValueStatus', 'ExecutableValueInputs', 'ExecutableValuePolicy', 'ExecutableValueResult', 'evaluate_executable_value')),
    ('services.alpha.catalog', 'services.lifecycle.catalog_snapshot',
     ('AlphaStateRow', 'CatalogSnapshot')),
    ('services.alpha.evaluation_evidence', 'quantos.contracts.evaluation_evidence',
     ('bind_evaluation_reproducibility', 'evaluation_reproducibility_hash', 'verify_evaluation_reproducibility')),
])
def test_old_serialized_globals_resolve_to_same_shared_objects(legacy, shared, names):
    import importlib
    import pickle
    old, new = importlib.import_module(legacy), importlib.import_module(shared)
    for name in names:
        value = getattr(new, name)
        assert getattr(old, name) is value
        assert pickle.loads(f'c{legacy}\n{name}\n.'.encode()) is value
