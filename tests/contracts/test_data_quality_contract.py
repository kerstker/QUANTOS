def test_volume_dispersion_check_has_one_contract_owned_identity():
    from quantos.contracts.quality import check_volume_dispersion as canonical
    from services.data.quality import check_volume_dispersion as data_compat
    from services.research.realism.capacity_curve import (
        check_volume_dispersion as research_consumer,
    )

    assert data_compat is canonical
    assert research_consumer is canonical
