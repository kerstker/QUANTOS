"""Explicit limits for a single longer gold research tape, not trading authority."""

EXTENDED_CAPTURE_SCHEMA = 'GOLD_PUBLIC_EXTENDED_CAPTURE_V1'
EXTENDED_STREAM_SCHEMA = 'GOLD_BAR_EXTENDED_STREAM_SUMMARY_V1'
EXTENDED_MAX_SAMPLES = 720
EXTENDED_MAX_SECONDS = 3600


def window_closes(value):
    if (type(value) not in (tuple, list) or not 3 <= len(value) <= 4
            or any(type(t) is not int or not 0 < t < 2**63 - 3600000
                   or t % 900000 for t in value)
            or any(b - a != 900000 for a, b in zip(value, value[1:]))):
        raise ValueError('INVALID_EXTENDED_GOLD_WINDOW')
    return tuple(value)
