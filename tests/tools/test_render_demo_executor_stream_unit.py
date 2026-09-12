"""Protected stream unit must run capture only with an immutable source."""
import pytest

from scripts.render_demo_executor_stream_unit import render


def test_stream_service_runs_only_protected_capture_and_preserves_human_gates():
    body=render(release_commit='a'*40,executor_uid=2001)
    assert 'User=2001\n' in body
    assert ' -m tools.demo_executor_stream --source-commit '+'a'*40+'\n' in body
    assert 'tools.demo_execution_session' not in body and '--lease-worker' not in body
    assert 'Restart=always\n' in body and 'RestartPreventExitStatus=78\n' in body
    assert 'Environment=ALLOW_ORDER_SUBMISSION=false\n' in body
    assert '\n[Install]' not in body and 'EnvironmentFile=' not in body
    assert 'ProtectHome=true\n' in body and 'ReadWritePaths=/var/lib/quantos-demo-executor\n' in body


@pytest.mark.parametrize('release,uid',[('abc',2001),('a'*40+'\nExecStart=other',2001),('a'*40,0),('a'*40,True)])
def test_invalid_release_or_executor_cannot_render_a_unit(release,uid):
    with pytest.raises(ValueError):render(release_commit=release,executor_uid=uid)
