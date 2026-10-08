"""Complete Gold maintenance scope; legacy observations remain narrower evidence."""
import json

import pytest

from tests.tools.test_demo_gold_research_handoff import request_case as request_case
from tests.tools.test_demo_gold_research_layout import configured as configured
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tests.tools.test_demo_maintenance_processes import proc as proc, process
from tests.tools.test_demo_maintenance_inhibit import runtime as runtime
from tests.tools.test_demo_maintenance_services import Backend
from tools import demo_maintenance_services as services
from tools import demo_maintenance_processes as processes
from tools import demo_maintenance_inhibit as inhibit


GOLD_UNITS = services.ALLOWED_UNITS + ('quantos-gold-pilot.service', 'quantos-research-runner.service')


class GoldRuntime(Backend):
    units = GOLD_UNITS

    def __init__(self):
        super().__init__()
        self.states.update({name: 'inactive' for name in GOLD_UNITS})


def test_fixed_gold_scope_pauses_and_restores_the_original_writing_services(tmp_path):
    backend = GoldRuntime()
    backend.states['quantos-gold-pilot.service'] = 'active'
    backend.states['quantos-research-runner.service'] = 'active'
    journal = tmp_path / 'services.json'
    services.prepare(journal, backend)
    assert services.inspect_journal(journal)['schema'] == 'GOLD_SCOPED_SERVICE_MAINTENANCE_V1'
    services.pause(journal, backend)
    assert set(backend.calls) == {('stop', 'quantos-gold-pilot.service'), ('stop', 'quantos-research-runner.service')}
    services.restore(journal, backend)
    assert all(backend.states[name] == 'active' for name in ('quantos-gold-pilot.service', 'quantos-research-runner.service'))


def test_partial_gold_scope_and_legacy_journal_relabelling_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='UNIT_SCOPE'):
        services.validate_scope(GOLD_UNITS[:-1])
    journal = tmp_path / 'services.json'
    services.prepare(journal, Backend())
    value = json.loads(journal.read_text())
    value['schema'] = 'GOLD_SCOPED_SERVICE_MAINTENANCE_V1'
    journal.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='JOURNAL_SCOPE'):
        services.inspect_journal(journal)


def test_gold_backend_has_fixed_units_but_legacy_backend_cannot_stop_them():
    assert services.GoldSystemdBackend().units == GOLD_UNITS
    with pytest.raises(ValueError, match='UNIT_SCOPE'):
        services.SystemdBackend().idle('quantos-gold-pilot.service')


def test_long_running_gold_or_runner_cannot_be_reported_restored_when_inactive(tmp_path):
    backend = GoldRuntime()
    backend.states['quantos-research-runner.service'] = 'active'
    journal = tmp_path / 'services.json'
    services.prepare(journal, backend)
    services.pause(journal, backend)
    original = backend.change
    def stays_inactive(action, name):
        original(action, name)
        if action == 'start':
            backend.states[name] = 'inactive'
    backend.change = stays_inactive
    with pytest.raises(ValueError, match='RESTORE_INCOMPLETE'):
        services.restore(journal, backend)
    assert services.inspect_journal(journal)['phase'] == 'RESTORING'


def test_gold_inhibitors_cover_all_fixed_units_without_changing_legacy_scope(runtime):
    assert inhibit.install('a' * 64, units=GOLD_UNITS)['files'] == len(GOLD_UNITS)
    assert inhibit.dropin('quantos-gold-pilot.service').exists()
    assert inhibit.dropin('quantos-research-runner.service').exists()
    inhibit.remove('a' * 64, units=GOLD_UNITS)
    assert not list(runtime.glob('*/*.conf'))
    assert 'quantos-gold-pilot.service' not in services.UNITS


@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
@pytest.mark.parametrize('configured', ['multiple'], indirect=True)
def test_idle_second_research_uid_is_visible_without_any_state_handle(request_case, proc):
    f = request_case
    public = f['configured']['public']
    uid = f['configured']['roots'][1]['layout']['uid']
    process(proc, uid=uid)
    # The original regression: the legacy two-UID observer says no process.
    assert processes.observe(executor_uid=public['registry']['executor_uid'],
        issuer_uid=public['registry']['issuer_uid'])['status'] == 'NO_SCOPED_PROCESS_OBSERVED'
    result = processes.observe_gold(verified_layout=f['handle'], verified_public=f['configured']['handle'])
    assert result['status'] == 'SCOPED_PROCESSES_PRESENT'
    assert result['processes'][0]['reasons'] == ['DEDICATED_UID']
    assert result['research_uids'] == sorted(public['registry']['research_uids'])
    assert result['quiescence_proven'] is result['manual_exclusion_proven'] is False


@pytest.mark.parametrize('kind', ['handle', 'mapping', 'context'])
def test_root_metadata_in_every_research_root_is_in_the_observed_scope(request_case, proc, kind):
    f = request_case
    target = str(f['handle'].root_for_uid(f['uid']) / 'private-original.json')
    child = process(proc, uid=0, fd_target=target if kind == 'handle' else None)
    if kind == 'mapping':
        (child / 'maps').write_text('1234000-1235000 rw-s 00000000 00:01 123 ' + target + '\n')
    if kind == 'context':
        (child / 'cwd').unlink()
        (child / 'cwd').symlink_to(f['handle'].root_for_uid(f['uid']))
    result = processes.observe_gold(verified_layout=f['handle'], verified_public=f['configured']['handle'])
    assert result['status'] == 'SCOPED_PROCESSES_PRESENT'
    assert result['processes'][0]['reasons'] == ['STATE_' + kind.upper()]
    assert target not in str(result)


@pytest.mark.parametrize('unit', ['quantos-gold-pilot.service', 'quantos-gold-research-abc-9999.service', 'quantos-research-runner.service'])
def test_gold_cgroups_are_visible_even_if_uid_and_handles_are_unrelated(request_case, proc, unit):
    f = request_case
    process(proc, uid=0, cgroup='/system.slice/' + unit)
    result = processes.observe_gold(verified_layout=f['handle'], verified_public=f['configured']['handle'])
    assert result['processes'][0]['reasons'] == ['GOLD_CGROUP']


def test_unreadable_complete_scope_is_unknown_and_cannot_be_used_as_empty(request_case, proc):
    f = request_case
    child = process(proc)
    (child / 'status').write_text('Uid:\tinvalid\n')
    result = processes.observe_gold(verified_layout=f['handle'], verified_public=f['configured']['handle'])
    assert result['status'] == 'PROCESS_SCAN_INCOMPLETE'
    assert result['processes'] is None


@pytest.mark.parametrize('claim', [True, {}, {'research_uids': [9999]}])
def test_process_scope_requires_the_actual_verified_roster(request_case, proc, claim):
    with pytest.raises(ValueError, match='CAPTURED_HANDLE_REQUIRED'):
        processes.observe_gold(verified_layout=claim, verified_public=request_case['configured']['handle'])
