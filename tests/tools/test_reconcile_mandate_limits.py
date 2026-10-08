"""tools/reconcile_mandate_limits.py:gate 侧 aum 乘数 vs mandate 侧人工签署限额——只读对账
诊断工具(审计 docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md §4.4 [P1-R1])。

背景:`tools/live_track_advance.py` 把 `PreTradeRiskGate` 的实际生效上限算成 aum 的乘数
(gross=aum*2 / net=aum*0.3 / single_order=aum*0.5 / daily_loss=aum*0.1,见其 `gate_limits()`
调用点 + `evaluate_risk_state`/`flatten_target_for_breach` 调用点),`key/live_mandate*.json`
是真正逐字段人工签署的独立限额体系(max_gross_notional 等)。两套体系并行生效于同一条下单
路径,但代码里从未有一处把两组数字并排列出来告诉人"给定当前 aum,哪一层真正收口"。

本文件覆盖:
1. gate 侧 aum 乘数计算(compute_gate_limits)本身正确;
2. mandate 侧签署文件加载(load_mandate_limits)——正常/缺失/损坏三种情况;
3. compare_limits() 在不同 aum 下"谁更紧"的判断随 aum 正确翻转(低 aum → gate 更紧,
   高 aum → mandate 更紧,相等 → tie);
4. mandate 未签署时诚实标注"未签署"、不崩溃、不拿 None 冒充"无限额";
5. format_report()/CLI 端到端不崩溃、覆盖到全部维度;
6. 一个"源码字符串哨兵"测试——防止本文件复制的 aum 乘数常量与
   `tools/live_track_advance.py` 里真实的调用点字面量悄悄漂移。
"""
from __future__ import annotations

import json
import os
import re

import pytest

import tools.reconcile_mandate_limits as rml
from tools.reconcile_mandate_limits import (
    compare_limits,
    compute_gate_limits,
    format_report,
    load_mandate_limits,
    main,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_mandate(tmp_path, **overrides) -> str:
    data = {
        "mandate_id": "test-mandate-2026-07-09",
        "venue": "testnet",
        "exchange": "binance",
        "allowed_instruments": ["BTC", "ETH"],
        "max_single_order_notional": 2500.0,
        "max_gross_notional": 10000.0,
        "max_net_notional": 3000.0,
    }
    data.update(overrides)
    path = tmp_path / "mandate.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 1) gate 侧 aum 乘数
# ---------------------------------------------------------------------------

def test_compute_gate_limits_applies_aum_multipliers():
    g = compute_gate_limits(10_000.0)
    assert g.aum == 10_000.0
    assert g.gross_notional == 20_000.0
    assert g.net_notional == 3_000.0
    assert g.single_order_notional == 5_000.0
    assert g.daily_loss_notional == 1_000.0
    assert g.max_leverage == 3.0


def test_compute_gate_limits_scales_linearly_with_aum():
    g = compute_gate_limits(1.0)
    assert g.gross_notional == pytest.approx(2.0)
    assert g.net_notional == pytest.approx(0.3)
    assert g.single_order_notional == pytest.approx(0.5)
    assert g.daily_loss_notional == pytest.approx(0.1)


def test_compute_gate_limits_zero_aum_is_zero_not_crash():
    g = compute_gate_limits(0.0)
    assert g.gross_notional == 0.0
    assert g.net_notional == 0.0
    assert g.single_order_notional == 0.0
    assert g.daily_loss_notional == 0.0


# ---------------------------------------------------------------------------
# 2) mandate 侧文件加载
# ---------------------------------------------------------------------------

def test_load_mandate_limits_reads_signed_file(tmp_path):
    path = _write_mandate(tmp_path)
    m = load_mandate_limits(path)
    assert m.signed is True
    assert m.gross_notional == 10000.0
    assert m.net_notional == 3000.0
    assert m.single_order_notional == 2500.0
    assert m.mandate_id == "test-mandate-2026-07-09"
    assert m.venue == "testnet"
    assert m.exchange == "binance"
    assert m.unsigned_reason is None


def test_load_mandate_limits_missing_file_is_unsigned_not_crash(tmp_path):
    missing = str(tmp_path / "does_not_exist.json")
    m = load_mandate_limits(missing)              # 不应抛异常
    assert m.signed is False
    assert m.gross_notional is None
    assert m.net_notional is None
    assert m.single_order_notional is None
    assert m.unsigned_reason is not None
    assert "not found" in m.unsigned_reason


def test_load_mandate_limits_malformed_json_is_unsigned_not_crash(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    m = load_mandate_limits(str(path))             # 不应抛异常
    assert m.signed is False
    assert m.unsigned_reason is not None


def test_load_mandate_limits_missing_required_field_is_unsigned(tmp_path):
    path = tmp_path / "mandate.json"
    data = {"mandate_id": "x", "venue": "testnet", "exchange": "binance",
           "allowed_instruments": []}       # 缺 max_gross_notional 等必填字段
    path.write_text(json.dumps(data), encoding="utf-8")
    m = load_mandate_limits(str(path))
    assert m.signed is False
    assert m.unsigned_reason is not None


# ---------------------------------------------------------------------------
# 3) compare_limits():"谁更紧"随 aum 正确翻转
# ---------------------------------------------------------------------------

def test_compare_limits_gate_tighter_when_aum_small(tmp_path):
    # mandate: gross 10000 / net 3000 / single 2500
    mandate = load_mandate_limits(_write_mandate(tmp_path))
    gate = compute_gate_limits(2_000.0)     # gross=4000 / net=600 / single=1000 — 全部 < mandate
    rows = {c.dimension: c for c in compare_limits(gate, mandate)}
    assert rows["gross_notional"].tighter == "gate"
    assert rows["net_notional"].tighter == "gate"
    assert rows["single_order_notional"].tighter == "gate"


def test_compare_limits_mandate_tighter_when_aum_large(tmp_path):
    mandate = load_mandate_limits(_write_mandate(tmp_path))
    gate = compute_gate_limits(50_000.0)    # gross=100000 / net=15000 / single=25000 — 全部 > mandate
    rows = {c.dimension: c for c in compare_limits(gate, mandate)}
    assert rows["gross_notional"].tighter == "mandate"
    assert rows["net_notional"].tighter == "mandate"
    assert rows["single_order_notional"].tighter == "mandate"


def test_compare_limits_tie_when_numerically_equal(tmp_path):
    # net_cap = aum*0.3 = 3000 <=> aum = 10000,恰好等于 mandate 的 max_net_notional
    mandate = load_mandate_limits(_write_mandate(tmp_path))
    gate = compute_gate_limits(10_000.0)
    rows = {c.dimension: c for c in compare_limits(gate, mandate)}
    assert rows["net_notional"].tighter == "tie"


def test_compare_limits_unsigned_mandate_marks_gate_only(tmp_path):
    mandate = load_mandate_limits(str(tmp_path / "nope.json"))
    gate = compute_gate_limits(10_000.0)
    rows = {c.dimension: c for c in compare_limits(gate, mandate)}
    assert rows["gross_notional"].tighter == "mandate_unsigned(gate_only)"
    assert rows["net_notional"].tighter == "mandate_unsigned(gate_only)"
    assert rows["single_order_notional"].tighter == "mandate_unsigned(gate_only)"


def test_compare_limits_daily_loss_and_leverage_are_gate_only_regardless_of_signing(tmp_path):
    # mandate schema (live/governance/mandate.py::Mandate) 本身没有 daily_loss/leverage 字段——
    # 这两行永远是 "mandate_has_no_field",与是否已签署无关(区别于 gross/net/single 的
    # "mandate_unsigned(gate_only)")。
    signed = load_mandate_limits(_write_mandate(tmp_path))
    unsigned = load_mandate_limits(str(tmp_path / "nope.json"))
    gate = compute_gate_limits(10_000.0)
    for mandate in (signed, unsigned):
        rows = {c.dimension: c for c in compare_limits(gate, mandate)}
        assert rows["daily_loss_notional"].tighter == "mandate_has_no_field"
        assert rows["daily_loss_notional"].mandate_value is None
        assert rows["max_leverage"].tighter == "mandate_has_no_field"
        assert rows["max_leverage"].mandate_value is None


# ---------------------------------------------------------------------------
# 4) format_report() / CLI
# ---------------------------------------------------------------------------

def test_format_report_contains_all_dimensions_and_key_numbers(tmp_path):
    mandate = load_mandate_limits(_write_mandate(tmp_path))
    gate = compute_gate_limits(10_000.0)
    report = format_report(gate, mandate, compare_limits(gate, mandate))
    for dim in ("gross_notional", "net_notional", "single_order_notional",
               "daily_loss_notional", "max_leverage"):
        assert dim in report
    assert "20,000" in report          # gate gross = aum*2 = 20000
    assert "10,000" in report          # mandate gross = 10000


def test_format_report_unsigned_mandate_says_unsigned_not_crash(tmp_path):
    mandate = load_mandate_limits(str(tmp_path / "none.json"))
    gate = compute_gate_limits(5_000.0)
    report = format_report(gate, mandate, compare_limits(gate, mandate))
    assert "未签署" in report


def test_main_requires_aum_argument():
    with pytest.raises(SystemExit):
        main([])


def test_main_rejects_unknown_venue():
    with pytest.raises(SystemExit):
        main(["--aum", "10000", "--venue", "coinbase"])


def test_main_prints_report_end_to_end_with_explicit_mandate_file(tmp_path, capsys):
    path = _write_mandate(tmp_path)
    exit_code = main(["--aum", "50000", "--mandate-file", path])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "gross_notional" in out
    assert "mandate" in out.lower()


def test_main_uses_venue_mandate_file_mapping_by_default(tmp_path, monkeypatch, capsys):
    path = _write_mandate(tmp_path)
    monkeypatch.setitem(rml.VENUE_MANDATE_FILES, "binance", path)
    exit_code = main(["--aum", "10000", "--venue", "binance"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert path in out


def test_main_never_mutates_mandate_file_on_disk(tmp_path):
    # 只读诊断的字面证明:跑完整个 CLI 之后,mandate 文件字节不变。
    path = _write_mandate(tmp_path)
    before = open(path, "rb").read()
    main(["--aum", "10000", "--mandate-file", path])
    after = open(path, "rb").read()
    assert before == after


# ---------------------------------------------------------------------------
# 5) 源码字符串哨兵:防止本文件的 aum 乘数常量与 live_track_advance.py 的真实调用点漂移
# ---------------------------------------------------------------------------

def _count_boundary_literal(literal: str, src: str) -> int:
    """统计 `literal` 在 `src` 里作为"完整数字字面量"出现的次数——用负向先行断言
    `(?![0-9.])` 排除"旧字面量只是新字面量的数字前缀"这种假阴性匹配。纯子串包含(`literal in
    src`)分辨不出这两者:例如把 `net_cap=aum * 0.3` 改成 `net_cap=aum * 0.35`(风控倍数最常见
    的调整方式)之后,旧字面量原样是新字面量的前缀,`in` 判断仍为 True、哨兵不会报红。这里用
    正则确保字面量后面紧跟的不是另一个数字/小数点字符,才算真正命中。"""
    pattern = re.escape(literal) + r"(?![0-9.])"
    return len(re.findall(pattern, src))


def test_source_sentinel_gate_aum_multipliers_match_live_track_advance():
    """漂移哨兵。`tools/live_track_advance.py` 把这组 aum 乘数写成调用处的字面量(未导出成命名
    常量,见其模块 docstring "gate 限额 vs mandate 限额,两者不同层")——本工具的硬约束是绝不
    修改那个文件,所以只能在这里复制一份常量。这个测试确保复制的这份不会与原文件悄悄漂移:原
    文件这几处字面量一旦被改而这份拷贝没跟着改,这里先报红,而不是本工具悄悄给出过时的对照表。

    覆盖 `tools/reconcile_mandate_limits.py` 模块 docstring 承诺的全部三处调用点(而不只是
    `gate_limits()` 一处):
    1. `gate_limits()`(期货 venue 的 else 分支)—— gross_cap/net_cap/single_order_cap/
       daily_loss_cap 四个字面量各出现一次,`max_leverage=3.0` 在函数签名默认值里出现一次;
    2. `evaluate_risk_state()` 与 3. `flatten_target_for_breach()`—— 2026-07 MEXC 对接后,
       两处熔断调用点改从统一的 `risk_gross_cap`/`risk_net_cap` 变量取值(现货 venue 用
       1×equity 口径,单名满仓 long 是合法目标),期货口径在 else 分支**单点赋值**
       `risk_gross_cap, risk_net_cap = aum * 2, aum * 0.3`——钉住这个单点不漂移(恰好 1 次),
       且两个调用点都确实经变量透传(各恰好 2 次)。任一处独立漂移都会让计数偏离,这里先报红。
    """
    src = open(os.path.join(_ROOT, "tools", "live_track_advance.py"), encoding="utf-8").read()

    for literal in ("gross_cap=aum * 2", "net_cap=aum * 0.3",
                   "single_order_cap=aum * 0.5", "daily_loss_cap=aum * 0.1",
                   "max_leverage=3.0"):
        count = _count_boundary_literal(literal, src)
        assert count >= 1, (
            f"live_track_advance.py no longer contains {literal!r} as a whole numeric literal — "
            f"tools/reconcile_mandate_limits.py's GATE_*_AUM_MULT constants have drifted "
            f"from the real gate_limits() call site and must be updated to match")

    # 期货熔断口径的单点赋值(见 docstring):与 GATE_GROSS/NET_AUM_MULT 同步的唯一地方
    count = _count_boundary_literal("risk_gross_cap, risk_net_cap = aum * 2, aum * 0.3", src)
    assert count == 1, (
        f"live_track_advance.py expected exactly 1 single-point assignment of the futures "
        f"breach caps ('risk_gross_cap, risk_net_cap = aum * 2, aum * 0.3'), found {count} — "
        f"the futures multiplier has drifted from tools/reconcile_mandate_limits.py's "
        f"GATE_GROSS_AUM_MULT/GATE_NET_AUM_MULT constants; update both to match")
    for literal in ("max_gross_exposure=risk_gross_cap", "max_net_exposure=risk_net_cap"):
        count = _count_boundary_literal(literal, src)
        assert count == 2, (
            f"live_track_advance.py expected exactly 2 pass-through occurrences of {literal!r} "
            f"(evaluate_risk_state() call site + flatten_target_for_breach() call site), "
            f"found {count} — one breach call site stopped using the shared cap variables")
