"""A data-only panel must not import the quarantined alpha backtest engine."""
import os
from pathlib import Path
import subprocess
import sys


def test_feature_conversion_does_not_import_alpha_engine():
    code = '''
import importlib.abc, sys
class NoAlpha(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "services.alpha" or fullname.startswith("services.alpha."):
            raise ImportError("alpha engine unavailable")
sys.meta_path.insert(0, NoAlpha())
from services.research.alpha_expression import FieldPanel, panel_to_periods
panel = FieldPanel(("A", "B"), (1, 2), {"close": [[100, 50], [110, 45]]})
periods = panel_to_periods([[1, -1], [1, -1]], panel)
assert len(periods) == 1
assert periods[0].event_time == 1
assert periods[0].instruments == ("A", "B")
assert abs(periods[0].forward_returns[0] - 0.1) < 1e-12
assert abs(periods[0].forward_returns[1] + 0.1) < 1e-12
'''
    result = subprocess.run([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[2],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_legacy_panel_global_resolves_to_shared_type():
    import pickle
    from dataclasses import FrozenInstanceError
    import pytest
    from quantos.contracts.panel import PanelPeriod
    from services.alpha.panel_backtest import PanelPeriod as LegacyPanelPeriod
    assert LegacyPanelPeriod is PanelPeriod
    # Existing pickle GLOBAL references keep resolving through the old export.
    assert pickle.loads(b'cservices.alpha.panel_backtest\nPanelPeriod\n.') is PanelPeriod
    period = PanelPeriod(('A', 'B'), (1.0, -1.0), (0.1, -0.1), event_time=7)
    assert pickle.loads(pickle.dumps(period)) == period
    with pytest.raises(FrozenInstanceError):
        period.event_time = 8
