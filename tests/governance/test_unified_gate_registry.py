"""The capability registry must require the executable native release checks."""

import json
from pathlib import Path

from scripts.validate import CHECKS


ROOT = Path(__file__).resolve().parents[2]


def test_unified_gate_requires_current_native_evidence_checks():
    registry = json.loads((ROOT / "config/top_tier_requirements.json").read_text())
    requirement = next(row for row in registry["requirements"] if row["id"] == "PLATFORM.CI.UNIFIED_GATE")
    needles = set(requirement["checker_args"]["needles"])
    generators = {
        "generate_arm64_container_audit_evidence.py",
        "generate_arm64_container_reproducibility_evidence.py",
        "generate_arm64_console_smoke_evidence.py",
    }
    native_checks = {
        name for name, argv, _cwd in CHECKS
        if any(Path(arg).name in generators for arg in argv) and "--check" in argv
    }
    assert len(native_checks) == len(generators)
    assert native_checks <= needles
    declared_evidence_checks = {value for value in needles if value.endswith("-evidence:current")}
    assert declared_evidence_checks <= {name for name, _argv, _cwd in CHECKS}
