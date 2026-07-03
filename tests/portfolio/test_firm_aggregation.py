"""Rank #25 — firm-level cross-book aggregation reveals the true firm exposure + hidden overlaps
(a name held by multiple 'independent' books) that per-book views cannot show."""
import math

import pytest

from services.portfolio.firm_aggregation import aggregate_firm_books


def test_overlap_across_books_is_flagged():
    # X is held (long) by BOTH books — a firm-level concentration hidden from each per-book view
    books = {"carry": {"X": 0.5, "Y": -0.5}, "crowd": {"X": 0.5, "Z": -0.5}}
    r = aggregate_firm_books(books)
    assert r["status"] == "AGGREGATED"
    assert r["n_overlaps"] == 1
    ov = r["overlaps"][0]
    assert ov["symbol"] == "X" and ov["n_books"] == 2 and ov["books"] == ["carry", "crowd"]
    assert ov["firm_position"] == pytest.approx(1.0)         # 0.5 + 0.5, summed across books
    # Y and Z are singletons -> not overlaps
    assert {o["symbol"] for o in r["overlaps"]} == {"X"}


def test_firm_net_and_gross_are_the_true_sum():
    books = {"a": {"X": 0.5, "Y": -0.5}, "b": {"X": 0.5, "Z": -0.5}}
    r = aggregate_firm_books(books)
    # firm positions: X +1.0, Y -0.5, Z -0.5  -> gross 2.0, net 0.0
    assert r["firm_gross"] == pytest.approx(2.0)
    assert r["firm_net"] == pytest.approx(0.0)


def test_notional_weighting_scales_contributions():
    books = {"big": {"X": 0.5}, "small": {"X": 0.5}}
    r = aggregate_firm_books(books, book_notionals={"big": 10.0, "small": 1.0})
    # X = 10*0.5 + 1*0.5 = 5.5 ; the big book dominates the firm position
    assert r["overlaps"][0]["firm_position"] == pytest.approx(5.5)


def test_offsetting_positions_net_but_still_gross():
    # one book long X, another short X -> they OFFSET in net but the firm still carries gross risk
    books = {"a": {"X": 0.5, "Y": -0.5}, "b": {"X": -0.5, "Z": 0.5}}
    r = aggregate_firm_books(books)
    x = next(o for o in r["overlaps"] if o["symbol"] == "X")
    assert x["firm_position"] == pytest.approx(0.0)          # net-offset in X
    assert r["firm_gross"] == pytest.approx(1.0)             # but Y and Z remain -> gross 1.0


def test_no_books_fails_closed():
    r = aggregate_firm_books({})
    assert r["status"] == "BLOCKED" and r["reasons"] == ("empty_book_positions",)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_weight_fails_closed(bad):
    r = aggregate_firm_books({"a": {"X": bad}})
    assert r["status"] == "BLOCKED" and r["reasons"][0].startswith("invalid_weight")


def test_deterministic():
    books = {"a": {"X": 0.5, "Y": -0.5}, "b": {"X": 0.5, "Z": -0.5}}
    assert aggregate_firm_books(books) == aggregate_firm_books(books)
