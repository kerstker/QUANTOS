from types import SimpleNamespace

from quantos.contracts.data import VolumeUnit
from tools.scenario_tail_research import _median_quote_adv


def _bar(close, volume):
    return SimpleNamespace(close=close, volume=volume)


def test_trend_capacity_does_not_multiply_quote_volume_by_price_again():
    bars = [_bar(50_000.0, 1_000_000_000.0), _bar(60_000.0, 2_000_000_000.0)]
    assert _median_quote_adv(bars, volume_unit=VolumeUnit.QUOTE_USD) == 1_500_000_000.0


def test_base_asset_volume_is_converted_to_quote_notional():
    bars = [_bar(100.0, 10.0), _bar(200.0, 20.0)]
    assert _median_quote_adv(bars, volume_unit=VolumeUnit.BASE_ASSET) == 2500.0
