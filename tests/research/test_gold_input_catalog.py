import hashlib
import os

from services.console.api.gold_input_catalog import catalog
from tests.tools.test_gold_cost_replay import inputs
from tools.gold_cost_inputs import dump_inputs


def test_catalog_uses_exact_worker_bytes_and_exposes_data_window(tmp_path):
    directory = tmp_path / 'data/cache/ai_factory/gold-inputs'
    directory.mkdir(parents=True)
    args = inputs()
    raw = dump_inputs(args)
    (directory / '7.json').write_bytes(raw)
    result = catalog(tmp_path)
    assert result['status'] == 'AVAILABLE'
    assert result['items'][0] == {'input_id': 7, 'input_sha256': hashlib.sha256(raw).hexdigest(),
        'bytes': len(raw), 'start_ms': args['document']['start_ms'],
        'end_ms': max(args['document']['decision_times_ms']), 'frame_count': len(args['frames']),
        'decision_count': len(args['document']['decision_times_ms']), 'source_verified': False}


def test_missing_and_unreadable_inputs_do_not_appear_as_selectable(tmp_path):
    assert catalog(tmp_path)['status'] == 'NO_SNAPSHOT'
    directory = tmp_path / 'data/cache/ai_factory/gold-inputs'
    directory.mkdir(parents=True)
    (directory / '1.json').write_bytes(b'{}')
    os.mkfifo(directory / '2.json')
    (directory / '3.json').symlink_to(directory / '1.json')
    result = catalog(tmp_path)
    assert result['status'] == 'DATA_BLOCKED' and result['items'] == []
    assert len(result['errors']) == 3
