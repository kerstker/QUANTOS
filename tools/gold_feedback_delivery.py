"""Publish a retained Gold artifact in the original separate, bounded child."""
from pathlib import Path
import re
import subprocess
import sys

from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.demo_feedback_publish import hash_value, pending_receipt, RETRYABLE_FAILURE_CODE


class FeedbackDeliveryPending(ValueError):
    """A bound storage diagnostic; it is not a successful delivery receipt."""
    def __init__(self, receipt):
        super().__init__('STRATEGY_FEEDBACK_DELIVERY_PENDING')
        self.receipt=dict(receipt)


def deliver_feedback(*,source_commit,cycle,exported):
    """The child alone reads the original artifact and permanently drops UID."""
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit) is not None,
        'STRATEGY_FEEDBACK_SOURCE')
    hash_value(cycle)
    require(type(exported) is dict and exported.get('status') in ('STRATEGY_FEEDBACK_RETAINED','BAR_STRATEGY_FEEDBACK_RETAINED')
        and exported.get('submission_enabled') is False and exported.get('economic_authority') is False,
        'STRATEGY_FEEDBACK_EXPORT')
    for key in ('feedback_sha256','artifact_sha256'):hash_value(exported.get(key))
    root=Path('/opt/quantos/releases')/source_commit
    child=subprocess.run([sys.executable,'-P','-B','-m','tools.demo_feedback_publish',
        '--bar' if exported['status']=='BAR_STRATEGY_FEEDBACK_RETAINED' else '--strategy','--source-commit',source_commit,'--cycle',cycle,
        '--artifact-sha256',exported['artifact_sha256'],'--feedback-sha256',exported['feedback_sha256']],
        cwd=root,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONPATH':str(root),
            'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'},
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=30,check=False)
    require(type(child.returncode) is int and child.returncode in (0,RETRYABLE_FAILURE_CODE)
        and type(child.stdout) is bytes and len(child.stdout)<=65536,'STRATEGY_FEEDBACK_DELIVERY_FAILED')
    value=load_json_without_duplicate_keys(child.stdout)
    if child.returncode==RETRYABLE_FAILURE_CODE:
        expected=pending_receipt(source_commit=source_commit,cycle=cycle,
            artifact_sha256=exported['artifact_sha256'],feedback_sha256=exported['feedback_sha256'])
        require(type(value) is dict and value==expected
            and all(value.get(k) is False for k in ('source_verified','submission_enabled','economic_authority')),
            'STRATEGY_FEEDBACK_DELIVERY_PENDING_BINDING')
        raise FeedbackDeliveryPending(value)
    require(type(value) is dict and value.get('status')=='RESEARCH_FEEDBACK_DELIVERED'
        and value.get('cycle_sha256')==cycle and value.get('feedback_sha256')==exported['feedback_sha256']
        and all(value.get(k) is False for k in ('source_verified','submission_enabled','economic_authority')),
        'STRATEGY_FEEDBACK_DELIVERY_BINDING')
    return value
