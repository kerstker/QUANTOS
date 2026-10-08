"""Runtime publication must work without the CLI package, as in the console image."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
BLOCK_TOOLS = '''
import importlib.abc, sys
class NoTools(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tools" or fullname.startswith("tools."):
            raise ImportError("CLI package unavailable")
sys.meta_path.insert(0, NoTools())
from pathlib import Path
root = Path(sys.argv[1])
'''
STATEMENT = '''
from services.research.demo_statement import save_statement, load_statement
report = dict(schema="BINANCE_DEMO_STATEMENT_V1", status="NOT_REQUESTED",
    observed_at_ms=2, completed_at_ms=2, start_ms=0, end_ms=1,
    symbols=["BTCUSDT"], source_sha256="a"*64, reads=[], trades=[], income=[], gaps=[])
save_statement(root / "statement", report)
assert load_statement(root / "statement") == report
'''
CAMPAIGN = '''
import asyncio, hashlib, json
from services.research_runner import main
from services.research_runner import factory_campaign
factory_campaign.campaign_tick = lambda *a, **kw: {"status": "IDLE", "label": "研究"}
main.time.time_ns = lambda: 123_000_000
async def check():
    async def published():
        while not (root / "data/cache/ai_factory/campaign-worker.json").is_file():
            await asyncio.sleep(0.01)
    async with main._lifespan(main.app):
        await asyncio.wait_for(published(), 5)
asyncio.run(check())
body = dict(status="IDLE", label="研究", observed_at_ms=123, authoritative=False)
expected = {**body, "result_hash": hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()}
observed = json.loads((root / "data/cache/ai_factory/campaign-worker.json").read_text())
assert observed == expected
'''


@pytest.mark.parametrize('operation', [STATEMENT, CAMPAIGN], ids=['statement', 'campaign'])
def test_runtime_publishes_without_cli_package(tmp_path, operation):
    # Publication destination and all runner state belong to this test.
    (tmp_path / 'data/cache/ai_factory').mkdir(parents=True)
    env = dict(os.environ, QUANTOS_STATE_ROOT=str(tmp_path),
               QUANTOS_FACTORY_CAMPAIGNS_ENABLED='true', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', BLOCK_TOOLS + operation, str(tmp_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
