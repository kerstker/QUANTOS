"""Unstarted and interrupted source inputs belong to the same whole review cut."""
import io
import tarfile

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.research.test_project_chunked_review_request import exported as exported


@pytest.mark.parametrize('exported', ['source-unstarted', 'source-partial'], indirect=True)
def test_archive_retains_inputs_from_unstarted_and_empty_partial_runs(exported):
    from services.research.project_research_review_request import open_request, iter_request_archive
    f, plans, _, packet, directory = exported
    invocation = 'd'*64
    material = packet['project_cut']['runs'][invocation]['input_material']
    assert packet['project_cut']['attempt_count'] == 9
    assert packet['project_cut']['all_runs_finalized'] is False
    assert not (f['root']/'project/runs'/invocation/'archive').exists()
    raw = b''.join(iter_request_archive(directory, request_id=directory.name))
    recovered = f['root']/'transported-request'
    recovered.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        # The data filter deliberately discards directory modes. These verified
        # archives contain only regular files and private directory members.
        assert all(member.isfile() or member.isdir() for member in archive.getmembers())
        archive.extractall(recovered, filter='tar')
    with open_request(recovered, request_id=directory.name) as (documents, reports):
        assert documents == packet
        assert set(reports) == {plan['invocation_id'] for plan in plans}
        if material['status'] == 'COMPLETE':
            retained = reports.source_inputs[invocation]
            assert len(retained) == 2
        else:
            assert material['files'] == {} and invocation not in reports.source_inputs
    if material['status'] == 'COMPLETE':
        with pytest.raises(ValueError, match='CONTEXT_CLOSED'):
            retained[0]
        blob = next((recovered/'inputs'/invocation/'input-parts/blobs').iterdir())
        blob.write_bytes(blob.read_bytes()+b' ')
        with pytest.raises(ValueError):
            with open_request(recovered, request_id=directory.name):
                pass
