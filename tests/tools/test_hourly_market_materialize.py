import json
from pathlib import Path

import pytest

from services.data.hourly_universe import UniversePolicy
from tools.hourly_market_materialize import materialize_hourly_market


def test_materializer_import_and_contract(tmp_path: Path):
    raw = tmp_path / "raw"
    out = tmp_path / "out"
    raw.mkdir()
    (raw / "records.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(Exception):
        materialize_hourly_market(raw_root=raw, output_root=out, as_of=4 * 3600, policy=UniversePolicy(min_size=1, max_size=1))


def test_cli_requires_explicit_as_of():
    import subprocess, sys
    result = subprocess.run([sys.executable, "-m", "tools.hourly_market_materialize", "--raw-root", ".", "--output-root", "out"], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode != 0
    assert "as-of" in (result.stderr + result.stdout)
