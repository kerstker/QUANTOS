"""本地仓位账本(Stage 1)。设计:docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.5;
strategy_id 分账维度:docs/engineering/specs/2026-07-06-strategy-position-isolation-design.md。

append-only SQLite；同一 client_order_id/fill_id + 同一不可变 payload 的重放幂等 no-op，
同 ID 异 payload 抛 `LiveLedgerConflictError`，不再由 `INSERT OR IGNORE` 静默吞碰撞。
订单/成交记录一旦落库即不可篡改。fill 自带 side(不依赖 join 回订单表),
一笔成交本身就是自足的账本条目。

**strategy_id(2026-07-06 战线③):只是本地账本内部的分账维度。** venue/保证金/风控/对账/紧急
减仓全部维持账户级——交易所根本不知道策略,任何 "per-strategy vs venue" 对账都是拿本地分解自己
对自己、伪造独立性(见 reconciliation.py)。结构性不变量:账户级 `all_positions()` = 全部 fills
的和,策略级是按 fill.strategy_id 的分区,分区求和恒等于全集(测试钉死,数学上不可能漂移)。

- 写入 API 的 `strategy_id` 是**必填关键字**(不给默认值)——强制显式从驱动器一路传到底,杜绝
  新写入静默落到 'crowd_contra'。新库 schema 刻意 NOT NULL 无默认:绕过本 API 的裸 INSERT 在
  新库上直接报错,而不是静默错标(2026-07-06 评审"小"项)。
- 惰性迁移(`_migrate_strategy_id`)只对**迁移前的存量行**留下列默认值 'crowd_contra'(SQLite
  ALTER ADD COLUMN 的 NOT NULL 语法要求;该默认值永久留在被迁移库的 schema 里,无法无损移除——
  无害,因为 Python 写入 API 全部显式带 strategy_id,SQL 默认值只对迁移前的存量行生效)。
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from quantos.contracts.trading import OrderSide

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_orders (
  client_order_id TEXT PRIMARY KEY,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  submitted_at    INTEGER NOT NULL,
  venue_order_id  TEXT,
  status          TEXT NOT NULL DEFAULT 'NEW',
  strategy_id     TEXT NOT NULL,
  order_type      TEXT,
  limit_price     REAL
);
CREATE TABLE IF NOT EXISTS live_fills (
  fill_id         TEXT PRIMARY KEY,
  client_order_id TEXT NOT NULL,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  price           REAL NOT NULL,
  filled_at       INTEGER NOT NULL,
  strategy_id     TEXT NOT NULL
);
"""
# TCA 表(2026-07-05)单独建:老账本(建库时还没有它)必须能平滑迁移——`record_tca` 惰性建表、
# `tca_records` 缺表返回空,核心 schema 校验(_TABLES)不包含它,避免老库一打开就被拒。
_TCA_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_tca (
  fill_id          TEXT PRIMARY KEY,
  client_order_id  TEXT NOT NULL,
  instrument_id    TEXT NOT NULL,
  side             TEXT NOT NULL,
  decision_price   REAL NOT NULL,
  fill_price       REAL NOT NULL,
  quantity         REAL NOT NULL,
  slippage_bps     REAL,
  recorded_at      INTEGER NOT NULL,
  strategy_id      TEXT NOT NULL
);
"""
_TABLES = ("live_orders", "live_fills")
# 2026-07-06 之前所有存量真实成交都属 crowd_contra——这是**只读核查过的历史事实**(设计文档 §7:
# 三个真实账本逐行枚举;32 位 hex 决策订单前缀经 decision_prefix("crowd_contra", decided_ts, inst)
# 逐一复算命中;quantos-smoke/recon-smoke/resize-comp-* 手工补记行也都是 crowd_contra book 的
# 运维产物,按用户裁定同样保持该回填值),不是猜测。
_BACKFILL_STRATEGY_ID = "crowd_contra"
_STRATEGY_TABLES = ("live_orders", "live_fills", "live_tca")
# order_type/limit_price(2026-07-06 战线②):**双轨**——_SCHEMA(上面)让新建账本/preview
# 一次性草稿账本(create=True)直接带列;`_migrate_order_type_columns` 给存量真实测试网账本
# 惰性 ALTER。两列刻意**不设 NOT NULL/DEFAULT**(与 strategy_id 的强制回填不同):存量行读回
# NULL 是诚实的("当时只有 MARKET,这个字段当时根本不存在"),不是拿 'MARKET' 去猜一个从未
# 记录过的历史事实;新写入永远由 Python API 显式传值(默认 order_type="MARKET"),不依赖
# SQL 端默认值。只作用于 live_orders(live_fills/live_tca 不需要下单方式这个维度)。
_ORDER_TYPE_TABLE = "live_orders"


class LiveLedgerError(RuntimeError):
    pass


class LiveLedgerConflictError(LiveLedgerError):
    """An idempotency key was reused for a different immutable ledger payload."""


class LiveLedger:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_TCA_SCHEMA)
            self._conn.commit()
        self._require_schema()
        # 迁移必须在无 create 路径也跑:ops API / gonogo_report 用 LiveLedger(path) 打开
        self._migrate_strategy_id()
        self._migrate_order_type_columns()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise LiveLedgerError(f"live ledger unreadable: {exc}") from exc
        missing = [t for t in _TABLES if t not in {r["name"] for r in rows}]
        if missing:
            raise LiveLedgerError(f"live ledger missing tables: {missing}")

    def _add_strategy_column_if_missing(self, table: str) -> bool:
        """ALTER ADD COLUMN,duplicate column 按"已被并发迁移完成"吞掉(2026-07-06 评审 #3:
        驱动器/哨兵/ops API/gonogo/备份是多个独立进程,PRAGMA→ALTER 两步非原子,两进程同时
        打开老库都看到列缺失、都 ALTER——后到者绝不能因此把构造函数炸掉)。其它
        OperationalError(真实损坏)照常暴露。返回是否真的加了列。"""
        try:
            self._conn.execute(
                f"ALTER TABLE {table} ADD COLUMN strategy_id TEXT NOT NULL "
                f"DEFAULT '{_BACKFILL_STRATEGY_ID}'")
            return True
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
            return False

    def _migrate_strategy_id(self) -> None:
        """惰性迁移(镜像 live_tca 的平滑迁移范式,每次打开都跑,幂等):老库缺 strategy_id 列
        -> ALTER TABLE ADD COLUMN 并以 'crowd_contra' 为默认值回填存量行。**存量库是真实测试网
        交易数据,SQLite 的 ADD COLUMN 不重写任何存量行、不碰任何已有值**(老行读取时返回列
        默认值)——数据零丢失零改动,这正是回填语义。live_tca 缺表时跳过(record_tca 惰性建表
        时自带新列)。"""
        changed = False
        for table in _STRATEGY_TABLES:
            exists = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if exists is None:
                continue                    # live_tca 老库可能没有,惰性建表路径自带新列
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if "strategy_id" in cols:
                continue
            changed = self._add_strategy_column_if_missing(table) or changed
        if changed:
            self._conn.commit()

    def _add_column_if_missing(self, table: str, column: str, sql_type: str) -> bool:
        """通用 nullable ADD COLUMN(无 NOT NULL/DEFAULT)——duplicate column 按"已被并发
        迁移完成"吞掉(镜像 `_add_strategy_column_if_missing` 的并发防御,理由同前:多个独立
        进程可能同时看到列缺失、同时 ALTER)。其它 OperationalError(真实损坏)照常暴露。"""
        try:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            return True
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
            return False

    def _add_order_type_columns_if_missing(self) -> bool:
        """order_type/limit_price 的惰性 ALTER 那一半(存量真实测试网账本;_SCHEMA 已经让
        新库/preview 草稿账本直接带列)。两列均 nullable、无 DEFAULT——老行读回 NULL 是诚实
        的(见模块常量注释),不是回填一个从未记录过的历史事实。"""
        changed = self._add_column_if_missing(_ORDER_TYPE_TABLE, "order_type", "TEXT")
        changed = self._add_column_if_missing(_ORDER_TYPE_TABLE, "limit_price", "REAL") or changed
        return changed

    def _migrate_order_type_columns(self) -> None:
        exists = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_ORDER_TYPE_TABLE,)).fetchone()
        if exists is None:
            return
        cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({_ORDER_TYPE_TABLE})")}
        if {"order_type", "limit_price"} <= cols:
            return
        if self._add_order_type_columns_if_missing():
            self._conn.commit()

    def record_submission(self, client_order_id: str, instrument_id: str, side: OrderSide,
                          quantity: float, *, strategy_id: str, submitted_at: int,
                          venue_order_id: str = "", status: str = "NEW",
                          order_type: str = "MARKET", limit_price: Optional[float] = None) -> None:
        immutable = (
            instrument_id, side.value, float(quantity), strategy_id, order_type, limit_price,
        )
        try:
            self._conn.execute(
                "INSERT INTO live_orders"
                "(client_order_id, instrument_id, side, quantity, submitted_at, venue_order_id,"
                " status, strategy_id, order_type, limit_price) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (client_order_id, instrument_id, side.value, float(quantity), int(submitted_at),
                 venue_order_id, status, strategy_id, order_type, limit_price))
        except sqlite3.IntegrityError:
            self._conn.rollback()
            existing = self._conn.execute(
                "SELECT instrument_id, side, quantity, strategy_id, order_type, limit_price"
                " FROM live_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if existing is None:
                raise
            if tuple(existing) != immutable:
                raise LiveLedgerConflictError(
                    f"client_order_id collision with different payload: {client_order_id}"
                )
            return
        self._conn.commit()

    def update_status(self, client_order_id: str, status: str, *,
                      venue_order_id: Optional[str] = None) -> None:
        if venue_order_id is not None:
            self._conn.execute(
                "UPDATE live_orders SET status=?, venue_order_id=? WHERE client_order_id=?",
                (status, venue_order_id, client_order_id))
        else:
            self._conn.execute("UPDATE live_orders SET status=? WHERE client_order_id=?",
                               (status, client_order_id))
        self._conn.commit()

    def record_fill(self, fill_id: str, client_order_id: str, instrument_id: str,
                    side: OrderSide, quantity: float, price: float, *, strategy_id: str,
                    filled_at: int) -> None:
        immutable = (
            client_order_id, instrument_id, side.value, float(quantity), float(price), strategy_id,
        )
        try:
            self._conn.execute(
                "INSERT INTO live_fills"
                "(fill_id, client_order_id, instrument_id, side, quantity, price, filled_at,"
                " strategy_id) VALUES (?,?,?,?,?,?,?,?)",
                (fill_id, client_order_id, instrument_id, side.value, float(quantity), float(price),
                 int(filled_at), strategy_id))
        except sqlite3.IntegrityError:
            self._conn.rollback()
            existing = self._conn.execute(
                "SELECT client_order_id, instrument_id, side, quantity, price, strategy_id"
                " FROM live_fills WHERE fill_id=?",
                (fill_id,),
            ).fetchone()
            if existing is None:
                raise
            if tuple(existing) != immutable:
                raise LiveLedgerConflictError(
                    f"fill_id collision with different payload: {fill_id}"
                )
            return
        self._conn.commit()

    def record_tca(self, fill_id: str, *, client_order_id: str, instrument_id: str,
                   side: OrderSide, decision_price: float, fill_price: float, quantity: float,
                   strategy_id: str, recorded_at: int) -> None:
        """成交质量记录(2026-07-05 TCA)。惰性建表；同 ID 同 payload 幂等、异 payload 拒绝。
        slippage_bps 在写入时算好落库(带方向:买贵/卖贱为正=成本),读取端零重算。"""
        from live.execution.tca import slippage_bps
        self._conn.executescript(_TCA_SCHEMA)
        self._migrate_strategy_id()          # 老库 live_tca 可能建于加列之前(防御,幂等)
        slip = slippage_bps(side, decision_price=decision_price, fill_price=fill_price)
        immutable = (
            client_order_id, instrument_id, side.value, float(decision_price),
            float(fill_price), float(quantity), slip, strategy_id,
        )
        try:
            self._conn.execute(
                "INSERT INTO live_tca"
                "(fill_id, client_order_id, instrument_id, side, decision_price, fill_price,"
                " quantity, slippage_bps, recorded_at, strategy_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fill_id, client_order_id, instrument_id, side.value, float(decision_price),
                 float(fill_price), float(quantity), slip, int(recorded_at), strategy_id))
        except sqlite3.IntegrityError:
            self._conn.rollback()
            existing = self._conn.execute(
                "SELECT client_order_id, instrument_id, side, decision_price, fill_price,"
                " quantity, slippage_bps, strategy_id FROM live_tca WHERE fill_id=?",
                (fill_id,),
            ).fetchone()
            if existing is None:
                raise
            if tuple(existing) != immutable:
                raise LiveLedgerConflictError(
                    f"TCA fill_id collision with different payload: {fill_id}"
                )
            return
        self._conn.commit()

    def tca_records(self) -> List[dict]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='live_tca'").fetchall()
        if not rows:
            return []                                          # 老账本无 TCA 表 -> 诚实空,不炸
        return [dict(r) for r in
                self._conn.execute("SELECT * FROM live_tca ORDER BY recorded_at")]

    def close(self) -> None:
        self._conn.close()

    def orders(self, strategy_id: Optional[str] = None) -> List[dict]:
        if strategy_id is None:
            return [dict(r) for r in
                    self._conn.execute("SELECT * FROM live_orders ORDER BY submitted_at")]
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM live_orders WHERE strategy_id=? ORDER BY submitted_at", (strategy_id,))]

    def fills(self, instrument_id: Optional[str] = None, *,
              strategy_id: Optional[str] = None) -> List[dict]:
        conds, params = [], []
        if instrument_id is not None:
            conds.append("instrument_id=?")
            params.append(instrument_id)
        if strategy_id is not None:
            conds.append("strategy_id=?")
            params.append(strategy_id)
        q = "SELECT * FROM live_fills"
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return [dict(r) for r in self._conn.execute(q + " ORDER BY filled_at", params)]

    def strategy_ids(self) -> List[str]:
        """账本里出现过的全部策略(orders ∪ fills——2026-07-06 评审 #5:只提交过订单还没有
        成交的策略也必须可见,不能等第一笔成交才被发现)。console/审计/多策略未签 mandate
        告警用。"""
        rows = self._conn.execute(
            "SELECT DISTINCT strategy_id FROM live_orders "
            "UNION SELECT DISTINCT strategy_id FROM live_fills")
        return sorted(r["strategy_id"] for r in rows)

    def position(self, instrument_id: str, strategy_id: Optional[str] = None) -> float:
        return self.all_positions(strategy_id=strategy_id).get(instrument_id, 0.0)

    def all_positions(self, strategy_id: Optional[str] = None) -> Dict[str, float]:
        """strategy_id=None -> 账户级聚合(现有语义一字节不变:全部 fills 的和,风控/对账用);
        带参 -> 该策略的分账仓位(目标增量计算/parity 用)。分区求和恒等于账户级(结构性
        不变量,tests/live/test_position_ledger_strategy.py 钉死)。"""
        out: Dict[str, float] = {}
        for f in self.fills(strategy_id=strategy_id):
            sign = 1.0 if f["side"] == OrderSide.BUY.value else -1.0
            out[f["instrument_id"]] = out.get(f["instrument_id"], 0.0) + sign * f["quantity"]
        return out
