"""One immutable report walk per lease, with unchanged binding and exit checks."""
import copy

import pytest

from tests.research.test_project_result_material_reader import original_population as original_population
from tests.tools.test_registered_research_run import registered_case as registered_case


def test_complete_report_walks_do_not_grow_with_verifiers_inside_one_lease(
        original_population, monkeypatch):
    from quantos.contracts import project_chunked_results as chunked
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    fixture, _, cut = original_population
    calls = []
    emit = chunked.json_parts

    def counted(value, **options):
        calls.append(value)
        yield from emit(value, **options)

    monkeypatch.setattr(chunked, 'json_parts', counted)
    provider = ProjectResultMaterialReader(fixture['root'] / 'project')
    with provider.reports(cut) as reports:
        initial_walks = len(calls)
        assert initial_walks == len(reports)
        for _ in range(10):
            chunked.full_report_population(cut, reports)
        assert len(calls) == initial_walks, 'repeated verification rewalks the same pinned full report'


def test_reused_report_rejects_a_changed_original_binding(original_population):
    from quantos.contracts import project_chunked_results as chunked
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    fixture, plans, cut = original_population
    invocation = plans[0]['invocation_id']
    with ProjectResultMaterialReader(fixture['root'] / 'project').reports(cut) as reports:
        chunked.report_for(cut['runs'][invocation], reports)
        changed = copy.deepcopy(cut['runs'][invocation])
        changed['originals']['archive/report.json']['sha256'] = 'f' * 64
        with pytest.raises(ValueError):
            chunked.report_for(changed, reports)


def test_reuse_rejects_an_index_rewrite_during_the_next_verification(original_population):
    from quantos.contracts import project_chunked_results as chunked
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    fixture, plans, cut = original_population
    invocation = plans[0]['invocation_id']
    rejected_inside_call = False
    with pytest.raises(ValueError, match='CHANGED'):
        with ProjectResultMaterialReader(fixture['root'] / 'project').reports(cut) as reports:
            kept = chunked.report_for(cut['runs'][invocation], reports)
            path = kept.index.path
            path.write_bytes(path.read_bytes())
            try:
                chunked.report_for(cut['runs'][invocation], reports)
            except ValueError:
                rejected_inside_call = True
    assert rejected_inside_call, 'a reused proof must reject changed backing index before returning'


def test_repeated_checks_keep_nonselected_original_exit_revalidation(original_population):
    from quantos.contracts import project_chunked_results as chunked
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    fixture, plans, cut = original_population
    with pytest.raises(ValueError, match='CHANGED'):
        with ProjectResultMaterialReader(fixture['root'] / 'project').reports(cut) as reports:
            for _ in range(3):
                chunked.full_report_population(cut, reports)
            blob = next((fixture['directories']['two'] / 'archive/report-parts/blobs').iterdir())
            blob.write_bytes(blob.read_bytes())
    with pytest.raises(ValueError, match='CLOSED'):
        chunked.report_for(cut['runs'][plans[0]['invocation_id']], reports)


def test_prepared_report_cannot_replace_its_stream_or_native_view(original_population):
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    fixture, plans, cut = original_population
    with ProjectResultMaterialReader(fixture['root'] / 'project').reports(cut) as reports:
        kept = reports[plans[0]['invocation_id']]
        with pytest.raises(AttributeError):
            kept.items = lambda: iter(())
        with pytest.raises(AttributeError):
            kept.index = None


def test_mapping_claiming_a_verification_cannot_bypass_complete_bytes(original_population):
    from quantos.contracts import project_chunked_results as chunked
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from tests.research.test_perpetual_chunked_result_reader import plain
    fixture, plans, cut = original_population
    invocation = plans[0]['invocation_id']
    with ProjectResultMaterialReader(fixture['root'] / 'project').reports(cut) as reports:
        class Claim(dict):
            def verify_for(self, run):
                pytest.fail('a caller-provided claim is not an owning prepared view')
        forged = Claim(plain(reports[invocation]))
        forged['result_hash'] = 'f' * 64
        with pytest.raises(ValueError, match='REPORT_BYTES'):
            chunked.report_for(cut['runs'][invocation], {invocation: forged})


def test_prepared_stream_cannot_be_deleted(original_population):
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    fixture, plans, cut = original_population
    with ProjectResultMaterialReader(fixture['root'] / 'project').reports(cut) as reports:
        kept = reports[plans[0]['invocation_id']]
        with pytest.raises(AttributeError):
            del kept.items


def test_owner_closure_immediately_invalidates_prepared_verification(original_population):
    from quantos.contracts import project_chunked_results as chunked
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    fixture, plans, cut = original_population
    invocation = plans[0]['invocation_id']
    with ProjectResultMaterialReader(fixture['root'] / 'project').reports(cut) as reports:
        chunked.report_for(cut['runs'][invocation], reports)
        reports.close_inputs()
        with pytest.raises(ValueError, match='CLOSED'):
            chunked.report_for(cut['runs'][invocation], reports)
