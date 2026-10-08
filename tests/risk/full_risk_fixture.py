"""Nine computed reports over synthetic history; no evidence or trade authority."""


def complete_engine_reports():
    from tests.validation.test_research_risk_provenance import _benign, _bundle, _full_result

    returns, weights, equity, timestamps = _benign()
    result = _full_result(returns, weights, equity, timestamps, _bundle(returns, weights, timestamps))
    assert result['status'] == 'APPROVED' and result['gates_run'] == result['gates_passed'] == 9
    return result['reports']
