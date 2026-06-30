from services.execution.dry_run_pipeline import plan_target_quantities


def test_target_quantities_round_and_skip_bad_price():
    w = {"BTC": 0.5, "ETH": -0.5, "DOGE": 0.1}
    px = {"BTC": 100.0, "ETH": 50.0, "DOGE": 0.0}  # DOGE price 0 -> skipped
    q = plan_target_quantities(w, px, aum=10000.0)
    assert q == {"BTC": 50, "ETH": -100}  # 0.5*1e4/100=50 ; -0.5*1e4/50=-100 ; DOGE dropped


def test_target_quantities_drops_zero_and_missing_price():
    w = {"BTC": 0.0001, "ETH": 0.5, "XRP": 0.5}
    px = {"BTC": 100.0, "ETH": 50.0}  # XRP price missing
    q = plan_target_quantities(w, px, aum=1000.0)
    assert q == {"ETH": 10}  # BTC rounds to 0 -> dropped ; XRP missing -> dropped
