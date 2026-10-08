"""Composition-root adapter to the existing read-only CLI evidence contract."""
import os
from pathlib import Path
from tools.rf3_daily_forward import status as forward_status
from tools.research_first_assessment import (
    assess_paths, with_rf2_evidence, DEFAULT_REGISTRY, DEFAULT_ASSESSMENT, DEFAULT_PROFILE,
)
from tools.research_first_run_evidence import inspect_rf2


def read_assessment():
    report = assess_paths(DEFAULT_REGISTRY, DEFAULT_ASSESSMENT, DEFAULT_PROFILE)
    root = os.environ.get('QUANTOS_RF2_EVIDENCE_ROOT')
    if root:
        report = with_rf2_evidence(report, inspect_rf2(root))
        # File hashes remain available; local paths are not a browser contract.
        report['run_evidence'].pop('root', None)
        for item in report['run_evidence']['evidence_files']:
            item.pop('path', None)
    forward_root=os.environ.get('QUANTOS_RF3_OBSERVATION_ROOT')
    if forward_root:
        # The observer remains bound to its immutable collector, not UI releases.
        root=Path(forward_root)
        try:
            observed=forward_status(root,repo_root=root.parent/'source')
            report['forward_observation']={k:observed[k] for k in (
                'plan_hash','identity_matches','observed_ms','published','gaps','pending','slots')}
            report['forward_observation']['status']='OBSERVING' if observed['identity_matches'] and not observed['gaps'] else 'BLOCKED'
        except Exception:
            report['forward_observation']={'status':'DATA_BLOCKED'}
    return report
