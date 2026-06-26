"""Frequent-subtree avoidance (structural diversity) — tests-first.

Our return-based de-redundancy asks "does this factor's PnL look like one we have?". This asks
"does this factor's STRUCTURE look like ones we keep finding?" — counting how often each
sub-expression recurs across discovered trees and penalising the search reward of structurally
common candidates, so the search stops re-finding the same formula shape (Lopez de Prado-style
formulaic homogenisation, the mechanism from arXiv 2505.11122). Pure, deterministic.
"""
from services.research.subtree_stats import subtree_keys, structural_commonness, update_corpus

A = ("add", ("leaf", "momentum", "10"), ("leaf", "zscore", "5"))


def test_subtree_keys_enumerate_every_subtree():
    keys = subtree_keys(A)
    assert len(keys) == 3                                  # the add + 2 leaves
    assert sum(k.startswith("(add") for k in keys) == 1 and sum(k.startswith("(leaf") for k in keys) == 2


def test_commonness_rises_as_structure_recurs():
    corpus = {}
    c0 = structural_commonness(A, corpus)                  # unseen -> ~0
    for _ in range(5):
        update_corpus(corpus, A)
    c1 = structural_commonness(A, corpus)                  # seen 5x -> higher
    assert c0 == 0.0 and 0.0 < c1 < 1.0 and c1 > c0


def test_commonness_in_unit_range_and_novel_is_low():
    corpus = {}
    for _ in range(3):
        update_corpus(corpus, A)
    novel = ("mul", ("leaf", "accel", "3"), ("leaf", "volatility", "6"))
    cn = structural_commonness(novel, corpus)
    ca = structural_commonness(A, corpus)
    assert 0.0 <= cn <= 1.0 and cn < ca                    # a structurally novel tree is less common


def test_update_corpus_counts_distinct_subtrees_once_per_tree():
    corpus = {}
    update_corpus(corpus, ("add", ("leaf", "momentum", "3"), ("leaf", "momentum", "3")))
    # the two identical leaves are the SAME structure -> counted once for this tree
    leaf_key = [k for k in corpus if "leaf momentum 3" in k][0]
    assert corpus[leaf_key] == 1
