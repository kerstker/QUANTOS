"""真前向 paper-track 核心(Stage 0,实盘路线)的测试 —— 设计:
docs/engineering/specs/2026-07-04-forward-track-live-design.md。

定义性约束:决策(权重)必须在结果存在之前落库;兑现只用已落库的权重 + 之后到达的价格;
无前视(min_gap 之内绝不兑现);覆盖率不足 fail-closed 留 pending;与现有 paper_track 两表共存互不扰。
"""

import json
import sqlite3
from dataclasses import replace

import pytest

from services.research.paper_trading.forward import realize_due
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.paper_trading.store import PaperTrackStore
from services.research.ports.persistence import ForwardLedgerError

_DAY = 86_400_000
_WEEK = 7 * _DAY
_T0 = 1_780_000_000_000  # 真实纪元附近的毫秒时间戳(非合成纪元)


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "paper_track.db")
    store = PaperTrackStore(path, create=True)
    ledger = ForwardLedger(path, create=True)
    return store, ledger


def _ref(scope=("BTC", "ETH")):
    return ForwardAdmissionRef(
        admission_id="admission-1",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=_T0 - 1,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=tuple(sorted(scope)),
    )


def _realize_with_evidence(ledger, store, alpha_id, **kwargs):
    return realize_due(
        ledger,
        store,
        alpha_id,
        quality_evidence_current=True,
        **kwargs,
    )


def _commit(ledger, *, realized_return=0.01, inputs=None, admission_ref=None):
    return ledger.commit_realization(
        "a",
        _T0,
        as_of_ts=_T0 + _WEEK,
        realized_return=realized_return,
        realized_ic=0.5,
        source="FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING",
        inputs=inputs
        or {
            "BTC": {"weight": 1.0, "price_return": 0.011, "funding": 0.001},
        },
        admission_ref=admission_ref or _ref(scope=("BTC",)),
        committed_at_ms=_T0 + _WEEK + 1,
    )


def test_record_decision_is_idempotent_but_conflict_fails_closed(db):
    _, ledger = db
    w = {"BTC": 0.5, "ETH": -0.5}
    kwargs = dict(recorded_at=_T0, weights=w, admission_ref=_ref())
    assert ledger.record_decision("crowd_contra", _T0, **kwargs) is True
    # 同 decided_ts 重录:忽略,不覆盖(即便权重不同——决策不可改写)
    assert ledger.record_decision("crowd_contra", _T0, **kwargs) is False
    with pytest.raises(Exception, match="identity reused"):
        ledger.record_decision(
            "crowd_contra",
            _T0,
            recorded_at=_T0,
            weights={"BTC": 1.0},
            admission_ref=_ref(),
        )
    decs = ledger.decisions("crowd_contra")
    assert len(decs) == 1
    assert json.loads(decs[0]["weights_json"]) == w  # 原始决策原样保留
    assert decs[0]["recorded_at"] == _T0
    assert decs[0]["realized"] == 0
    assert ledger.decision_chain_valid()


def test_paper_period_replay_is_idempotent_but_conflict_fails_closed(db):
    store, _ = db
    row = (_T0, 0.01, 0.2, "FORWARD_LIVE")

    store.append_periods("a", [row])
    store.append_periods("a", [row])

    assert len(store.periods("a")) == 1
    with pytest.raises(Exception, match="reused with different content"):
        store.append_periods("a", [(_T0, 0.02, 0.2, "FORWARD_LIVE")])
    assert store.periods("a")[0]["realized_return"] == pytest.approx(0.01)


def test_realization_and_period_commit_atomically_and_replay_idempotently(db):
    store, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )

    assert _commit(ledger) is True
    assert _commit(ledger) is False

    realizations = ledger.attested_realizations("a")
    assert len(realizations) == 1
    assert realizations[0]["realized_return"] == pytest.approx(0.01)
    assert len(store.periods("a")) == 1
    assert ledger.decisions("a")[0]["realized"] == 1
    assert ledger._conn.execute("SELECT COUNT(*) FROM forward_local_events").fetchone()[0] == 1
    assert ledger.realization_chain_valid()


def test_realization_identity_conflict_rolls_back(db):
    store, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )
    _commit(ledger)

    with pytest.raises(Exception, match="realization identity reused"):
        _commit(ledger, realized_return=0.02)

    assert len(ledger.attested_realizations("a")) == 1
    assert len(store.periods("a")) == 1
    assert ledger._conn.execute("SELECT COUNT(*) FROM forward_local_events").fetchone()[0] == 1


@pytest.mark.parametrize(
    "target_table",
    ["forward_realizations", "paper_periods", "forward_local_events"],
)
def test_realization_commit_rolls_back_every_authoritative_write_on_insert_failure(
    db, target_table
):
    store, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )
    ledger._conn.execute(
        f"CREATE TEMP TRIGGER fail_{target_table} BEFORE INSERT ON {target_table} "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    ledger._conn.commit()

    with pytest.raises(Exception, match="forward realization commit failed"):
        _commit(ledger)

    assert ledger.attested_realizations("a") == []
    assert store.periods("a") == []
    assert len(ledger.attested_pending("a")) == 1
    assert ledger._conn.execute("SELECT COUNT(*) FROM forward_local_events").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("table", "update_sql"),
    [
        ("forward_realizations", "UPDATE forward_realizations SET realized_return=9"),
        ("forward_local_events", "UPDATE forward_local_events SET payload_json='{}'"),
        ("paper_periods", "UPDATE paper_periods SET realized_return=9"),
    ],
)
def test_realization_event_and_period_tables_reject_update_and_delete(db, table, update_sql):
    _, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )
    _commit(ledger)

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger._conn.execute(update_sql)
    ledger._conn.rollback()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger._conn.execute(f"DELETE FROM {table}")
    ledger._conn.rollback()

    assert ledger.realization_chain_valid()


def test_realization_admission_and_input_scope_must_match_attested_decision(db):
    store, ledger = db
    ref = _ref(("BTC",))
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=ref)

    with pytest.raises(Exception, match="admission"):
        _commit(ledger, admission_ref=replace(ref, admission_id="different"))
    with pytest.raises(Exception, match="outside the admitted"):
        _commit(
            ledger,
            inputs={"ETH": {"weight": 1.0, "price_return": 0.01, "funding": 0.0}},
        )

    assert ledger.attested_realizations("a") == []
    assert store.periods("a") == []
    assert len(ledger.attested_pending("a")) == 1


def test_bogus_realization_row_invalidates_chain_and_attested_reads(db):
    _, ledger = db
    ledger._conn.execute(
        "INSERT INTO forward_realizations("
        "alpha_id,decided_ts,as_of_ts,decision_payload_sha256,realized_return,realized_ic,"
        "source,inputs_json,inputs_sha256,admission_id,admission_evidence_sha256,"
        "admission_decision_hash,committed_at_ms,prev_hash,row_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "bogus", _T0, _T0 + _WEEK, "c" * 64, 0.01, None,
            "FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING", '{}', "d" * 64,
            "bogus-admission", "a" * 64, "b" * 64, _T0 + _WEEK + 1,
            "0" * 64, "e" * 64,
        ),
    )
    ledger._conn.commit()

    assert ledger.realization_chain_valid() is False
    with pytest.raises(ForwardLedgerError, match="invalid forward realization chain"):
        ledger.attested_realizations()


def test_bogus_local_event_invalidates_realization_chain_and_attested_reads(db):
    _, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )
    _commit(ledger)
    previous = ledger._conn.execute(
        "SELECT row_hash FROM forward_local_events ORDER BY event_seq DESC LIMIT 1"
    ).fetchone()[0]
    ledger._conn.execute(
        "INSERT INTO forward_local_events"
        "(event_type,alpha_id,decided_ts,payload_json,prev_hash,row_hash) VALUES (?,?,?,?,?,?)",
        ("BOGUS", "a", _T0 + 1, '{}', previous, "f" * 64),
    )
    ledger._conn.commit()

    assert ledger.realization_chain_valid() is False
    with pytest.raises(ForwardLedgerError, match="invalid forward realization chain"):
        ledger.attested_realizations("a")


def test_standalone_forward_ledger_without_paper_periods_rejects_realization(tmp_path):
    path = str(tmp_path / "standalone-forward.db")
    ledger = ForwardLedger(path, create=True)
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )

    with pytest.raises(ForwardLedgerError, match="paper_periods"):
        _commit(ledger)

    assert ledger._conn.execute("SELECT COUNT(*) FROM forward_realizations").fetchone()[0] == 0
    with pytest.raises(ForwardLedgerError, match="invalid forward realization chain"):
        ledger.attested_realizations("a")
    assert len(ledger.attested_pending("a")) == 1


def test_unbound_v1_decision_is_legacy_and_never_attested_pending(db):
    _, ledger = db
    ledger._conn.execute(
        "INSERT INTO forward_decisions"
        "(alpha_id,decided_ts,recorded_at,weights_json,signals_json,source,realized) "
        "VALUES (?,?,?,?,?,?,0)",
        ("legacy", _T0, _T0, '{\"BTC\":1.0}', None, "FORWARD_LIVE"),
    )
    ledger._conn.commit()
    assert [row["alpha_id"] for row in ledger.legacy_decisions()] == ["legacy"]
    assert ledger.attested_pending("legacy") == []


def test_v1_migration_preserves_original_row_and_classifies_it_legacy(tmp_path):
    path = str(tmp_path / "v1-forward.db")
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE forward_decisions ("
        "alpha_id TEXT NOT NULL, decided_ts INTEGER NOT NULL, recorded_at INTEGER NOT NULL, "
        "weights_json TEXT NOT NULL, signals_json TEXT, source TEXT NOT NULL, "
        "realized INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (alpha_id, decided_ts))"
    )
    original = ("legacy", _T0, _T0 + 1, '{\"BTC\":1.0}', None, "FORWARD_LIVE", 0)
    connection.execute("INSERT INTO forward_decisions VALUES (?,?,?,?,?,?,?)", original)
    connection.commit()
    connection.close()

    ledger = ForwardLedger(path, create=True)

    row = ledger._conn.execute(
        "SELECT alpha_id,decided_ts,recorded_at,weights_json,signals_json,source,realized "
        "FROM forward_decisions"
    ).fetchone()
    assert tuple(row) == original
    assert [decision["alpha_id"] for decision in ledger.legacy_decisions()] == ["legacy"]


def test_binding_and_decision_identity_tables_reject_tampering(db):
    _, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ledger._conn.execute("DELETE FROM forward_decision_bindings")
    with pytest.raises(sqlite3.DatabaseError, match="identity is immutable"):
        ledger._conn.execute("UPDATE forward_decisions SET weights_json='{}'")


def test_orphan_binding_invalidates_chain_and_attested_reads(db):
    _, ledger = db
    ledger._conn.execute(
        "INSERT INTO forward_decision_bindings("
        "alpha_id,decided_ts,decision_payload_sha256,admission_id,"
        "admission_evidence_sha256,admission_decision_hash,admission_recorded_at_ms,"
        "price_basis,instrument_scope_json,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "orphan",
            _T0,
            "c" * 64,
            "admission-orphan",
            "a" * 64,
            "b" * 64,
            _T0 - 1,
            "PERP_MARK_PRICE",
            '["BTC"]',
            "0" * 64,
            "d" * 64,
        ),
    )
    ledger._conn.commit()

    assert ledger.decision_chain_valid() is False
    with pytest.raises(ForwardLedgerError, match="invalid forward decision binding chain"):
        ledger.attested_pending("orphan")


def test_bogus_matching_binding_is_not_an_attested_pending_decision(db):
    _, ledger = db
    ledger._conn.execute(
        "INSERT INTO forward_decisions"
        "(alpha_id,decided_ts,recorded_at,weights_json,signals_json,source,realized) "
        "VALUES (?,?,?,?,?,?,0)",
        ("bogus", _T0, _T0, '{\"BTC\":1.0}', None, "FORWARD_LIVE"),
    )
    ledger._conn.execute(
        "INSERT INTO forward_decision_bindings("
        "alpha_id,decided_ts,decision_payload_sha256,admission_id,"
        "admission_evidence_sha256,admission_decision_hash,admission_recorded_at_ms,"
        "price_basis,instrument_scope_json,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "bogus",
            _T0,
            "c" * 64,
            "admission-bogus",
            "a" * 64,
            "b" * 64,
            _T0 - 1,
            "PERP_MARK_PRICE",
            '["BTC"]',
            "0" * 64,
            "d" * 64,
        ),
    )
    ledger._conn.commit()

    assert ledger.decision_chain_valid() is False
    with pytest.raises(ForwardLedgerError, match="invalid forward decision binding chain"):
        ledger.attested_pending("bogus")


def _deny_commit(action, arg1, _arg2, _database, _source):
    if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def test_record_decision_rolls_back_its_transaction_when_commit_is_denied(db):
    _, ledger = db
    ledger._conn.set_authorizer(_deny_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            ledger.record_decision(
                "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
            )
    finally:
        ledger._conn.set_authorizer(None)

    assert ledger._conn.in_transaction is False


def test_attested_pending_rolls_back_its_transaction_when_commit_is_denied(db):
    _, ledger = db
    ledger._conn.set_authorizer(_deny_commit)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            ledger.attested_pending("a")
    finally:
        ledger._conn.set_authorizer(None)

    assert ledger._conn.in_transaction is False


def test_record_decision_rejects_weight_outside_admitted_scope(db):
    _, ledger = db

    with pytest.raises(Exception, match="outside the admitted"):
        ledger.record_decision(
            "a", _T0, recorded_at=_T0, weights={"ETH": 1.0}, admission_ref=_ref(("BTC",))
        )


def test_mark_realized_requires_one_pending_decision(db):
    _, ledger = db
    ledger.record_decision(
        "a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref(("BTC",))
    )

    ledger.mark_realized("a", _T0)

    with pytest.raises(Exception, match="pending forward decision not found"):
        ledger.mark_realized("a", _T0)
    with pytest.raises(Exception, match="pending forward decision not found"):
        ledger.mark_realized("missing", _T0)


def test_realize_math_long_short_plus_funding(db):
    store, ledger = db
    # L/S 各半:BTC +0.5(涨 10%),ETH -0.5(涨 4%) -> 价差 0.5*0.10 - 0.5*0.04 = +0.03
    w = {"BTC": 0.5, "ETH": -0.5}
    ledger.record_decision("crowd_contra", _T0, recorded_at=_T0, weights=w, admission_ref=_ref())
    t1 = _T0 + _WEEK
    prices = {_T0: {"BTC": 100.0, "ETH": 50.0}, t1: {"BTC": 110.0, "ETH": 52.0}}

    def funding_sum(name, t_from, t_to):
        assert (t_from, t_to) == (_T0, t1)
        return {"BTC": 0.002, "ETH": -0.001}[name]  # 周内日资金费求和

    out = _realize_with_evidence(
        ledger, store, "crowd_contra", timestamps=[_T0, t1], prices=prices, funding_sum=funding_sum
    )
    assert out["n_realized"] == 1
    ps = store.periods("crowd_contra")
    assert len(ps) == 1 and ps[0]["as_of_ts"] == t1
    # 资金费现金流:持多付正资金费、持空收正资金费 -> Σ(−w·f) = −0.5*0.002 + 0.5*(−0.001)*(−1)... 手算:
    # −(0.5)(0.002) − (−0.5)(−0.001) = −0.001 − 0.0005 = −0.0015;总 = 0.03 − 0.0015 = 0.0285
    assert ps[0]["realized_return"] == pytest.approx(0.0285, abs=1e-9)
    assert ps[0]["source"] == "FORWARD_LIVE_PERP_MARK_GROSS_PLUS_FUNDING"
    # 决策标记已兑现,不再 pending
    assert ledger.pending("crowd_contra") == []
    # summary 已重算
    assert store.track("crowd_contra")["n_periods"] == 1


def test_no_lookahead_within_min_gap(db):
    store, ledger = db
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref())
    t_early = _T0 + 2 * _DAY  # 仅 2 天后——低于 min_gap
    prices = {_T0: {"BTC": 100.0}, t_early: {"BTC": 130.0}}
    out = _realize_with_evidence(ledger, store, "a", timestamps=[_T0, t_early], prices=prices)
    assert out["n_realized"] == 0  # 绝不提早兑现
    assert len(ledger.pending("a")) == 1
    assert store.periods("a") == []


def test_coverage_fail_closed_leaves_pending_with_alert(db):
    store, ledger = db
    w = {"BTC": 0.4, "ETH": -0.4, "SOL": 0.2}
    ledger.record_decision("a", _T0, recorded_at=_T0, weights=w, admission_ref=_ref(tuple(w)))
    t1 = _T0 + _WEEK
    prices = {_T0: {"BTC": 100.0, "ETH": 50.0, "SOL": 20.0}, t1: {"BTC": 105.0}}  # 覆盖率 1/3 < 0.8
    out = _realize_with_evidence(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    assert out["n_realized"] == 0
    assert len(ledger.pending("a")) == 1  # 留 pending,等数据补齐
    assert any("coverage" in a for a in out["alerts"])
    assert store.periods("a") == []


def test_quality_failed_weighted_ticker_leaves_due_decision_pending(db):
    store, ledger = db
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 0.5, "ETH": -0.5}, admission_ref=_ref())
    t1 = _T0 + _WEEK
    prices = {
        _T0: {"BTC": 100.0, "ETH": 50.0},
        t1: {"BTC": 101.0, "ETH": 49.0},
    }

    out = _realize_with_evidence(
        ledger,
        store,
        "a",
        timestamps=[_T0, t1],
        prices=prices,
        blocked_instruments={"ETH"},
    )

    assert out["n_realized"] == 0
    assert out["n_quality_blocked"] == 1
    assert len(ledger.pending("a")) == 1
    assert store.periods("a") == []
    assert any("ETH" in alert and "quality" in alert for alert in out["alerts"])


def test_unrelated_quality_failure_does_not_block_realization(db):
    store, ledger = db
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref())
    t1 = _T0 + _WEEK

    out = _realize_with_evidence(
        ledger,
        store,
        "a",
        timestamps=[_T0, t1],
        prices={_T0: {"BTC": 100.0}, t1: {"BTC": 101.0}},
        blocked_instruments={"ETH"},
    )

    assert out["n_realized"] == 1
    assert out["n_quality_blocked"] == 0


def test_missing_current_quality_evidence_blocks_all_realization_by_default(db):
    store, ledger = db
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref())
    t1 = _T0 + _WEEK

    out = realize_due(
        ledger,
        store,
        "a",
        timestamps=[_T0, t1],
        prices={_T0: {"BTC": 100.0}, t1: {"BTC": 101.0}},
    )

    assert out["n_realized"] == 0
    assert out["n_quality_blocked"] == 1
    assert len(ledger.pending("a")) == 1
    assert store.periods("a") == []
    assert any("quality evidence unavailable" in alert for alert in out["alerts"])


def test_realized_ic_spearman_against_hand_computation(db):
    store, ledger = db
    # 4 个名字(IC 的最小截面),信号与实现收益完全同序 -> Spearman = +1
    w = {"A": 0.5, "B": 0.25, "C": -0.25, "D": -0.5}
    sig = {"A": 2.0, "B": 1.0, "C": -1.0, "D": -2.0}
    ledger.record_decision("a", _T0, recorded_at=_T0, weights=w, signals=sig, admission_ref=_ref(tuple(w)))
    t1 = _T0 + _WEEK
    prices = {_T0: {n: 100.0 for n in w}, t1: {"A": 108.0, "B": 104.0, "C": 98.0, "D": 96.0}}  # 收益同信号排序
    _realize_with_evidence(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    ps = store.periods("a")
    assert ps[0]["realized_ic"] == pytest.approx(1.0, abs=1e-9)


def test_ic_none_when_cross_section_too_thin(db):
    store, ledger = db
    w = {"A": 0.5, "B": -0.5}  # 2 名 < 最小截面 4
    ledger.record_decision("a", _T0, recorded_at=_T0, weights=w, signals={"A": 1.0, "B": -1.0}, admission_ref=_ref(tuple(w)))
    t1 = _T0 + _WEEK
    prices = {_T0: {"A": 100.0, "B": 100.0}, t1: {"A": 101.0, "B": 99.0}}
    _realize_with_evidence(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    assert store.periods("a")[0]["realized_ic"] is None  # 诚实 None,不硬凑


def test_multi_decision_catchup_realizes_in_order(db):
    store, ledger = db
    # 错过两周后一次 catch-up:两条 pending 决策都应按各自的下一周截面兑现
    for k in range(2):
        ledger.record_decision("a", _T0 + k * _WEEK, recorded_at=_T0 + k * _WEEK, weights={"BTC": 1.0}, admission_ref=_ref())
    ts = [_T0, _T0 + _WEEK, _T0 + 2 * _WEEK]
    prices = {ts[0]: {"BTC": 100.0}, ts[1]: {"BTC": 110.0}, ts[2]: {"BTC": 99.0}}
    out = _realize_with_evidence(ledger, store, "a", timestamps=ts, prices=prices)
    assert out["n_realized"] == 2
    ps = store.periods("a")
    assert [p["as_of_ts"] for p in ps] == [ts[1], ts[2]]
    assert ps[0]["realized_return"] == pytest.approx(0.10, abs=1e-9)
    assert ps[1]["realized_return"] == pytest.approx(99.0 / 110.0 - 1.0, abs=1e-9)


def test_extreme_return_realizes_but_alerts(db):
    store, ledger = db
    ledger.record_decision("a", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref())
    t1 = _T0 + _WEEK
    prices = {_T0: {"BTC": 100.0}, t1: {"BTC": 130.0}}  # +30% 周 —— 异常但真实
    out = _realize_with_evidence(ledger, store, "a", timestamps=[_T0, t1], prices=prices)
    assert out["n_realized"] == 1  # 照常记录(不是拒绝)
    assert any("extreme" in a for a in out["alerts"])  # 但要报告


def test_coexists_with_legacy_paper_track_tables(db):
    store, ledger = db
    # 老式历史切分 track 先写入
    store.append_periods("legacy_alpha", [(1000, 0.01, None, "OOS_TAIL")])
    store.recompute_summary("legacy_alpha")
    # 前向流写入后,老 track 原样,新 track 用真实时间戳并列
    ledger.record_decision("crowd_contra", _T0, recorded_at=_T0, weights={"BTC": 1.0}, admission_ref=_ref())
    t1 = _T0 + _WEEK
    _realize_with_evidence(
        ledger, store, "crowd_contra", timestamps=[_T0, t1], prices={_T0: {"BTC": 100.0}, t1: {"BTC": 101.0}}
    )
    assert store.track("legacy_alpha")["n_periods"] == 1
    assert store.track("crowd_contra")["last_ts"] == t1
    ids = {t["alpha_id"] for t in store.all_tracks()}
    assert ids == {"legacy_alpha", "crowd_contra"}


def test_ledger_fails_closed_without_schema(tmp_path):
    import sqlite3

    path = str(tmp_path / "not_a_ledger.db")
    sqlite3.connect(path).execute("CREATE TABLE unrelated(x)")  # 存在但无 ledger 表
    with pytest.raises(Exception):
        ForwardLedger(path, create=False)
