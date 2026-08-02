"""Binance USDT-M 期货 mainnet 驱动器接线(2026-07 Binance mainnet 接线,镜像
tests/live/test_btc_spot_driver.py 的 `_VENUES["mexc"]` 注册形状钉法):
`_VENUES["binance_mainnet"]` 注册形状、凭据加载(env/key 文件两路径)、mainnet 构造闸门、
mandate 模板加载语义、`mainnet_key_safety_skip_reason` 交易所家族归一化。
main() 是网络胶水不可离线跑(仓库惯例),这里测可测纯件 + 源码钉子(「可测桥 + 源码钉子」
惯例,与 mainnet_key_safety_skip_reason 同法)。零真网、零真凭证。
"""
import json
import os

import pytest

from live.exchange.binance_futures import BinanceFuturesAdapter
from quantos.contracts.config import SafetyViolation
from tools import live_track_advance as drv

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC = open(os.path.join(_ROOT, "tools", "live_track_advance.py"), encoding="utf-8").read()
_ENV = ("BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET")


# --- _VENUES["binance_mainnet"] 注册形状(镜像 test_mexc_venue_registration_shape) ---

def test_binance_mainnet_venue_registration_shape():
    cfg = drv._VENUES["binance_mainnet"]
    assert cfg["adapter"] is BinanceFuturesAdapter
    assert cfg["base_url"] == BinanceFuturesAdapter.MAINNET_BASE
    assert cfg["env"] == _ENV
    assert cfg["key_file"] == os.path.join("key", "binance_mainnet_key.json")
    assert cfg["suffix"] == "_binance_mainnet"
    assert cfg["mandate_file"] == os.path.join("key", "live_mandate_binance_mainnet.json")
    assert cfg["mainnet_only"] is True
    # 期货 venue:**无** spot 标记——不进现货现金门/现货口径限额分支(期货形 gate 默认)
    assert "spot" not in cfg
    # 期货语义,复用 testnet 分支,绝非 _mexc_* 现货函数
    assert cfg["balances"] is drv._binance_balances
    assert cfg["margin"] is drv._binance_margin
    # venue 归一化桥(mainnet_key_safety_skip_reason)依赖 venue_name 做交易所家族归一
    assert cfg["adapter"].venue_name == "binance"


def test_binance_mainnet_credentials_align_with_verify_key_permissions():
    # 驱动器与 verify_key_permissions 的 MAINNET 诊断配置是同一份凭据(drift-lock):
    # mainnet_key_safety_skip_reason("binance_mainnet") 归一化到 "binance" 后加载的必须
    # 正是驱动器自己用的 env/key_file,否则权限门查的不是实际下单的那把 key。
    from tools.verify_key_permissions import _MAINNET_CREDENTIAL_CONFIG
    cfg = drv._VENUES["binance_mainnet"]
    diag = _MAINNET_CREDENTIAL_CONFIG["binance"]
    assert cfg["env"] == diag["env"]
    assert cfg["key_file"] == diag["key_file"]


def test_binance_mainnet_venue_paths_unique():
    # 一个 mandate 只授权一个交易所账户;不同 venue = 不同账户,账本/状态/mandate 绝不共用
    others = {name: c for name, c in drv._VENUES.items() if name != "binance_mainnet"}
    cfg = drv._VENUES["binance_mainnet"]
    assert cfg["suffix"] not in {c["suffix"] for c in others.values()}
    assert cfg["mandate_file"] not in {c["mandate_file"] for c in others.values()}
    ledger = drv._venue_paths(cfg["suffix"])["ledger"]
    for other in others.values():
        assert ledger != drv._venue_paths(other["suffix"])["ledger"]


# --- 凭据加载(通用 _load_credentials 喂 binance_mainnet 的 venue_cfg) ---

def test_binance_mainnet_credentials_env_path(monkeypatch, tmp_path):
    monkeypatch.setenv(_ENV[0], " env-key ")
    monkeypatch.setenv(_ENV[1], " env-secret ")
    monkeypatch.chdir(tmp_path)                       # 无 key 文件也应命中 env
    assert drv._load_credentials(drv._VENUES["binance_mainnet"], "binance_mainnet") == \
        ("env-key", "env-secret")


def test_binance_mainnet_credentials_key_file_path(monkeypatch, tmp_path):
    monkeypatch.delenv(_ENV[0], raising=False)
    monkeypatch.delenv(_ENV[1], raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "key").mkdir()
    (tmp_path / "key" / "binance_mainnet_key.json").write_text(
        json.dumps({"api_key": "file-key", "api_secret": "file-secret"}), encoding="utf-8")
    assert drv._load_credentials(drv._VENUES["binance_mainnet"], "binance_mainnet") == \
        ("file-key", "file-secret")


def test_binance_mainnet_credentials_missing_raises(monkeypatch, tmp_path):
    monkeypatch.delenv(_ENV[0], raising=False)
    monkeypatch.delenv(_ENV[1], raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError):
        drv._load_credentials(drv._VENUES["binance_mainnet"], "binance_mainnet")


# --- mainnet 构造闸门(venue 级等效:无 armed SafetyConfig → 构造即拒) ---

def test_binance_mainnet_adapter_uninstantiable_without_armed_safety():
    with pytest.raises(SafetyViolation):
        BinanceFuturesAdapter(api_key="k", api_secret="s",
                              base_url=drv._VENUES["binance_mainnet"]["base_url"])


def test_driver_refuses_mainnet_only_venue_without_arm_live_source_pin():
    # main() 是网络胶水不可离线跑 → 源码钉子:mainnet_only venue 不带 --arm-live 必须
    # SystemExit(唯一 arming 路径 = 人工 CLI);arm 之后仍强制过一次 key 权限校验
    # (check_binance_key_permissions,SAFE 之外一律 fail-closed 退出)。
    assert 'venue_cfg.get("mainnet_only") and safety_config is None' in _SRC
    assert "check_binance_key_permissions(api_key, api_secret)" in _SRC
    assert "is_safe_to_proceed" in _SRC
    # 期货 venue 绝不进现货分支:mainnet_only 条目不携带 spot 标记
    assert '"mainnet_only": True' in _SRC


# --- mainnet_key_safety_skip_reason:交易所家族归一化(镜像 bybit_demo 三态钉法) ---

def test_mainnet_key_safety_skip_reason_binance_mainnet_noop_without_credentials(
        monkeypatch, tmp_path):
    # 无凭据 = 逐字节 no-op:返回 None、零网络(头号验收标准)
    for env_key in _ENV:
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.chdir(tmp_path)   # key/binance_mainnet_key.json 不存在
    assert drv.mainnet_key_safety_skip_reason("binance_mainnet") is None


def test_mainnet_key_safety_skip_reason_binance_mainnet_maps_to_binance_checker(monkeypatch):
    # binance_mainnet 归一化后必须真的走 binance 的 checker(凭据加载也走归一化后的家族名,
    # 不是原始 venue 字符串——否则 _MAINNET_CREDENTIAL_CONFIG["binance_mainnet"] 上 KeyError)。
    import tools.verify_key_permissions as vkp
    seen = []
    monkeypatch.setattr(vkp, "_load_mainnet_credentials",
                        lambda venue: (seen.append(venue), ("k", "s"))[1])
    unsafe = vkp.KeyPermissionResult(venue="binance", safety=vkp.KeySafety.UNSAFE,
                                     reasons=("enableWithdrawals=true",))
    monkeypatch.setattr(vkp, "_CHECKERS", {"binance": lambda k, s: unsafe})
    reason = drv.mainnet_key_safety_skip_reason("binance_mainnet")
    assert reason is not None
    assert "mainnet_key_permission_unsafe" in reason and "binance_mainnet" in reason
    assert seen == ["binance"]


def test_mainnet_key_safety_skip_reason_binance_mainnet_safe_passes(monkeypatch):
    import tools.verify_key_permissions as vkp
    monkeypatch.setattr(vkp, "_load_mainnet_credentials", lambda venue: ("k", "s"))
    safe = vkp.KeyPermissionResult(venue="binance", safety=vkp.KeySafety.SAFE, reasons=("ok",))
    monkeypatch.setattr(vkp, "_CHECKERS", {"binance": lambda k, s: safe})
    assert drv.mainnet_key_safety_skip_reason("binance_mainnet") is None


# --- mandate 模板(venue/exchange 域钉死 + load_mandate 加载语义) ---

_TEMPLATE = os.path.join(
    _ROOT,
    "config",
    "templates",
    "live_mandate_binance_mainnet.json.template",
)


def _load_template() -> dict:
    with open(_TEMPLATE, encoding="utf-8") as fh:
        return json.load(fh)


def test_mandate_template_domains_match_adapter():
    data = _load_template()
    # venue = 适配器 venue_mode(mainnet base),exchange = 适配器 venue_name(mandate 交易所域)
    assert data["venue"] == "mainnet"
    assert data["exchange"] == BinanceFuturesAdapter.venue_name == "binance"
    assert data["allowed_instruments"]
    assert "<" in data["approved_by"][0]            # 签署人仍是占位符,代码不代填


def test_mandate_template_loads_after_filling_placeholders(tmp_path):
    from live.governance.mandate_store import load_mandate
    data = _load_template()
    data["approved_by"] = ["human-operator-1"]
    path = tmp_path / "live_mandate_binance_mainnet.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    mandate = load_mandate(str(path))
    assert mandate.venue == "mainnet" and mandate.exchange == "binance"
    assert mandate.allowed_instruments == frozenset(data["allowed_instruments"])
    assert mandate.max_gross_notional > 0
    assert dict(mandate.per_instrument_max_notional)


def test_mandate_template_unedited_is_fail_closed_at_evaluation(tmp_path):
    # expires_at=0(模板占位)→ evaluate_mandate 恒 mandate_expired → 绝不 AUTO:
    # 未编辑的模板即便被误复制成正式 mandate 文件,也不可能放行任何真实批次。
    from live.contracts import LiveOrderRequest
    from live.governance.mandate import NEEDS_HUMAN_REVIEW, evaluate_mandate
    from live.governance.mandate_store import load_mandate
    from quantos.contracts.trading import OrderSide
    data = _load_template()
    data["approved_by"] = ["human-operator-1"]
    path = tmp_path / "live_mandate_binance_mainnet.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    mandate = load_mandate(str(path))
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.001,
                             client_order_id="c1")
    decision = evaluate_mandate([order], {"BTC": 60_000.0}, mandate,
                                venue="mainnet", exchange="binance", now=1)
    assert decision.tier == NEEDS_HUMAN_REVIEW


# --- armed preview(2026-07-24):--arm-live 无 --submit = 真实适配器只读透传 + 结构性零提交 ---

def test_armed_preview_config_constructs_mainnet_adapter():
    # armed preview 的全部意义:preview 也能拿到真实 mainnet 适配器(唯读方法透传真实市况)
    cfg = drv.armed_safety_config(["prog", "--arm-live"])
    assert cfg is not None and cfg.live_trading_armed is True
    adapter = BinanceFuturesAdapter(
        api_key="k", api_secret="s", base_url=drv._VENUES["binance_mainnet"]["base_url"],
        safety_config=cfg, http_request=lambda *a, **kw: (200, {}))
    assert adapter.venue_mode == "mainnet"


def test_armed_preview_venue_structurally_cannot_submit():
    # PreviewVenue 包装 **armed** 的真实 venue:place_order 只记录 would_submit、返回
    # PREVIEW_NOT_SUBMITTED,真实 venue 的 place_order 零调用;唯读方法照常透传。
    from live.contracts import LiveOrderRequest
    from quantos.contracts.trading import OrderSide

    class _ArmedRealVenue:
        venue_mode = "mainnet"
        venue_name = "binance"

        def __init__(self):
            self.place_calls = []

        def place_order(self, order):                     # 被调到 = armed preview 破防
            self.place_calls.append(order)
            raise AssertionError("PreviewVenue must never forward place_order")

        def account(self):
            return {"availableBalance": "1.0"}

    real = _ArmedRealVenue()
    preview = drv.PreviewVenue(real)
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.001,
                             client_order_id="c1")
    result = preview.place_order(order)
    assert result.status == "PREVIEW_NOT_SUBMITTED"
    assert preview.would_submit == [order]
    assert real.place_calls == []
    assert preview.account() == {"availableBalance": "1.0"}   # 唯读透传不受影响


def test_armed_preview_submit_branch_never_entered_source_pin():
    # main() 网络胶水不可离线跑(仓库惯例)→ 源码钉子:不带 --submit 时 venue 恒为
    # PreviewVenue(real_venue)、写账本恒为 :memory: 草稿——armed preview 的两道结构性保险。
    assert "real_venue if submit else PreviewVenue(real_venue)" in _SRC
    assert 'ledger if submit else LiveLedger(":memory:", create=True)' in _SRC


def test_venue_requires_arm_matches_mainnet_only_set():
    # arm 面 = 驱动器两处启动闸门(spot/mainnet_only)的并集,ops /preview 据此加 --arm-live
    assert drv.venue_requires_arm("binance_mainnet") is True
    assert drv.venue_requires_arm("mexc") is True
    for testnet in ("binance", "bybit", "bybit_demo"):
        assert drv.venue_requires_arm(testnet) is False
    for name, cfg in drv._VENUES.items():
        assert drv.venue_requires_arm(name) == bool(cfg.get("mainnet_only") or cfg.get("spot"))
