"""The versioned original-history producer reaches the original signed consumer."""
import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case


@pytest.fixture
def streamed_exported(registered_case, monkeypatch, request):
    from tests.research.test_project_chunked_review_request import exported
    from tests.tools.test_gold_stream_history import history_storage
    run = registered_case['run']
    prepare = run.prepare_gold
    def streamed(arguments, **configuration):
        return prepare(arguments, **configuration, history_storage=history_storage())
    monkeypatch.setattr(run, 'prepare_gold', streamed)
    yield from exported.__wrapped__(registered_case, monkeypatch, request)


def test_original_signed_consumer_cold_read_and_nonselected_damage_with_history_v2(
        streamed_exported, monkeypatch):
    from tests.research.test_project_chunked_review_consumption import (
        test_production_signed_consumer_recovery_and_cold_read_use_all_original_reports,
    )
    _, plans, _, _, _ = streamed_exported
    assert all(p['schema']=='QUANTOS_REGISTERED_GOLD_CHUNKED_FORWARD_RUN_V2' for p in plans)
    test_production_signed_consumer_recovery_and_cold_read_use_all_original_reports(
        streamed_exported, monkeypatch)
