"""P0-2 回归:遗留报告不得把权威/可部署标记"写死"在源码里。

2026-09 punch-list(P0-2 legacy authoritative-state cleanup)确认两处源码直接
断言了系统尚未取得的权威:

1. ``tools/alpha_paper_cycle.py`` 的 canonical dry-run 块硬编码
   ``"authoritative": True``,emit 出的标记可以比
   ``is_authoritative_backtest_record()`` 的返回更"权威"。
2. ``services/research/run_registry/indexer.py`` 把资金费 carry bundle 的
   verdict 硬编码为 ``"DEPLOYABLE"``,而该 run kind 在
   ``services/research_runner/run_kinds.py`` 里声明为 DIAGNOSTIC_ONLY(且整个
   kind 已被隔离),没有任何东西可以"挣到"这个结论。

权威只能由 admission 判定算出来,不能由源码写出来。这里的断言只钉住
authority 侧;research-risk 闸门有自己的测试,不在此处重复其契约。"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import tools.alpha_paper_cycle as alpha_paper_cycle
from services.execution.backtest_authority import is_authoritative_backtest_record
from services.research.run_registry.indexer import adapt_carry, index_existing
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from services.research_runner.run_kinds import (
    LEGACY_QUARANTINED_RUN_KINDS,
    RUN_KINDS,
    RunPurpose,
)

ROOT = Path(__file__).resolve().parents[2]

# Every artifact build_report() reads, so the assertions below never depend on
# whatever happens to sit in the repository's live data/cache or evidence tree.
_ARTIFACT_KEYS = (
    "carry_strategy", "carry_walkforward", "oi_research", "oi_verify", "dry_run",
    "carry_dry_run_authoritative", "crowd_dry_run_authoritative", "top_dry_run_diagnostic",
    "top_dry_run_authoritative", "top_verify", "capacity", "forward", "admission",
    "event_health", "event_consistency", "projection_recovery", "gonogo",
)

_CARRY_BOOK = {"sharpe": 0.4}


def _report(tmp_path: Path, **artifacts: dict) -> dict:
    unknown = set(artifacts) - set(_ARTIFACT_KEYS)
    assert not unknown, f"unknown artifact override: {sorted(unknown)}"
    paths = {}
    for name in _ARTIFACT_KEYS:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(artifacts.get(name, {})), encoding="utf-8")
        paths[name] = str(path)
    return alpha_paper_cycle.build_report(artifact_paths=paths)


def _carry_record(**extra: object) -> dict:
    record = {
        "evidence_class": "CANONICAL_L2_DRY_RUN",
        "run_id": "carry-canonical-1",
        "n_periods": 192,
        "pnl": {"sharpe": 99.0, "sharpe_vt": 99.0, "max_drawdown": 0.01,
                "total_return": 9.9, "residual": 0.0},
        "research_risk": {"decision": "RISK_APPROVED"},
        "detail": {"research_risk": {"decision": "RISK_APPROVED"}},
    }
    record.update(extra)
    return record


def _approve_research_risk(monkeypatch) -> None:
    """Isolate the authority gate: the risk gate has its own contract tests."""
    monkeypatch.setattr(alpha_paper_cycle, "full_research_risk_approved", lambda result: True)


def test_alpha_paper_cycle_never_asserts_an_authority_literal():
    """authoritative 标记必须是算出来的表达式,不能是源码里的 True 字面量。"""
    tree = ast.parse((ROOT / "tools" / "alpha_paper_cycle.py").read_text(encoding="utf-8"))
    asserted = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "authoritative"
                and isinstance(value, ast.Constant)
                and value.value is True
            ):
                asserted.append(key.lineno)
    assert asserted == [], f"hardcoded authority claim at line(s) {asserted}"


def test_canonical_dry_run_flag_tracks_the_admission_predicate(tmp_path, monkeypatch):
    """emit 的标记就是 admission 判定对同一条记录的返回,不多也不少。"""
    _approve_research_risk(monkeypatch)
    record = _carry_record(_admit=True)
    monkeypatch.setattr(
        alpha_paper_cycle,
        "is_authoritative_backtest_record",
        lambda r: r.get("_admit") is True,
    )
    block = _report(tmp_path, carry_dry_run_authoritative=record)["alphas"]["carry_oi_majors"]["dry_run"]
    assert isinstance(block, dict)
    assert block["authoritative"] is alpha_paper_cycle.is_authoritative_backtest_record(record)
    assert block["authoritative"] is True


def test_canonical_dry_run_flag_cannot_exceed_a_denied_predicate(tmp_path, monkeypatch):
    """判定拒绝同一记录时,emit 的 flag 不能升格为 True(哪怕其余闸门都放行)。"""
    _approve_research_risk(monkeypatch)
    monkeypatch.setattr(alpha_paper_cycle, "is_authoritative_backtest_record", lambda r: False)
    report = _report(
        tmp_path,
        dry_run={"books": {"carry": dict(_CARRY_BOOK)}},
        carry_dry_run_authoritative=_carry_record(),
    )
    row = report["alphas"]["carry_oi_majors"]
    assert "DRY_RUN_AUTHORITY_INVALID" in row["blockers"]
    # 被拒记录的 99.0 不得变成 headline 结论,也不得带上权威标记。
    assert row["dry_run"] == _CARRY_BOOK


def test_canonical_dry_run_does_not_claim_authority_while_verifier_is_unintegrated(
    tmp_path, monkeypatch
):
    """外部签名验证未接入:其余闸门全开也不能把 canonical 记录标成 authoritative。"""
    _approve_research_risk(monkeypatch)
    record = _carry_record()
    assert is_authoritative_backtest_record(record) is False

    report = _report(
        tmp_path,
        dry_run={"books": {"carry": dict(_CARRY_BOOK)}},
        carry_dry_run_authoritative=record,
    )
    row = report["alphas"]["carry_oi_majors"]
    assert "DRY_RUN_AUTHORITY_INVALID" in row["blockers"]
    assert row["dry_run"] == _CARRY_BOOK


def test_non_full_research_risk_reports_the_current_blocker_vocabulary(tmp_path, monkeypatch):
    """风险未满 9/9 时的 blocker 是当前的 DRY_RUN_RESEARCH_RISK_NOT_FULL。

    旧字符串 DRY_RUN_RESEARCH_RISK_NOT_PASS 已不在任何代码路径上;这里把
    tests/tools/test_alpha_paper_cycle.py 里那条已经空转的断言按现行词表重
    新钉住(该文件不属于本 lane,故在本文件覆盖同一性质)。"""
    monkeypatch.setattr(
        alpha_paper_cycle, "is_authoritative_backtest_record", lambda r: r.get("_admit") is True
    )
    record = _carry_record(_admit=True)
    # 历史四引擎子集:decision 标签是 RISK_APPROVED,但不构成 admission 契约。
    subset_risk = {"decision": "RISK_APPROVED", "status": "APPROVED", "gates_run": 4,
                   "gates_passed": 4, "provenance": "REAL_RESEARCH_RISK_SUBSET"}
    record["research_risk"] = subset_risk
    record["detail"] = {"research_risk": subset_risk}
    row = _report(tmp_path, carry_dry_run_authoritative=record)["alphas"]["carry_oi_majors"]
    assert "DRY_RUN_RESEARCH_RISK_NOT_FULL" in row["blockers"]
    assert "DRY_RUN_RESEARCH_RISK_NOT_PASS" not in row["blockers"]


def test_run_registry_carry_bundle_never_claims_deployable():
    """carry bundle 的 run kind 是 DIAGNOSTIC_ONLY,verdict 不能自己升格成 DEPLOYABLE。"""
    out = adapt_carry({"n_instruments": 115, "n_days": 2186, "sharpe_net_5bps": 2.03,
                       "avg_funding_bps_day": 5.43, "sharpe_price_plus_funding": 2.25,
                       "sharpe_price_only": 1.39, "avg_turnover": 0.28})
    assert out["verdict"] != "DEPLOYABLE"
    assert out["verdict"] == "DIAGNOSTIC_ONLY"
    policy = RUN_KINDS["carry_strategy"].provenance_policy
    assert policy.purpose is RunPurpose.DIAGNOSTIC_ONLY
    assert "carry_strategy" in LEGACY_QUARANTINED_RUN_KINDS


def test_indexed_carry_run_stores_a_diagnostic_verdict(tmp_path):
    """落库的 run 记录也必须是同一结论,不能只在 adapter 里改字符串。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "crypto_carry_strategy.json").write_text(json.dumps(
        {"sharpe_net_5bps": 2.03, "avg_funding_bps_day": 5.43, "n_instruments": 115,
         "n_days": 2186, "sharpe_price_plus_funding": 2.25, "sharpe_price_only": 1.39,
         "avg_turnover": 0.28}), encoding="utf-8")
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    assert index_existing(str(cache), reg, now=1000) == 1
    carry = reg.list(kind="carry_strategy")
    assert len(carry) == 1
    assert carry[0].verdict == RunPurpose.DIAGNOSTIC_ONLY.value
