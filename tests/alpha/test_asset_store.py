"""AlphaAssetRegistry 的 sqlite 持久层(services/alpha/asset_store.py,Phase 2 P3-4):跨会话保住富元数据
(owner/generator/tags/version/evaluation/lineage,正是内存 dict 跨会话丢的),fail-closed + immutable + 篡改检测。"""
import os

import pytest

from quantos.contracts.alpha_asset import AlphaAsset, AlphaLineage
from quantos.contracts.research import AlphaState
from services.alpha.asset_store import (AlphaAssetStoreError, ImmutableAlphaAssetError,
                                        SqliteAlphaAssetStore, load_registry, save_registry)
from services.alpha.registry import AlphaAssetRegistry


def _asset(aid, version=1, **kw):
    return AlphaAsset(alpha_id=aid, expression=kw.pop("expression", "close"), version=version, **kw)


def _store(tmp_path, name="a.db"):
    return SqliteAlphaAssetStore(os.path.join(str(tmp_path), name), create=True)


def test_roundtrip_preserves_rich_metadata(tmp_path):
    reg = AlphaAssetRegistry()
    reg.add(_asset("a", 1, generator="cs_genome", owner="alice", tags=("carry",)), ts=5)
    reg.add(_asset("a", 2, generator="cs_genome", owner="alice", tags=("carry", "v2")), ts=9)
    reg.add(_asset("b", 1, generator="gp_tree", owner="bob"), ts=7)
    store = _store(tmp_path)
    save_registry(reg, store)
    loaded = load_registry(store)
    assert loaded.all_ids() == ("a", "b")
    assert loaded.versions("a") == (1, 2)
    assert loaded.latest("a").owner == "alice" and loaded.latest("a").tags == ("carry", "v2")
    assert loaded.latest("b").generator == "gp_tree"
    # 逐 asset content hash 全等(富元数据无损)
    assert loaded.latest("a").asset_hash == reg.latest("a").asset_hash


def test_children_query_survives_roundtrip(tmp_path):
    reg = AlphaAssetRegistry()
    reg.add(_asset("p", 1))
    reg.add(_asset("c1", 1, lineage=AlphaLineage(parent_ids=("p",), mutation_op="mutate")))
    reg.add(_asset("c2", 1, lineage=AlphaLineage(parent_ids=("p", "q"), mutation_op="crossover")))
    store = _store(tmp_path)
    save_registry(reg, store)
    loaded = load_registry(store)
    assert {a.alpha_id for a in loaded.children_of("p")} == {"c1", "c2"}
    assert loaded.children_of("nobody") == ()


def test_insert_asset_immutable_and_idempotent(tmp_path):
    store = _store(tmp_path)
    a = _asset("a", 1)
    assert store.insert_asset("a", 1, a.model_dump_json(), a.asset_hash, 0) is True
    assert store.insert_asset("a", 1, a.model_dump_json(), a.asset_hash, 0) is False   # 幂等 no-op
    b = _asset("a", 1, owner="different")
    with pytest.raises(ImmutableAlphaAssetError):
        store.insert_asset("a", 1, b.model_dump_json(), b.asset_hash, 0)               # 异内容拒


def test_uninitialized_store_fails_closed(tmp_path):
    empty = os.path.join(str(tmp_path), "empty.db")
    open(empty, "w").close()                                                            # 空文件,未建表
    store = SqliteAlphaAssetStore(empty, create=False)
    with pytest.raises(AlphaAssetStoreError):
        store.all_assets()


def test_load_detects_tampered_asset_json(tmp_path):
    reg = AlphaAssetRegistry()
    reg.add(_asset("a", 1, owner="alice"))
    store = _store(tmp_path)
    save_registry(reg, store)
    # 直接篡改 asset_json(改 owner)但不改 content_hash -> 加载重算 hash 不符 -> raise
    store._conn.execute("UPDATE alpha_assets SET asset_json=REPLACE(asset_json,'alice','mallory')")
    store._conn.commit()
    with pytest.raises(AlphaAssetStoreError):
        load_registry(store)


def test_forged_approved_rejected_on_restore(tmp_path):
    # 盘里放一个 status=APPROVED 但无 lifecycle_event_ids 的 asset(content_hash 自洽,过篡改检测)
    forged = _asset("x", 1, status=AlphaState.APPROVED)
    store = _store(tmp_path)
    store.insert_asset("x", 1, forged.model_dump_json(), forged.asset_hash, 0)
    # restore 过 containment 守卫 -> 拒(非人不能经持久层后门到 APPROVED)
    with pytest.raises(ValueError):
        load_registry(store)


def test_save_is_idempotent_accumulate_cross_session(tmp_path):
    reg = AlphaAssetRegistry()
    reg.add(_asset("a", 1, owner="alice"))
    store = _store(tmp_path)
    save_registry(reg, store)
    save_registry(reg, store)                                    # 二次 save 稳定
    assert len(store.all_assets()) == 1
    reg2 = load_registry(store)
    reg2.add(_asset("a", 2, owner="alice"))                       # 跨会话追加新 version
    save_registry(reg2, store)
    assert len(store.all_assets()) == 2                           # v1 不变 + v2 追加(单调累积)


def test_audit_log_roundtrip_order(tmp_path):
    reg = AlphaAssetRegistry()
    reg.add(_asset("a", 1), ts=5)
    reg.add(_asset("b", 1), ts=9)
    store = _store(tmp_path)
    save_registry(reg, store)
    loaded = load_registry(store)
    got = [(e["alpha_id"], e["version"], e["ts"]) for e in loaded.audit_log()]
    assert got == [("a", 1, 5), ("b", 1, 9)]
