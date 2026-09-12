import pytest

from scripts.render_demo_recovery_unit import render


@pytest.mark.parametrize('release,uid', [('abc',2001), ('a'*40+'\nExecStart=wrong',2001), ('a'*40,0), ('a'*40,True)])
def test_renderer_rejects_unpinned_release_and_root_or_injected_unit(release, uid):
    with pytest.raises(ValueError): render(release_commit=release, executor_uid=uid)


def test_recovery_unit_binds_exact_release_and_executor_without_enablement_or_submission():
    body = render(release_commit='a'*40, executor_uid=2001)
    assert 'User=2001\n' in body
    assert ' -m tools.demo_resident_recovery --source-commit '+'a'*40+' --serve\n' in body
    assert 'Environment=ALLOW_ORDER_SUBMISSION=false\n' in body
    assert '\n[Install]' not in body and 'EnvironmentFile=' not in body and 'ExecStartPre=' not in body
    assert 'ReadWritePaths=/var/lib/quantos-demo-executor\n' in body
    assert '/srv/quantos/state' not in body and '/home/' not in body
