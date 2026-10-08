"""Bounded association of an income timestamp with an identified trade.

The Demo observations include whole-second income times beside millisecond
trade times. This explicit comparison policy retains both reported values. It
does not assert that settlement and execution occurred at the same instant.
Callers must independently bind account, symbol, trade ID, type and amount.
"""
POLICY='EXACT_MS_OR_REPORTED_SECOND_CONTAINS_TRADE_V1'


def association(income_ms,trade_ms):
    if type(income_ms) is not int or type(trade_ms) is not int or not 0<=income_ms<=trade_ms<2**53:
        return None
    if income_ms==trade_ms:return 'EXACT_MILLISECOND'
    if income_ms%1000==0 and trade_ms-income_ms<1000:return 'REPORTED_SECOND_CONTAINS_TRADE'
    return None
