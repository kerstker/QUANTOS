import json

from services.console.api.gold_bar_program_status import programs


def fixture(root):
    path = root/'data/cache/gold_bar_research/programs/1'
    path.mkdir(parents=True)
    (path/'plan.json').write_text(json.dumps(dict(schema='GOLD_BAR_PROGRAM_V1',
        program_id=1, rounds=2, window_bars=3, submission_enabled=False)))
    (path/'events.jsonl').write_text(json.dumps(dict(status='COLLECTING',
        attempt=1, observed_at_ms=1000, submission_enabled=False))+'\n')
    return path


def test_recorded_collection_does_not_claim_live_process(tmp_path):
    fixture(tmp_path)
    result = programs(tmp_path)
    assert result['items'][0]['recorded_stage'] == 'COLLECTING'
    assert result['items'][0]['liveness'] == 'UNKNOWN'
    assert result['items'][0]['terminal_status'] is None


def test_terminal_failure_preserves_last_stage(tmp_path):
    path = fixture(tmp_path)
    (path/'summary.json').write_text(json.dumps(dict(status='RESEARCH_PROGRAM_INCOMPLETE',
        rounds=[], error_type='ValueError', authoritative=False,
        economic_eligible=False, submission_enabled=False)))
    row = programs(tmp_path)['items'][0]
    assert row['terminal_status'] == 'RESEARCH_PROGRAM_INCOMPLETE'
    assert row['error_type'] == 'ValueError'
    assert row['completed_rounds'] == 0


def test_partial_tail_is_not_a_completed_event(tmp_path):
    path = fixture(tmp_path)
    with (path/'events.jsonl').open('a') as h:
        h.write('{"status":"ROUND_COMPLETE"')
    assert programs(tmp_path)['items'][0]['recorded_stage'] == 'COLLECTING'


def test_missing_view_never_creates_state(tmp_path):
    assert programs(tmp_path)['status'] == 'NO_SNAPSHOT'
    assert not (tmp_path/'data').exists()


def test_symlink_and_identity_mismatch_are_blocked(tmp_path):
    path = fixture(tmp_path)
    (path/'plan.json').unlink()
    target = tmp_path/'other.json'
    target.write_text('{}')
    (path/'plan.json').symlink_to(target)
    assert programs(tmp_path)['status'] == 'DATA_BLOCKED'
    assert programs(tmp_path)['items'] == []


def test_partial_result_cannot_claim_completed_program(tmp_path):
    path = fixture(tmp_path)
    (path/'summary.json').write_text(json.dumps(dict(status='RESEARCH_PROGRAM_COMPLETE',
        rounds=[], authoritative=False, economic_eligible=False, submission_enabled=False)))
    assert programs(tmp_path)['status'] == 'DATA_BLOCKED'
    assert programs(tmp_path)['items'] == []
