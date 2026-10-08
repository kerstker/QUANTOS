"""2026-09-20 审计 P0-2:证据树不得再出现未经准入的 authority 声明。

背景。`2026-08-29` 以前的多周期回测、候选选择、分数复用与 paper fill 路径的
盈利/权威结论已被 `INVALIDATED_PERP_LEDGER_V1` 覆盖(见
services/execution/backtest_authority.py),不能重新引用为有效业绩。但保留的
证据文件本身仍写着 `"authoritative": true`:

    evidence/alpha_paper_cycle.json       (dry_run / canonical_dry_run 两处)
    evidence/three_alpha_paper_flow.json  (三处)

这两个文件是**追加式证据**,其摘要被签名清单
(evidence/external_sigstore/.../quantos-source.manifest.json)pinned,
因此审计要求的是"加读取层守卫",不是改写历史字节。本文件锁定:

1. 任何 tracked `evidence/**/*.json` 里的 `"authoritative": true` 必须要么被
   `is_authoritative_backtest_record()` 真正接纳,要么属于已知的 legacy 失效集合;
2. 新出现的、两条都不满足的 authority 声明会让本测试变红(fail-closed);
3. 准入谓词当前对任何记录都返回 False —— 即今天没有任何"当前有效"的权威记录,
   所以证据里剩下的 `authoritative: true` 按定义都是历史遗留、不能当作业绩引用。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence"

# 已由 727 审计投影以 LEGACY_INVALIDATION_CODE 标记的历史盈利类 artifact。
# 新增条目必须同时进入 scripts/generate_727_final_audit.py 的
# _LEGACY_PROFITABILITY_ARTIFACTS,否则第 3 个测试会失败。
LEGACY_INVALIDATED_EVIDENCE = frozenset(
    {
        "evidence/alpha_paper_cycle.json",
        "evidence/three_alpha_paper_flow.json",
    }
)


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses 解析注解时会回头查 sys.modules[cls.__module__],按路径加载的
    # 模块必须先登记,否则 @dataclass 在 exec_module 期间就炸。
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _iter_authority_claims(value, location: str = ""):
    """递归产出文档里每一处 `authoritative` 键的 (json 路径, 值)。"""
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{location}.{key}" if location else str(key)
            if key == "authoritative":
                yield here, item
            yield from _iter_authority_claims(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_authority_claims(item, f"{location}[{index}]")


def _claimed_authoritative(document) -> bool:
    return any(claim is True for _, claim in _iter_authority_claims(document))


def _unguarded_authoritative_claims(documents: dict, legacy) -> list:
    """返回声称 authority 却既未获准入、也不在 legacy 失效集合里的文件。"""
    return sorted(
        relative
        for relative, document in documents.items()
        if _claimed_authoritative(document) and relative not in legacy
    )


def _evidence_documents() -> dict:
    documents = {}
    for path in sorted(EVIDENCE.rglob("*.json")):
        try:
            documents[path.relative_to(ROOT).as_posix()] = json.loads(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # 非 JSON 或不可读的证据文件不参与 authority 声明扫描;
            # 它们由各自的证据门禁负责。
            continue
    return documents


# --------------------------------------------------------------------------
# 1. 真实证据树
# --------------------------------------------------------------------------


def test_no_unguarded_authoritative_claim_in_evidence_tree():
    documents = _evidence_documents()
    assert documents, "evidence tree scan found no JSON documents"

    offenders = _unguarded_authoritative_claims(documents, LEGACY_INVALIDATED_EVIDENCE)

    assert offenders == [], (
        "these evidence documents claim authoritative:true but are neither admitted "
        "by is_authoritative_backtest_record() nor marked as legacy-invalidated: "
        f"{offenders}"
    )


def test_known_legacy_evidence_is_invalidated_not_merely_renamed():
    """legacy 集合必须仍是审计投影覆盖的那一批,否则"失效"只是本文件里的口头约定。"""
    audit = _load_module("quantos_727_audit_authority_claims", "scripts/generate_727_final_audit.py")
    projected = {str(path) for path in audit._LEGACY_PROFITABILITY_ARTIFACTS}

    missing = sorted(LEGACY_INVALIDATED_EVIDENCE - projected)
    assert missing == [], (
        "these paths are allowed to carry authoritative:true here but are not covered "
        f"by the 727 legacy-invalidation projection: {missing}"
    )


def test_nothing_is_currently_admitted_as_authoritative():
    """外部签名验证未接入 -> 准入谓词对任何记录都必须返回 False。"""
    authority = _load_module("quantos_backtest_authority_claims", "services/execution/backtest_authority.py")

    assert authority.is_authoritative_backtest_record({}) is False
    assert authority.is_authoritative_backtest_record(
        {
            "detail": {
                "engine_semantics": getattr(authority, "CANONICAL_ENGINE_SEMANTICS", None),
                "evidence_status": getattr(authority, "AUTHORITATIVE_EVIDENCE_STATUS", None),
                "engine_attestations": {},
            }
        }
    ) is False


# --------------------------------------------------------------------------
# 2. 守卫本身必须真的会拦(否则上面的绿灯毫无意义)
# --------------------------------------------------------------------------


def test_guard_detects_a_new_unguarded_authority_claim():
    documents = {
        "evidence/alpha_paper_cycle.json": {"dry_run": {"authoritative": True}},
        "evidence/brand_new_report.json": {"nested": [{"authoritative": True}]},
    }

    offenders = _unguarded_authoritative_claims(documents, LEGACY_INVALIDATED_EVIDENCE)

    assert offenders == ["evidence/brand_new_report.json"], (
        "a newly added authoritative claim outside the legacy allowlist must be caught"
    )


def test_guard_ignores_false_and_null_authority_claims():
    documents = {
        "evidence/diagnostic.json": {"authoritative": None, "other": {"authoritative": False}},
        "evidence/clean.json": {"metrics": {"sharpe": 1.0}},
    }

    assert _unguarded_authoritative_claims(documents, LEGACY_INVALIDATED_EVIDENCE) == []


@pytest.mark.parametrize(
    "document",
    [
        {"authoritative": True},
        {"a": {"b": {"authoritative": True}}},
        {"a": [{"authoritative": True}]},
        {"a": [{"b": [{"authoritative": True}]}]},
    ],
)
def test_claim_detection_sees_nested_shapes(document):
    assert _claimed_authoritative(document) is True
