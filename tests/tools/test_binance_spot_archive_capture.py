import hashlib
import io
import zipfile

import pytest

from tools.binance_spot_archive_capture import verify_archive, minute_timestamp


def test_spot_microsecond_transition_and_alignment():
    assert minute_timestamp('1735689600000000') == 1735689600000
    assert minute_timestamp('1704067200000') == 1704067200000
    for bad in ['1735689600000001', '1704067200001', '-1', 'nan']:
        with pytest.raises(ValueError):
            minute_timestamp(bad)


def test_official_checksum_and_single_member_are_required():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('BTCUSDT-1m-2023-01.csv', 'data')
    raw = buf.getvalue()
    name = 'BTCUSDT-1m-2023-01.zip'
    digest = hashlib.sha256(raw).hexdigest()
    assert verify_archive(raw, f'{digest}  {name}', name) == digest
    for checksum in [f'{"0"*64}  {name}', f'{digest}  ETHUSDT.zip']:
        with pytest.raises(ValueError):
            verify_archive(raw, checksum, name)
