from pathlib import Path

import pytest

from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from tools import build_evidence_digests as evidence_digests
from tools.build_evidence_digests import (
    _git_sha,
    extract_macro_regime,
    extract_scenario_tail,
)


def test_dry_run_digest_builder_quarantines_before_store_read(monkeypatch, tmp_path):
    dry_run_db = tmp_path / "dry_run.db"
    monkeypatch.setattr(evidence_digests, "_DRY_RUN_DB", str(dry_run_db))

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        evidence_digests.build_dry_run_digest()

    assert not dry_run_db.exists()


def test_digest_cli_quarantines_before_output_directory_or_json_write(
    monkeypatch, tmp_path
):
    output_dir = tmp_path / "evidence"
    monkeypatch.setattr(evidence_digests, "_EVIDENCE", str(output_dir))
    monkeypatch.setattr(
        evidence_digests,
        "build_json_digest",
        lambda *_args, **_kwargs: pytest.fail("artifact input read before quarantine"),
    )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        evidence_digests.main()

    assert not output_dir.exists()


def _scenario_book(seed: float) -> dict:
    return {
        "n_periods": int(seed * 100),
        "crises": {
            "2022-05 LUNA": {"total_return": seed + 0.01},
            "2022-11 FTX": {"total_return": seed + 0.02},
        },
        "worst_drawdown_window": {"drawdown": seed + 0.03},
        "var_cvar_95": {"cvar": seed + 0.04},
        "hill_tail_index": seed + 2.0,
        "lower_tail_dep_vs_btc": seed + 0.05,
    }


def test_extract_scenario_tail_captures_load_bearing_findings() -> None:
    snapshot = {
        "provenance": "REAL_HISTORICAL_REPLAY",
        "books": {
            "carry": _scenario_book(1.0),
            "crowd_contra": _scenario_book(2.0),
        },
    }

    assert extract_scenario_tail(snapshot) == {
        "carry.n_periods": 100.0,
        "carry.worst_drawdown": 1.03,
        "carry.cvar_95": 1.04,
        "carry.hill_tail_index": 3.0,
        "carry.lower_tail_dep_vs_btc": 1.05,
        "carry.luna_total_return": 1.01,
        "carry.ftx_total_return": 1.02,
        "crowd_contra.n_periods": 200.0,
        "crowd_contra.worst_drawdown": 2.03,
        "crowd_contra.cvar_95": 2.04,
        "crowd_contra.hill_tail_index": 4.0,
        "crowd_contra.lower_tail_dep_vs_btc": 2.05,
        "crowd_contra.luna_total_return": 2.01,
        "crowd_contra.ftx_total_return": 2.02,
    }


def _macro_book(seed: float) -> dict:
    return {
        "n_periods": int(seed * 100),
        "dims": {
            "vol_VIX": {
                "buckets": {
                    "high": {"ann_sharpe": seed + 0.1},
                    "low": {"ann_sharpe": seed + 0.2},
                }
            },
            "rates_2s10s": {
                "buckets": {
                    "normal": {"ann_sharpe": seed + 0.3},
                    "inverted": {"ann_sharpe": seed + 0.4},
                }
            },
            "credit_HYOAS": {"covered_periods": int(seed * 10)},
        },
    }


def test_extract_macro_regime_captures_complementarity_and_coverage() -> None:
    snapshot = {
        "provenance": "REAL_FRED_MACRO",
        "alfred_present": False,
        "books": {
            "carry": _macro_book(1.0),
            "crowd_contra": _macro_book(2.0),
        },
    }

    assert extract_macro_regime(snapshot) == {
        "macro.alfred_present": 0.0,
        "carry.n_periods": 100.0,
        "carry.vix_high_sharpe": 1.1,
        "carry.vix_low_sharpe": 1.2,
        "carry.curve_normal_sharpe": 1.3,
        "carry.curve_inverted_sharpe": 1.4,
        "carry.credit_covered_periods": 10.0,
        "crowd_contra.n_periods": 200.0,
        "crowd_contra.vix_high_sharpe": 2.1,
        "crowd_contra.vix_low_sharpe": 2.2,
        "crowd_contra.curve_normal_sharpe": 2.3,
        "crowd_contra.curve_inverted_sharpe": 2.4,
        "crowd_contra.credit_covered_periods": 20.0,
    }


@pytest.mark.parametrize(
    ("extractor", "snapshot"),
    [
        (extract_scenario_tail, {"provenance": "SYNTHETIC", "books": {}}),
        (
            extract_macro_regime,
            {"provenance": "SYNTHETIC", "alfred_present": False, "books": {}},
        ),
    ],
)
def test_research_extractors_reject_non_real_provenance(extractor, snapshot) -> None:
    with pytest.raises(ValueError, match="provenance"):
        extractor(snapshot)


def test_macro_regime_rejects_ambiguous_alfred_marker() -> None:
    snapshot = {
        "provenance": "REAL_FRED_MACRO",
        "alfred_present": "false",
        "books": {
            "carry": _macro_book(1.0),
            "crowd_contra": _macro_book(2.0),
        },
    }

    with pytest.raises(ValueError, match="alfred_present"):
        extract_macro_regime(snapshot)


def test_git_sha_resolves_linked_worktree_common_refs(tmp_path: Path) -> None:
    sha = "1234567890abcdef1234567890abcdef12345678"
    common = tmp_path / "repo.git"
    worktree_git = common / "worktrees" / "research"
    branch_ref = common / "refs" / "heads" / "codex" / "research"
    worktree = tmp_path / "checkout"
    worktree_git.mkdir(parents=True)
    branch_ref.parent.mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text(
        "gitdir: ../repo.git/worktrees/research\n", encoding="utf-8"
    )
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    (worktree_git / "HEAD").write_text(
        "ref: refs/heads/codex/research\n", encoding="utf-8"
    )
    branch_ref.write_text(f"{sha}\n", encoding="utf-8")

    assert _git_sha(worktree) == sha[:12]
