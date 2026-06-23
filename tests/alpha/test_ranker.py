"""Phase 6 step 8 — robust, diversity-aware ranker: no single outlier metric
dominates; redundant/crowded alphas sink; deterministic + explainable."""
from services.alpha.config import AlphaFactoryConfig
from services.alpha.ranker import rank_alphas

CFG = AlphaFactoryConfig()


def _ev(icir, tstat, cap, hit, dsr, mir, turn):
    return {"icir": icir, "ic_tstat": tstat, "capacity_adj_sharpe": cap,
            "hit_rate": hit, "deflated_sharpe": dsr, "marginal_ir": mir, "turnover": turn}


def test_single_inflated_metric_does_not_reach_top():
    evals = {f"m{i}": _ev(0.4 + 0.02 * i, 1.0 + 0.1 * i, 0.5, 0.5, 0.6, 0.3, 0.5) for i in range(6)}
    evals["steady"] = _ev(0.7, 2.0, 1.0, 0.6, 0.85, 0.8, 0.3)      # strong on everything
    evals["outlier"] = _ev(100.0, 1.0, 0.5, 0.5, 0.6, 0.3, 0.5)    # one wild metric only
    ranks = {r.alpha_id: r.rank for r in rank_alphas(evals, config=CFG)}
    assert ranks["steady"] < ranks["outlier"]                      # robustness holds


def test_redundant_clones_sink_below_independent_alpha():
    strong = _ev(0.7, 2.0, 1.0, 0.6, 0.85, 0.8, 0.3)
    nearly = _ev(0.68, 1.9, 0.98, 0.59, 0.84, 0.78, 0.31)          # B: independent, ~as good
    evals = {"A": strong, "Aclone1": strong, "Aclone2": strong, "B": nearly}
    corr = {
        ("A", "Aclone1"): 0.98, ("A", "Aclone2"): 0.98, ("Aclone1", "Aclone2"): 0.98,
        ("A", "B"): 0.0, ("Aclone1", "B"): 0.0, ("Aclone2", "B"): 0.0,
    }
    ranks = {r.alpha_id: r.rank for r in rank_alphas(evals, config=CFG, correlations=corr, beta=0.5)}
    assert ranks["A"] == 1                                          # one of the family leads
    assert ranks["B"] < ranks["Aclone1"] and ranks["B"] < ranks["Aclone2"]  # B beats the clones


def test_crowding_penalty_demotes_crowded_alpha():
    evals = {"clean": _ev(0.7, 2.0, 1.0, 0.6, 0.85, 0.8, 0.3),
             "crowded": _ev(0.7, 2.0, 1.0, 0.6, 0.85, 0.8, 0.3)}
    ranks = {r.alpha_id: r.rank for r in
             rank_alphas(evals, config=CFG, crowding={"clean": 0.0, "crowded": 0.9}, gamma=0.5)}
    assert ranks["clean"] < ranks["crowded"]


def test_ranking_is_deterministic_and_explainable():
    evals = {"a": _ev(0.6, 1.5, 0.8, 0.55, 0.7, 0.5, 0.4),
             "b": _ev(0.5, 1.2, 0.7, 0.52, 0.65, 0.4, 0.45)}
    r1 = rank_alphas(evals, config=CFG)
    r2 = rank_alphas(evals, config=CFG)
    assert [x.alpha_id for x in r1] == [x.alpha_id for x in r2]
    assert r1[0].z_components and r1[0].reproducibility_hash == r2[0].reproducibility_hash
