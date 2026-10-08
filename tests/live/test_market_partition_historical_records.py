"""Historical installed partition validation reuses the original content gate."""
from copy import deepcopy
import json

import pytest

from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_demo_market_partition import book as book, NOW


def test_original_partition_records_validate_without_fixed_path_or_current_key(installed, monkeypatch):
    f=installed; module=f['runtime']; activation=json.loads(f['activation'].read_bytes())
    expected=module._load(role='executor',registry=f['registry'],as_of_ms=NOW+140000)
    monkeypatch.setattr(module,'_read_record',lambda *a,**kw:pytest.fail('historical pure records performed I/O'))
    assert module.validate_installed_records(f['record'],activation,registry=f['registry'],
        source_commit='c'*40,as_of_ms=NOW+140000)==expected
    # Historical verification uses the original cut, not current key lifetime.
    assert module.validate_installed_records(f['record'],activation,registry=f['registry'],
        source_commit='c'*40,as_of_ms=NOW+500000)==expected


@pytest.mark.parametrize('damage',['record','activation','registry','source','time'])
def test_historical_partition_does_not_skip_original_bindings(installed, damage):
    f=installed; record=deepcopy(f['record']); activation=json.loads(f['activation'].read_bytes())
    registry=deepcopy(f['registry']); source='c'*40; at=NOW+140000
    if damage=='record':record['candidate_sha256']='a'*64
    elif damage=='activation':activation['installed_record_sha256']='a'*64
    elif damage=='registry':registry['keys'][0]['valid_until_ms']+=1
    elif damage=='source':source='d'*40
    else:at=record['installed_at_ms']-1
    with pytest.raises(ValueError):
        f['runtime'].validate_installed_records(record,activation,registry=registry,source_commit=source,as_of_ms=at)
