"""Independent architecture review regressions, using synthetic inputs only."""
import pytest
import numpy as np

from tools import rf2_bounded_strategy_experiment as driver


def positive_cases():
    return {
        driver.case_key(candidate, slip): {
            'status': driver.CASE_STATUS_OK,
            'net_pnl': '30' if candidate == 'log_slope90' else '10',
            'annual_pnl': {'2025': 10., '2026': 20.},
            'fills': 12, 'max_drawdown': .05,
            'turnover_notional': '100' if candidate == 'log_slope90' else '200',
        }
        for candidate in driver.CANDIDATES for slip in driver.SLIPPAGES
    }


@pytest.mark.parametrize('fault', ['failed', 'missing', 'extra'])
def test_screen_requires_exactly_twelve_successful_cases(fault):
    cases = positive_cases()
    assert driver._screen(cases)['passed'] is True
    if fault == 'failed':
        cases['cash/2']['status'] = driver.CASE_STATUS_FAILED
    elif fault == 'missing':
        del cases['cash/2']
    else:
        cases['unregistered/2'] = cases['cash/2']
    assert driver._screen(cases)['passed'] is False


def test_output_dotdot_cannot_bypass_forbidden_prefix(tmp_path, monkeypatch):
    source = tmp_path / 'input'
    source.mkdir()
    permitted = tmp_path / 'permitted'
    permitted.mkdir()
    forbidden = tmp_path / 'forbidden'
    forbidden.mkdir()
    monkeypatch.setattr(driver, 'FORBIDDEN_OUTPUT_PREFIXES', (str(forbidden),))
    destination = permitted / '..' / 'forbidden' / 'new-result'
    with pytest.raises(driver.PathRejection):
        driver._validate_paths(source, destination)
    assert not (forbidden / 'new-result').exists()


def test_cli_nonzero_when_any_computation_failed(monkeypatch, capsys):
    monkeypatch.setattr(driver, 'run_experiment', lambda *_: {
        'status': driver.EVIDENCE_CLASS, 'cases': {}, 'failed_cases': 1,
        'completed_cases': 11, 'screening': {'passed': False},
    })
    assert driver.main(['--input', '/unused-input', '--output', '/unused-output']) != 0
    assert 'failed_cases' in capsys.readouterr().out


def test_trial_identity_binds_source_and_registered_plan():
    identity = {'dataset': {'hash': 'data'}, 'source': {'hash': 'source-v1'}, 'plan_sha256': '1' * 64}
    original = driver.planned_trials(identity)
    for changed in ({**identity, 'source': {'hash': 'source-v2'}},
                    {**identity, 'plan_sha256': '2' * 64}):
        assert {p['trial_id'] for p in original}.isdisjoint(
            p['trial_id'] for p in driver.planned_trials(changed))


def test_final_identity_drift_has_durable_invalidated_marker(tmp_path, monkeypatch):
    source = tmp_path / 'input'
    source.mkdir()
    destination = tmp_path / 'output'
    monkeypatch.setattr(driver, '_freeze_identity', lambda: {
        'source': {'hash': 'synthetic'}, 'plan_sha256': driver._plan_digest(),
    })
    monkeypatch.setattr(driver, 'load_data', lambda **_: (
        {'start': 0, 'end': 86_400_000, 'times': []}, np.array([], dtype=int),
        np.empty((0, 5)), None, None, {},
    ))
    monkeypatch.setattr(driver, '_run_case', lambda *args: {'status': driver.CASE_STATUS_OK})
    calls = 0

    def check(_):
        nonlocal calls
        calls += 1
        # Two loading checks, two per case, then final publication check.
        if calls == 27:
            raise driver.IdentityDriftError('synthetic final drift')

    monkeypatch.setattr(driver, '_check_source_and_plan', check)
    with pytest.raises(driver.IdentityDriftError):
        driver.run_experiment(source, destination)
    assert not (destination / 'summary.json').exists()
    assert (destination / 'invalidated.json').exists()
