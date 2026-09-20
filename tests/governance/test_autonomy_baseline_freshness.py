"""2026-09-20 审计 P0-2-F:autonomy baseline 需要一个只读的新鲜度校验入口。

审计发现两点:

1. ``scripts/generate_autonomy_baseline.py`` 只有 ``--output`` / ``--gap-report``
   / ``--now-ms``,**没有任何只读校验入口**;
2. ``tests/governance/test_autonomy_baseline.py`` 只把新生成的结果写进临时目录、
   再断言其文本——**从不与已提交的 artifact 比对**。

于是已提交的 ``evidence/autonomy_baseline.json`` 漂移了也无人察觉。实测当前漂移
字段为:commit、current_evidence、evidence_priority、generator_sha256、input_hash、
inputs、phase_status、remaining_blockers(stored generator_sha256 ``c1d17284…``
vs actual ``133b1a2e…``)。

本文件锁定新加的 ``--check`` 入口本身是可靠的。**注意:这里刻意没有把 ``--check``
接进门禁**——artifact 当前确实是陈旧的,接线会让门禁变红。修复该 artifact 会改动
一个被已签名清单(evidence/external_sigstore/2026-07-30/quantos-source.manifest.json)
pin 住的文件,因而属于签名证据工作流,必须由该工作流的所有者一并刷新清单后接线。
在那之前,这个入口让缺口在需要时可见,而不是假装它是新鲜的。
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "generate_autonomy_baseline.py"


def test_check_accepts_a_freshly_generated_baseline(tmp_path):
    output = tmp_path / "baseline.json"
    gap = tmp_path / "gap.md"

    generated = subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(output), "--gap-report", str(gap)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr

    checked = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "current" in checked.stdout


@pytest.mark.parametrize(
    "field",
    ["commit", "generator_sha256", "input_hash", "phase_status", "inputs"],
)
def test_check_detects_field_drift(tmp_path, field):
    output = tmp_path / "baseline.json"
    gap = tmp_path / "gap.md"
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(output), "--gap-report", str(gap)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    payload[field] = {"tampered": True} if isinstance(payload[field], dict) else "tampered"
    output.write_text(json.dumps(payload), encoding="utf-8")

    checked = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode != 0, f"drift in {field!r} was not detected"
    assert field in checked.stdout, checked.stdout


def test_check_is_read_only(tmp_path):
    output = tmp_path / "baseline.json"
    gap = tmp_path / "gap.md"
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(output), "--gap-report", str(gap)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    before = output.read_bytes()

    subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert output.read_bytes() == before, "--check must never rewrite the artifact"


def test_check_reports_an_unreadable_artifact_instead_of_crashing(tmp_path):
    output = tmp_path / "baseline.json"
    output.write_text("{not json", encoding="utf-8")

    checked = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode != 0
    assert "unreadable" in checked.stdout


def test_committed_artifact_is_known_stale_not_silently_green(tmp_path):
    """把"当前 artifact 是陈旧的"记录成机器事实,而不是让它继续假装新鲜。

    当签名证据工作流修复并接线 ``--check`` 之后,这个测试应当被删除或反转为
    "已提交的 artifact 必须通过 --check"。
    """
    committed = ROOT / "evidence" / "autonomy_baseline.json"
    if not committed.is_file():
        pytest.skip("no committed autonomy baseline in this checkout")

    checked = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(committed)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode != 0, (
        "evidence/autonomy_baseline.json now passes --check; if the signed-evidence "
        "workstream repaired it, delete this test and wire --check into validate.py"
    )
    assert "drifted on" in checked.stdout
