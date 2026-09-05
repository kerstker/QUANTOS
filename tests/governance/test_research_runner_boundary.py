"""治理:research_runner 是唯一可变面,机器钉死它"只研究、绝不碰交易"。

AST 扫描 research_runner 包:无券商/交易所 SDK、不 import services.trading、无下单/转账调用;
且 RUN_KINDS 白名单每个入口都指向 services/tools 的研究函数(不含 trading/order),参数 schema 受类型约束。
镜像 tests/governance/test_crypto_data_boundary.py 的写法。"""
import ast
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Make `services` importable from ANY cwd/rootdir so the whitelist tests below truly run their boundary
# assertions instead of silently failing/skipping with ModuleNotFoundError (was a portability P2).
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_PKG = os.path.join(_ROOT, "services", "research_runner")
_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_FORBIDDEN_CALLS = ["create_order", "place_order", "submit_order", "cancel_order", "create_market_order",
                    "create_limit_order", "fapiPrivate", "sapiPost", ".withdraw(", "create_withdrawal",
                    "transfer("]


def _import_roots(path: str) -> set:
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


def _pyfiles() -> list:
    return [os.path.join(_PKG, f) for f in os.listdir(_PKG) if f.endswith(".py")]


def _dynamic_literal_imports(path: str) -> set[str]:
    tree = ast.parse(open(path, encoding="utf-8").read())
    modules = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
            and func.attr == "import_module"
        ):
            modules.add(node.args[0].value)
    return modules


def test_research_core_does_not_own_http_tool_runner_adapter():
    old_package = os.path.join(_ROOT, "services", "research", "research_runner")
    assert not list(Path(old_package).glob("*.py"))

    offenders = []
    research_root = os.path.join(_ROOT, "services", "research")
    for root, dirs, files in os.walk(research_root):
        dirs[:] = [directory for directory in dirs if directory not in {"tests", "__pycache__"}]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(root, filename)
            dynamic_tools = sorted(
                module for module in _dynamic_literal_imports(path) if module == "tools" or module.startswith("tools.")
            )
            if dynamic_tools:
                offenders.append(f"{os.path.relpath(path, _ROOT)}:{dynamic_tools}")
    assert not offenders, offenders


def test_current_runner_launchers_reference_the_adapter_domain():
    current_guides = [
        os.path.join(_ROOT, "AGENTS.md"),
        os.path.join(_ROOT, "CLAUDE.md"),
        os.path.join(_ROOT, "docs", "RESEARCH_WORKFLOW.md"),
        os.path.join(_ROOT, "scripts", "start_console.ps1"),
    ]
    old_entrypoint = "services.research.research_runner.main:app"
    new_entrypoint = "services.research_runner.main:app"
    for path in current_guides:
        source = open(path, encoding="utf-8").read()
        assert old_entrypoint not in source, path
        assert new_entrypoint in source, path


def test_console_launcher_creates_no_interactive_window():
    path = os.path.join(_ROOT, "scripts", "start_console.ps1")
    source = open(path, encoding="utf-8").read()

    assert "Start-Process" not in source
    assert "$launch.UseShellExecute = $false" in source
    assert "$launch.CreateNoWindow = $true" in source
    assert "ProcessWindowStyle]::Hidden" in source


def test_runner_no_broker_sdk_or_trading_import_or_order_call():
    for p in _pyfiles():
        roots = _import_roots(p)
        assert not (roots & _FORBIDDEN_SDK), f"broker/exchange SDK in {os.path.basename(p)}: {sorted(roots & _FORBIDDEN_SDK)}"
        src = open(p, encoding="utf-8").read()
        assert "services.trading" not in src, f"trading-path import in {os.path.basename(p)}"
        hits = [c for c in _FORBIDDEN_CALLS if c in src]
        assert not hits, f"order/transfer call in {os.path.basename(p)}: {hits}"


def test_whitelist_entrypoints_are_research_only():
    from services.research_runner.run_kinds import RUN_KINDS, ProvenancePolicy
    assert RUN_KINDS, "whitelist must be non-empty"
    for kind, spec in RUN_KINDS.items():
        root = spec.entrypoint.split(":")[0].split(".")[0]
        assert root in {"services", "tools"}, f"{kind}: entrypoint root {root!r} not research"
        assert "trading" not in spec.entrypoint and "order" not in spec.entrypoint, f"{kind}: non-research entrypoint"
        assert isinstance(spec.param_schema, dict), f"{kind}: param schema must be a typed dict"
        assert isinstance(spec.provenance_policy, ProvenancePolicy), f"{kind}: missing provenance policy"


def test_runner_selection_authority_is_explicit_and_diagnostics_are_ineligible():
    from services.research_runner.run_kinds import (
        GLOBAL_TRIAL_LEDGER,
        LEGACY_QUARANTINED_RUN_KINDS,
        RUN_KINDS,
        RunPurpose,
    )

    expected_selection_bearing = {"factory_orthogonal", "factory_loop"}
    assert LEGACY_QUARANTINED_RUN_KINDS == {
        "ashare_alpha",
        "ashare_paper_track",
        "carry_strategy",
        "combo_book",
        "factory_orthogonal",
        "factory_loop",
        "ic_panel",
        "oi_research",
        "paper_cycle",
        "survivorship",
        "wide_gauntlet",
    }
    assert set(RUN_KINDS) - LEGACY_QUARANTINED_RUN_KINDS == {
        "ashare_forward_observation",
        "ashare_technical_search",
        "data_refresh",
        "perpetual_replay",
    }
    actual_selection_bearing = {
        kind
        for kind, spec in RUN_KINDS.items()
        if spec.provenance_policy.purpose is RunPurpose.SELECTION_BEARING
    }
    assert actual_selection_bearing == expected_selection_bearing
    for kind, spec in RUN_KINDS.items():
        policy = spec.provenance_policy
        if kind in expected_selection_bearing:
            assert policy.evidence_authority == GLOBAL_TRIAL_LEDGER
        else:
            assert policy.purpose is RunPurpose.DIAGNOSTIC_ONLY
            assert policy.evidence_authority is None


def test_runner_owns_structured_provenance_and_adapters_cannot_override_it():
    source = open(os.path.join(_PKG, "runner.py"), encoding="utf-8").read()
    assert "quantos.research_runner.provenance.v1" in source
    assert '"selection_eligible": False' in source
    assert "NON_AUTHORITATIVE_RUN_REGISTRY_MIRROR" in source
    assert "HEAD_ONLY_WORKTREE_UNATTESTED" in source
    assert "validate_run_provenance(queued, spec)" in source
    assert "validate_run_provenance(completed, spec)" in source
    assert 'provenance=a.get("provenance"' not in source


def test_runner_uses_unique_insert_and_cas_status_transitions():
    source = open(os.path.join(_PKG, "runner.py"), encoding="utf-8").read()
    assert "uuid.uuid4().hex" in source
    assert "self._reg.insert_or_get(queued)" in source
    assert "compare_and_set_status" in source
    assert "replace_if_status" in source
    assert "self._reg.upsert(" not in source
    assert "self._reg.set_status(" not in source

    store_source = open(
        os.path.join(_ROOT, "services", "research", "run_registry", "store.py"),
        encoding="utf-8",
    ).read()
    assert "INSERT OR REPLACE" not in store_source
    assert "ux_runs_runner_invocation" in store_source
    assert "terminal result does not match persisted immutable run identity" in store_source


def test_whitelist_is_subset_of_registry_allowed_kinds():
    # the runner can only produce kinds the registry will accept (defence in depth, kept in sync)
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research.run_registry.contract import ALLOWED_KINDS
    assert set(RUN_KINDS) <= ALLOWED_KINDS, f"runner kinds not in registry whitelist: {set(RUN_KINDS) - ALLOWED_KINDS}"


def test_v1_crypto_candidate_and_paper_shadow_chain_is_not_runner_admitted():
    from quantos.contracts.l2_schedule_policy import MVP_DISABLED_MODULES
    from services.research_runner.run_kinds import RUN_KINDS

    retained = {
        "realtime_alpha_discovery",
        "realtime_alpha_forward_shadow",
        "realtime_alpha_formal_shadow",
    }
    assert retained <= MVP_DISABLED_MODULES
    assert retained.isdisjoint(RUN_KINDS)

    discovery = Path(_ROOT, "tools", "realtime_alpha_discovery.py").read_text(
        encoding="utf-8"
    )
    forward = Path(_ROOT, "tools", "realtime_alpha_forward_shadow.py").read_text(
        encoding="utf-8"
    )
    formal = Path(_ROOT, "tools", "realtime_alpha_formal_shadow.py").read_text(
        encoding="utf-8"
    )
    assert "frozen_paper_runs" in discovery
    assert "build_feedback_plan(" in discovery
    assert "realize_forward_paper_decision(" in forward
    assert "record_paper_run(" in forward
    assert "run_cycle as _run_cycle" in formal


# --- 2026-07-05 安全审查 MEDIUM 修复:/cancel 与 /run 同等问责要求 ---
# --- 2026-07-06 续:/cancel 与 /run 同等本机共享密钥认证要求 ---

def test_cancel_requires_operator_header():
    from fastapi.testclient import TestClient
    from services.research_runner import main as m
    client = TestClient(m.app)
    r = client.post("/cancel", json={"run_id": "run_nonexistent"},
                    headers={"X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 400


def test_cancel_accepts_operator_header_and_records_it():
    from fastapi.testclient import TestClient
    from services.research_runner import main as m
    client = TestClient(m.app)
    r = client.post("/cancel", json={"run_id": "run_nonexistent"},
                    headers={"X-Operator": "alice", "X-Runner-Token": m._RUNNER_TOKEN})
    assert r.status_code == 200
    assert r.json()["operator"] == "alice"


def test_cancel_requires_runner_token():
    from fastapi.testclient import TestClient
    from services.research_runner import main as m
    client = TestClient(m.app)
    r = client.post("/cancel", json={"run_id": "run_nonexistent"},
                    headers={"X-Operator": "alice"})
    assert r.status_code == 401
