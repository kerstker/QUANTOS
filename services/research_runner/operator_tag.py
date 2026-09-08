"""Versioned Unicode transport for self-asserted research tags, not authentication."""
import base64
import binascii

OPERATOR_ENCODING = 'base64-utf8-v1'
MAX_OPERATOR_CHARACTERS = 128
MAX_ENCODED_CHARACTERS = 684  # 128 Unicode code points, at most four UTF-8 bytes each.


def decode_operator_tag(value: str | None, encoding: str | None = None) -> str:
    if value is None:
        raise ValueError('X-Operator header required')
    if encoding is not None:
        if encoding != OPERATOR_ENCODING or len(value) > MAX_ENCODED_CHARACTERS:
            raise ValueError('invalid operator encoding')
        try:
            raw = base64.b64decode(value, validate=True)
            if base64.b64encode(raw).decode('ascii') != value:
                raise ValueError('non-canonical operator encoding')
            value = raw.decode('utf-8', errors='strict')
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise ValueError('invalid UTF-8 operator tag') from exc
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        raise ValueError('operator tag must not contain control characters')
    value = value.strip()
    if not 1 <= len(value) <= MAX_OPERATOR_CHARACTERS:
        raise ValueError('X-Operator must contain 1 to 128 characters')
    return value
