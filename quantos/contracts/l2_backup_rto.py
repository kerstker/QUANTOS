"""Operator configuration for restore duration; independent of trading clocks."""

RTO_ENV = 'QUANTOS_BACKUP_RTO_MS'
DEFAULT_RTO_MS = 300_000
MAX_CONFIGURED_RTO_MS = 86_400_000


def validate_rto_ms(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_CONFIGURED_RTO_MS:
        raise ValueError('restore target must be whole milliseconds from 1 through 86400000')
    return value


def parse_rto_ms(value: str) -> int:
    if type(value) is not str or not 1 <= len(value) <= 8 or not value.isascii() or not value.isdecimal():
        raise ValueError('restore target must be an ASCII millisecond integer')
    return validate_rto_ms(int(value))
