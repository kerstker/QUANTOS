import asyncio
import json
import shutil
from pathlib import Path

import pytest

from tests.tools.test_gold_bar_definition import archive
from tests.tools.test_gold_bar_observe import recorded
from tests.tools.test_gold_strategy_entry import origin_at
from tests.research.test_gold_bar_dataset import STEP, raw_rows


@pytest.mark.parametrize('use_prepared', [False, True])
def test_restore_replays_original_entry_identity_without_renewing_deadline(tmp_path, monkeypatch, use_prepared):
    from tools import gold_bar_forward as forward
    from tools.gold_bar_demo_entry import restore_engine
    original = archive(tmp_path)
    source = Path(__file__).resolve().parents[2]
    from tools.gold_bar_definition import PreparedBarDefinition
    prepared = PreparedBarDefinition(original, candidate='channel', source_directory=source) if use_prepared else None
    options = {'prepared_definition':prepared} if use_prepared else {}
    result, journal = recorded(tmp_path, monkeypatch)
    end = raw_rows()[-1][6]+1
    monkeypatch.setattr(forward, 'now_ms', lambda: end+100)
    monkeypatch.setattr(forward, 'source_identity', lambda: {'fixture': True})

    async def capture(history, output, *, seconds, on_closed):
        shutil.copytree(journal, output)
        monkeypatch.setattr(forward, 'now_ms', lambda: end+STEP+21)
        on_closed(json.loads((output / 'decisions.jsonl').read_text()))
        return result

    monkeypatch.setattr(forward, 'observe', capture)
    directory = tmp_path / 'forward'
    asyncio.run(forward.run(original, candidate='channel', source_directory=source,
                           history=tmp_path / 'history', output=directory, seconds=1, **options))
    engine = restore_engine(directory, archive=original, source_directory=source, **options)
    row = json.loads((directory / 'selected-decisions.jsonl').read_text())
    assert engine.entry_signal(row['received_at_ms']) == row['signal']
    at = row['received_at_ms']+5000
    with pytest.raises(ValueError, match='SIGNAL_EXPIRED'):
        engine.prepare(origin=origin_at(at), now_ms=at)
    (directory / 'live-signals.jsonl').write_text('')
    with pytest.raises(ValueError, match='LIVE_COUNT'):
        restore_engine(directory, archive=original, source_directory=source, **options)
