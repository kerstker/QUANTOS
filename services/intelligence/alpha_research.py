"""Fixed intelligence-layer composition for legacy diagnostic research only.

The core still checks the legacy quarantine before touching any injected component.
This module does not provide lifecycle, approval, or submission capabilities.
"""
from services.alpha.catalog import AlphaCatalog
from services.alpha.evaluator import AlphaEvaluator
from services.alpha.lineage import make_child_asset
from services.alpha.population_optimizer import optimize_population
from services.alpha.registry import AlphaAssetRegistry
from services.research.ports.alpha_runtime import AlphaResearchRuntime
from services.research.alpha_factory import run_alpha_factory as _factory
from services.research.alpha_pipeline import run_alpha_asset_pipeline as _pipeline


RESEARCH_RUNTIME = AlphaResearchRuntime(
    catalog_factory=AlphaCatalog,
    evaluator_factory=AlphaEvaluator,
    registry_factory=AlphaAssetRegistry,
    make_child_asset=make_child_asset,
    optimize_population=optimize_population,
)


def run_alpha_factory(*args, **kwargs):
    if 'research_runtime' in kwargs:
        raise TypeError('research_runtime is fixed by the intelligence composition boundary')
    return _factory(*args, research_runtime=RESEARCH_RUNTIME, **kwargs)


def run_alpha_asset_pipeline(*args, **kwargs):
    if 'research_runtime' in kwargs:
        raise TypeError('research_runtime is fixed by the intelligence composition boundary')
    return _pipeline(*args, research_runtime=RESEARCH_RUNTIME, **kwargs)
