"""Fixed Gold schema on the shared complete, physically pinned disk reader."""
from quantos.contracts import gold_chunked_result as contract
from services.research.perpetual_chunked_result_reader import _open_result


def open_gold_result(directory, index_directory, *, expected_manifest_sha256,
                     maximum_source_bytes, maximum_index_bytes, maximum_nodes=100_000_000):
    return _open_result(directory, index_directory, expected_manifest_sha256=expected_manifest_sha256,
        maximum_source_bytes=maximum_source_bytes, maximum_index_bytes=maximum_index_bytes,
        maximum_nodes=maximum_nodes, manifest_contract=contract, result_binding=contract.bind_result)
