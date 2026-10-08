"""Real AUTO cash/export and native recovery with an isolated publisher child.

Only accepted-source, OS roles and venue/network transport are test boundaries.
Original issuer/receipt/OMS/cash, both retention stores, publication validators
and filesystem publisher, controller and rotation are exercised unchanged.
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_feedback_recovery_fixture import (
    completed_bar_feedback as completed_bar_feedback, case as case, original_evidence as original_evidence,
)
from tests.tools.gold_controller_fixture import map_host_custody
from tests.tools.test_gold_feedback_delivery import BOOTSTRAP


def test_original_auto_cash_completion_retries_same_claim_and_rotates_once(completed_bar_feedback, tmp_path, monkeypatch):
    from tools import gold_strategy_controller as controller, gold_strategy_tick as tick, gold_bar_rotation
    from tools import demo_operating_bundle as installer, gold_feedback_delivery as delivery
    from tools.gold_feedback_recovery import FeedbackOwner
    from tools.gold_pilot_retention_store import canonical
    f = completed_bar_feedback
    monkeypatch.setattr(os, 'geteuid', os.getuid)
    map_host_custody(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, 'EXECUTOR', f.executor_root)
    monkeypatch.setattr(controller.supervisor, 'STATE', f.controller_root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: f.clock[0])
    registry = dict(executor_uid=f.executor_uid)
    monkeypatch.setattr(controller, 'observation_configuration',
        lambda source: (registry, dict(account_ref_sha256=f.account)))
    from tools.gold_pilot_install import _read_target
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json',
        lambda path: _read_target(path, uid=os.getuid()))
    runtime_path = f.controller_root/'strategy-runtime.json'
    runtime_path.write_bytes(canonical(f.base_runtime)); runtime_path.chmod(0o600)
    research = tmp_path/'research'
    calls = []
    real_run = subprocess.run
    def publish_child(command, **kwargs):
        assert command[:5] == [sys.executable, '-P', '-B', '-m', 'tools.demo_feedback_publish']
        assert '--bar' in command and str(kwargs['cwd']) == '/opt/quantos/releases/'+f.source_commit
        settings = dict(executor=str(f.executor_root), research=str(research), source=f.source_commit,
            cycle=_digest(f.base_runtime), artifact=f.exported['artifact_sha256'],
            feedback=f.exported['feedback_sha256'], phase='publication', error=errno.ENOSPC if not calls else None)
        child = real_run([sys.executable, '-B', '-c', BOOTSTRAP, json.dumps(settings)],
            cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
        assert child.stderr == b''
        calls.append(child)
        return child
    monkeypatch.setattr(delivery.subprocess, 'run', publish_child)
    # Popen remains real for the isolated publication child. The controller's
    # worker command is refused separately before it can start.
    monkeypatch.setattr(controller.demo_task, 'preparation_command',
        lambda **kw: (_ for _ in ()).throw(AssertionError('completed feedback started another worker')))
    with FeedbackOwner() as owner:
        first = tick.run_loop(source_commit=f.source_commit, require_pilot=True, feedback_owner=owner)
        assert first['status'] == 'SETTLED_FEEDBACK_PENDING', first
        assert len(calls) == 1 and calls[0].returncode == 4
        assert not research.exists()
        assert gold_bar_rotation.load(root=f.controller_root, base=f.base_runtime, account=f.account) == f.base_runtime
        second = tick.run_loop(source_commit=f.source_commit, require_pilot=True, feedback_owner=owner)
        assert second['status'] == 'RESEARCH_FEEDBACK_DELIVERED', second
        assert len(calls) == 2 and calls[1].returncode == 0
        current = gold_bar_rotation.load(root=f.controller_root, base=f.base_runtime, account=f.account)
        found = owner.discover(current_runtime=current)
        assert found['relation'] == 'PREVIOUS' and found['claim'] == f.claim
        assert found['result'] == f.result and found['result_bytes'] == f.result_bytes
        assert found['executor_checkpoint'] == f.executor_checkpoint
        assert found['controller_checkpoint']['frames'] == f.executor_checkpoint['frames'] == 3
        owner.release()
        assert Path(f.exported['path']).read_bytes() == f.artifact_bytes
        published = list(research.rglob(f.exported['feedback_sha256']+'.json'))
        assert len(published) == 1 and json.loads(published[0].read_bytes()) == f.artifact
    with FeedbackOwner() as restarted:
        restarted.bind(root=f.controller_root, base_runtime=f.base_runtime, source_commit=f.source_commit,
            account=f.account, registry=registry)
        assert restarted.recover(current_runtime=current,
            deliver=lambda **kw: (_ for _ in ()).throw(AssertionError('replayed completed publication'))) is None
        assert gold_bar_rotation.load(root=f.controller_root, base=f.base_runtime, account=f.account) == current
    assert len(calls) == 2
