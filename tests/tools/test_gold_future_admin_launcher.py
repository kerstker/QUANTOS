"""The root launcher must use the actual administrative worker CLI contract.

Only the final worker body is isolated: argv parsing is real, and no systemd
unit, protected path, signing key or administrative operation is invoked.
"""
import pytest

from tools import demo_issuer_group_admin as worker
from tools import gold_future_budget_install as installer


@pytest.mark.parametrize('purpose', ['GROUP_CONTINUITY', 'DELEGATION_V2_SIGN'])
def test_root_launch_arguments_reach_native_admin_parser_with_exact_purpose(monkeypatch, purpose):
    source = 'c' * 40
    command = installer.admin_command(source_commit=source,
        registry={'executor_uid': 2001, 'issuer_uid': 2002}, purpose=purpose,
        unit='quantos-demo-issuer-isolated1234')
    module_at = command.index('-m')
    assert command[module_at + 1] == 'tools.demo_issuer_group_admin'
    calls = []

    def isolated_run(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(worker, 'run', isolated_run)
    assert worker.main(command[module_at + 2:]) == 0
    assert calls == [{'source_commit': source, 'purpose': purpose}]
