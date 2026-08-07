from tools.validation_dag_production_check import run_production_validation_check


def test_zero_monkeypatch_production_validation_check_passes_and_blocks(tmp_path):
    report = run_production_validation_check(tmp_path)

    assert report["pass"]["verdict"] == "PASS"
    assert report["pass"]["stages"] == ["cheap", "oos", "realism"]
    assert report["self_reported_positive"]["verdict"] == "BLOCK"
    assert report["self_reported_positive"]["blocked_stage"] == "oos"
    assert report["missing"]["verdict"] == "BLOCK"
    assert report["missing"]["blocked_stage"] == "cheap"
    assert report["malformed"]["verdict"] == "BLOCK"
    assert report["malformed"]["blocked_stage"] == "oos"
    assert report["malformed_realism"]["verdict"] == "BLOCK"
    assert report["malformed_realism"]["blocked_stage"] == "realism"
