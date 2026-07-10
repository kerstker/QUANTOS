"""Offline, deterministic top-tier capability assessment.

The registry declares targets only. Current status is derived from named,
read-only evidence checkers so editing a score file cannot raise maturity.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


DOMAINS = {
    "data_moat", "alpha_research", "validation_model_risk",
    "portfolio_construction", "risk_management", "execution_microstructure",
    "ai_agents", "governance_security", "platform_engineering",
    "reliability_operations", "frontend_console",
}
TIERS = {"A", "B"}
MATURITIES = {f"M{i}" for i in range(6)}
CLASSIFICATIONS = {"L2_SAFE_NOW", "NEEDS_EXTERNAL", "POST_L2"}
EVIDENCE_TYPES = {
    "implementation", "verification", "data", "operation", "governance", "reproducibility",
}
STATUSES = {"PASS", "PARTIAL", "FAILED", "UNVERIFIED", "MANUAL_EVIDENCE_REQUIRED"}


class RegistryError(ValueError):
    pass


def _paths_exist(repo_root: Path, args: dict[str, Any], target: str) -> dict[str, Any]:
    paths = args.get("paths", [])
    if not isinstance(paths, list) or not paths:
        return _result("UNVERIFIED", "M0", [], "checker has no evidence paths")
    missing = [path for path in paths if not (repo_root / path).exists()]
    if missing:
        return _result("FAILED", "M0", paths, "missing: " + ", ".join(missing))
    maturity = args.get("pass_maturity", target)
    return _result("PASS", maturity, paths, "all declared evidence paths exist")


def _text_contains_all(repo_root: Path, args: dict[str, Any], target: str) -> dict[str, Any]:
    path = args.get("path")
    needles = args.get("needles", [])
    if not isinstance(path, str) or not isinstance(needles, list) or not needles:
        return _result("UNVERIFIED", "M0", [], "checker arguments are incomplete")
    evidence = repo_root / path
    if not evidence.is_file():
        return _result("FAILED", "M0", [path], f"missing: {path}")
    text = evidence.read_text(encoding="utf-8")
    missing = [needle for needle in needles if needle not in text]
    if missing:
        return _result("FAILED", "M1", [path], "missing required markers: " + ", ".join(missing))
    maturity = args.get("pass_maturity", target)
    return _result("PASS", maturity, [path], "all required markers are present")


def _manual_evidence(_repo_root: Path, _args: dict[str, Any], _target: str) -> dict[str, Any]:
    return _result("MANUAL_EVIDENCE_REQUIRED", "M1", [], "requires external or human-verified evidence")


def _result(status: str, maturity: str, evidence: list[str], detail: str) -> dict[str, Any]:
    return {"status": status, "current_maturity": maturity, "evidence": evidence, "detail": detail}


CHECKERS: dict[str, Callable[[Path, dict[str, Any], str], dict[str, Any]]] = {
    "paths_exist": _paths_exist,
    "text_contains_all": _text_contains_all,
    "manual_evidence": _manual_evidence,
}


def load_requirements(path: str | Path) -> list[dict[str, Any]]:
    registry_path = Path(path)
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot load registry: {exc}") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("requirements"), list):
        raise RegistryError("registry requires schema_version=1 and a requirements list")
    requirements = payload["requirements"]
    seen: set[str] = set()
    for index, item in enumerate(requirements):
        _validate_requirement(item, index)
        requirement_id = item["id"]
        if requirement_id in seen:
            raise RegistryError(f"duplicate requirement id: {requirement_id}")
        seen.add(requirement_id)
    return sorted(requirements, key=lambda item: item["id"])


def _validate_requirement(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise RegistryError(f"requirement {index} must be an object")
    required = {
        "id", "domain", "tier", "target_maturity", "classification", "description",
        "required_evidence", "red_line", "checker", "next_action",
    }
    missing = sorted(required - item.keys())
    if missing:
        raise RegistryError(f"requirement {index} missing fields: {', '.join(missing)}")
    if not isinstance(item["id"], str) or not item["id"]:
        raise RegistryError("id must be a non-empty string")
    _require_enum(item, "domain", DOMAINS)
    _require_enum(item, "tier", TIERS)
    _require_enum(item, "target_maturity", MATURITIES)
    _require_enum(item, "classification", CLASSIFICATIONS)
    _require_enum(item, "checker", set(CHECKERS))
    evidence = item["required_evidence"]
    if not isinstance(evidence, list) or not evidence or any(value not in EVIDENCE_TYPES for value in evidence):
        raise RegistryError("required_evidence contains an invalid value")
    if not isinstance(item["red_line"], bool):
        raise RegistryError("red_line must be boolean")
    if "checker_args" in item and not isinstance(item["checker_args"], dict):
        raise RegistryError("checker_args must be an object")


def _require_enum(item: dict[str, Any], field: str, allowed: set[str]) -> None:
    if item[field] not in allowed:
        raise RegistryError(f"{field} has invalid value: {item[field]!r}")


def assess_repository(repo_root: str | Path, requirements_path: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    requirements = load_requirements(requirements_path)
    assessed: list[dict[str, Any]] = []
    for requirement in requirements:
        if requirement["classification"] in {"NEEDS_EXTERNAL", "POST_L2"}:
            result = _manual_evidence(root, {}, requirement["target_maturity"])
        else:
            checker = CHECKERS[requirement["checker"]]
            result = checker(root, requirement.get("checker_args", {}), requirement["target_maturity"])
        if result["status"] not in STATUSES or result["current_maturity"] not in MATURITIES:
            raise RegistryError(f"checker returned invalid result for {requirement['id']}")
        assessed.append({
            **requirement,
            **result,
            "meets_target": result["status"] == "PASS"
            and _maturity_value(result["current_maturity"]) >= _maturity_value(requirement["target_maturity"]),
        })

    red_lines_failed = sum(1 for item in assessed if item["red_line"] and item["status"] != "PASS")
    tier_a = [item for item in assessed if item["tier"] == "A"]
    if red_lines_failed:
        verdict = "NOT_TOP_TIER"
    elif tier_a and all(item["meets_target"] for item in tier_a):
        verdict = "TOP_TIER_RESEARCH"
    else:
        verdict = "IN_PROGRESS"

    domains = []
    for domain in sorted({item["domain"] for item in assessed}):
        items = [item for item in assessed if item["domain"] == domain]
        current = min(items, key=lambda item: _maturity_value(item["current_maturity"]))["current_maturity"]
        domains.append({
            "domain": domain,
            "current_maturity": current,
            "requirements": len(items),
            "passed": sum(1 for item in items if item["status"] == "PASS"),
            "red_lines_failed": sum(1 for item in items if item["red_line"] and item["status"] != "PASS"),
        })
    return {
        "schema_version": 1,
        "verdict": verdict,
        "summary": {
            "total": len(assessed),
            "passed": sum(1 for item in assessed if item["status"] == "PASS"),
            "partial": sum(1 for item in assessed if item["status"] == "PARTIAL"),
            "failed": sum(1 for item in assessed if item["status"] == "FAILED"),
            "unverified": sum(1 for item in assessed if item["status"] == "UNVERIFIED"),
            "manual_evidence_required": sum(
                1 for item in assessed if item["status"] == "MANUAL_EVIDENCE_REQUIRED"),
            "red_lines_failed": red_lines_failed,
        },
        "domains": domains,
        "requirements": assessed,
    }


def _maturity_value(value: str) -> int:
    return int(value[1:])
