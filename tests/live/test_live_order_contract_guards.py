"""P1-8 — construction guards on `LiveOrderRequest` / `VenueOrderResult`.

`LiveOrderRequest` is the venue-submission currency: before these guards it
carried any quantity (0, negative, inf, nan, bool, even a str) and any `side`,
and `VenueOrderResult` carried a non-finite/negative fill. A non-finite quantity
defeats downstream comparisons (`0.0 < qty` / `qty > min_qty` are False for NaN)
and poisons the ledger with `inf` arithmetic.

0.0 填充/均价是合法的:PREVIEW 路径(tools/live_track_advance.py 的
`PreviewVenue.place_order`)本来就不提交,如实报 filled_qty=0.0/avg_price=0.0。
"""
import dataclasses
from decimal import Decimal

import pytest

from live.contracts import LiveOrderRequest, VenueOrderResult
from quantos.contracts.trading import OrderSide

_NON_FINITE = (float("inf"), float("-inf"), float("nan"))


def _request(**overrides) -> LiveOrderRequest:
    fields = dict(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                  client_order_id="c1")
    fields.update(overrides)
    return LiveOrderRequest(**fields)


# --- valid default path stays byte-identical --------------------------------

def test_valid_market_request_still_constructs():
    o = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                         client_order_id="c1")
    assert o.order_type == "MARKET" and o.limit_price is None and o.post_only is False


def test_valid_limit_request_still_constructs():
    o = _request(order_type="LIMIT", limit_price=60_000.0, post_only=True)
    assert o.order_type == "LIMIT" and o.limit_price == 60_000.0


def test_integer_quantity_is_accepted():
    assert _request(quantity=1).quantity == 1


# --- side -------------------------------------------------------------------

@pytest.mark.parametrize("bad_side", ["BUY", "buy", None, 1])
def test_side_must_be_an_orderside(bad_side):
    with pytest.raises(ValueError, match="side must be an OrderSide"):
        _request(side=bad_side)


# --- quantity ---------------------------------------------------------------

@pytest.mark.parametrize("bad_qty", [True, False, "0.015", Decimal("0.015"), None])
def test_quantity_must_be_a_real_number_not_bool(bad_qty):
    with pytest.raises(ValueError, match="quantity must be a real number"):
        _request(quantity=bad_qty)


@pytest.mark.parametrize("bad_qty", [0.0, -0.015, 0, *_NON_FINITE])
def test_quantity_must_be_finite_and_positive(bad_qty):
    with pytest.raises(ValueError, match="quantity must be finite and positive"):
        _request(quantity=bad_qty)


# --- ids --------------------------------------------------------------------

@pytest.mark.parametrize("field", ["instrument_id", "client_order_id"])
@pytest.mark.parametrize("bad_id", ["", "   ", None, 123])
def test_ids_must_be_non_empty_strings(field, bad_id):
    with pytest.raises(ValueError, match=f"{field} must be a non-empty string"):
        _request(**{field: bad_id})


# --- the guard survives dataclasses.replace (slicer / emergency paths) -------

def test_replace_revalidates_quantity():
    parent = _request(quantity=1.0)
    child = dataclasses.replace(parent, quantity=0.25, client_order_id="parent1-s0")
    assert child.quantity == 0.25
    with pytest.raises(ValueError, match="quantity must be finite and positive"):
        dataclasses.replace(parent, quantity=0.0)


# --- VenueOrderResult -------------------------------------------------------

def test_valid_fill_result_still_constructs():
    r = VenueOrderResult(venue_order_id="123", client_order_id="c1", instrument_id="BTC",
                         status="FILLED", filled_qty=0.015, avg_price=60_000.0)
    assert r.filled_qty == 0.015 and r.avg_price == 60_000.0


def test_preview_zero_fill_is_allowed():
    r = VenueOrderResult(venue_order_id="PREVIEW", client_order_id="c1",
                         instrument_id="BTC", status="PREVIEW_NOT_SUBMITTED",
                         filled_qty=0.0, avg_price=0.0)
    assert r.filled_qty == 0.0 and r.avg_price == 0.0


@pytest.mark.parametrize("field", ["filled_qty", "avg_price"])
@pytest.mark.parametrize("bad", _NON_FINITE)
def test_venue_order_result_rejects_non_finite_fill(field, bad):
    fields = dict(venue_order_id="123", client_order_id="c1", instrument_id="BTC",
                  status="FILLED")
    fields[field] = bad
    with pytest.raises(ValueError, match=f"{field} must be finite and non-negative"):
        VenueOrderResult(**fields)


@pytest.mark.parametrize("field", ["filled_qty", "avg_price"])
def test_venue_order_result_rejects_negative_fill(field):
    fields = dict(venue_order_id="123", client_order_id="c1", instrument_id="BTC",
                  status="FILLED")
    fields[field] = -0.01
    with pytest.raises(ValueError, match=f"{field} must be finite and non-negative"):
        VenueOrderResult(**fields)
