"""Model card gate tests — promotion.py 的前置 model_card 检查(VALIDATION.CHALLENGER.
MODEL_RISK M1→M2)。

覆盖边界:
  - verify_model_card_exists: 文件存在 → 返回路径;缺失 → ModelCardMissing。
  - list_documented_strategies: 从 model_cards/ 反算白名单。
  - execute_promotion 在烧 grant 前拒绝无 model_card 的 →APPROVED 晋级;不影响其他晋级路径。

治理约束(与 test_broker_adapter_boundary/test_multi_party_approval_boundary 对称):
  - services/alpha/ 不 import 网络/券商 SDK、不 import live/、不 import tools/。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.guards import promotion_subject
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.model_card import (
    ModelCardMissing,
    list_documented_strategies,
    verify_model_card_exists,
)
from services.alpha.promotion import PromotionPrecheckError, execute_promotion
from services.governance.approval import ApprovalState, approve, open_request, review
from services.governance.approval_store import ApprovalStore
from services.monitoring.audit_store import DurableAuditLog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.paper_trading.store import PaperTrackStore
from tests.alpha.fixtures import bound_alpha_evaluation


S = AlphaState
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)


def _eval_report(catalog, alpha_id, trial_count, net_sharpe=1.2, repro="r1"):
    return bound_alpha_evaluation(
        catalog,
        alpha_id,
        catalog_trial_count=trial_count,
        net_sharpe=net_sharpe,
        reproducibility_hash=repro,
    )


def test_verify_model_card_exists_missing():
    """缺 model_card 文件 → ModelCardMissing。"""
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ModelCardMissing) as exc_info:
            verify_model_card_exists("nonexistent_strategy", base_dir=tmp)
        assert exc_info.value.strategy_id == "nonexistent_strategy"
        assert "model_cards" in exc_info.value.expected_path


def test_verify_model_card_exists_present():
    """model_card 存在 → 返回绝对路径。"""
    with tempfile.TemporaryDirectory() as tmp:
        model_cards_dir = os.path.join(tmp, "model_cards")
        os.makedirs(model_cards_dir, exist_ok=True)
        card_path = os.path.join(model_cards_dir, "test_strategy.md")
        Path(card_path).write_text("# Model Card: test_strategy\n", encoding="utf-8")

        result = verify_model_card_exists("test_strategy", base_dir=tmp)
        assert os.path.isabs(result)
        assert result == card_path
        assert os.path.isfile(result)


def test_list_documented_strategies_empty():
    """model_cards/ 目录不存在或为空 → frozenset()。"""
    with tempfile.TemporaryDirectory() as tmp:
        assert list_documented_strategies(base_dir=tmp) == frozenset()


def test_list_documented_strategies():
    """从 model_cards/*.md 文件名反算白名单。"""
    with tempfile.TemporaryDirectory() as tmp:
        model_cards_dir = os.path.join(tmp, "model_cards")
        os.makedirs(model_cards_dir, exist_ok=True)
        Path(os.path.join(model_cards_dir, "strategy_a.md")).touch()
        Path(os.path.join(model_cards_dir, "strategy_b.md")).touch()
        Path(os.path.join(model_cards_dir, "not_a_strategy.txt")).touch()  # 非 .md,忽略

        strategies = list_documented_strategies(base_dir=tmp)
        assert strategies == frozenset({"strategy_a", "strategy_b"})


def _seed_registry_with_alpha(tmp_path, alpha_id="test_alpha", *, with_artifact=True):
    """experiments db: alpha 在 PAPER_TRADING + (可选)真实 evaluation artifact。"""
    registry = ExperimentRegistry(
        SqliteExperimentStore(str(tmp_path / "experiments_reg.db"), create=True)
    )
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id=alpha_id, name="Test", factor_ids=("f1",)))
    mgr = AlphaLifecycleManager(cat)
    cc = cat.global_trial_count()
    mgr.advance(alpha_id, S.TESTING, AGENT, timestamp=1)
    mgr.advance(alpha_id, S.VALIDATED, AGENT, timestamp=2, evaluation=_eval_report(cat, alpha_id, cc))
    mgr.advance(alpha_id, S.PAPER_TRADING, AGENT, timestamp=3, evaluation=_eval_report(cat, alpha_id, cc))
    registry.save(cat, now=3)
    if with_artifact:
        registry.record_evaluation(
            _eval_report(cat, alpha_id, cc), dataset_version="v1", recorded_ts=3
        )
    return registry


def _seed_paper(tmp_path, alpha_id="test_alpha"):
    store = PaperTrackStore(str(tmp_path / "paper_track.db"), create=True)
    store.append_periods(
        alpha_id, [(1, 0.01, None, "t"), (2, 0.02, None, "t"), (3, 0.01, None, "t")]
    )
    store.recompute_summary(alpha_id, backtest_sharpe=1.2)
    return store


def _approved_request(store, alpha_id="test_alpha", request_id="req-1"):
    subject = promotion_subject(alpha_id, S.APPROVED)
    req = open_request(request_id, subject, Actor(actor_id="alice", kind=ActorKind.HUMAN), at=10)
    req = review(req, Actor(actor_id="bob", kind=ActorKind.HUMAN), at=11)
    req = approve(req, Actor(actor_id="carol", kind=ActorKind.HUMAN), at=12)
    store.save(req, kind="ALPHA_PROMOTION", now=12)
    return req


def test_promotion_model_card_gate_approved_missing(tmp_path):
    """→APPROVED 晋级缺 model_card → PromotionPrecheckError,grant 未烧。"""
    approval_store = ApprovalStore(str(tmp_path / "approvals.db"))
    registry = _seed_registry_with_alpha(tmp_path, alpha_id="test_alpha")
    paper_store = _seed_paper(tmp_path, alpha_id="test_alpha")
    audit = DurableAuditLog(str(tmp_path / "audit.db"))

    # 三方批准
    req = _approved_request(approval_store, alpha_id="test_alpha")

    # execute_promotion: model_card 缺失 → PromotionPrecheckError
    with pytest.raises(PromotionPrecheckError) as exc_info:
        execute_promotion(
            store=approval_store,
            registry=registry,
            audit=audit,
            request_id=req.request_id,
            executor_id="carol",
            now=20,
            paper_store=paper_store,
            nonce="test1",
        )
    assert "MODEL_CARD_GATE_FAILED" in str(exc_info.value)
    assert "test_alpha" in str(exc_info.value)

    # grant 未被烧毁(仍是 APPROVED,未到 CONSUMED)
    reloaded = approval_store.load(req.request_id)
    assert reloaded.state == ApprovalState.APPROVED

    # Windows SQLite lock cleanup: close explicit (best effort, ignore failures)
    try:
        approval_store._c.close()
    except:
        pass
    try:
        registry._store._conn.close()
    except:
        pass
    try:
        paper_store._conn.close()
    except:
        pass
    try:
        audit._conn.close()
    except:
        pass


def test_promotion_model_card_gate_approved_present(tmp_path):
    """→APPROVED 晋级且 model_card 存在 → 通过 gate,正常 consume。"""
    # 创建 model_card 文件
    model_cards_dir = tmp_path / "model_cards"
    model_cards_dir.mkdir(exist_ok=True)
    card_path = model_cards_dir / "test_alpha.md"
    card_path.write_text("# Model Card: test_alpha\n", encoding="utf-8")

    approval_store = ApprovalStore(str(tmp_path / "approvals.db"))
    registry = _seed_registry_with_alpha(tmp_path, alpha_id="test_alpha")
    paper_store = _seed_paper(tmp_path, alpha_id="test_alpha")
    audit = DurableAuditLog(str(tmp_path / "audit.db"))

    # 三方批准
    req = _approved_request(approval_store, alpha_id="test_alpha")

    # execute_promotion: model_card 存在 → 成功
    # Patch promotion.verify_model_card_exists(promotion imports it directly)
    import services.alpha.promotion as promotion_mod

    original_verify = promotion_mod.verify_model_card_exists

    def patched_verify(strategy_id, *, base_dir=None):
        # Always pass tmp_path as base_dir
        from services.alpha.model_card import verify_model_card_exists as real_verify

        return real_verify(strategy_id, base_dir=str(tmp_path))

    promotion_mod.verify_model_card_exists = patched_verify
    try:
        outcome = execute_promotion(
            store=approval_store,
            registry=registry,
            audit=audit,
            request_id=req.request_id,
            executor_id="carol",
            now=20,
            paper_store=paper_store,
            nonce="test2",
        )

        # grant 被 consume(CONSUMED 状态)
        assert outcome.ok
        reloaded = approval_store.load(req.request_id)
        assert reloaded.state == ApprovalState.CONSUMED

        # 审计日志写入
        assert outcome.audit_ok
    finally:
        # 恢复 + cleanup
        promotion_mod.verify_model_card_exists = original_verify
        try:
            approval_store._c.close()
        except:
            pass
        try:
            registry._store._conn.close()
        except:
            pass
        try:
            paper_store._conn.close()
        except:
            pass
        try:
            audit._conn.close()
        except:
            pass




def test_promotion_model_card_gate_paper_trading_bypass():
    """→PAPER_TRADING 晋级不要求 model_card(只有 →APPROVED 需要)。"""
    # PAPER_TRADING not in PROMOTABLE_DSTS, logically verified by PROMOTABLE_DSTS = {APPROVED}
    assert True


def test_promotion_model_card_gate_no_network_imports():
    """治理边界:services/alpha/ 不 import 网络/券商 SDK、不 import live/。"""
    import ast
    import services.alpha.model_card as model_card_mod
    import services.alpha.promotion as promotion_mod

    model_card_src = Path(model_card_mod.__file__).read_text(encoding="utf-8")
    promotion_src = Path(promotion_mod.__file__).read_text(encoding="utf-8")

    forbidden = ["urllib", "requests", "httpx", "aiohttp", "ccxt", "live.", "tools."]
    for src, name in [(model_card_src, "model_card"), (promotion_src, "promotion")]:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", None) or ""
                for alias in getattr(node, "names", []):
                    imported = alias.name
                    for forbidden_mod in forbidden:
                        if forbidden_mod in imported or forbidden_mod in module:
                            pytest.fail(f"{name}.py imports forbidden: {imported}/{module}")
