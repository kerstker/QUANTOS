from __future__ import annotations

import ast
import json
from dataclasses import asdict
from pathlib import Path

from quantos.contracts.mft_execution import create_testnet_execution_intent
from quantos.contracts.portfolio import TargetPosition
from services.risk.pre_trade.config import RiskLimits
from tools import testnet_mandate_proposal


NOW = 2_000_000


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    intent = create_testnet_execution_intent(
        portfolio_id="hourly-paper",
        alpha_ids=("a" * 64,),
        decided_at_ms=NOW - 10,
        expires_at_ms=NOW + 10_000,
        targets=(TargetPosition(instrument_id="BTC", target_weight=0.1),),
        market_watermark_ms=NOW - 50,
        market_snapshot_hash="1" * 64,
        workflow_receipt_hash="2" * 64,
        validation_artifact_hashes=("3" * 64,),
        policy_version="mft-testnet.v1",
        policy_hash="4" * 64,
        testnet_submission_requested=True,
    )
    limits = asdict(
        RiskLimits(
            allowed_instruments=frozenset({"BTC"}),
            allowed_accounts=frozenset({"testnet-account"}),
            max_order_notional=25_000.0,
            max_gross_exposure=80_000.0,
            max_net_exposure=40_000.0,
        )
    )
    limits["allowed_instruments"] = ["BTC"]
    limits["allowed_accounts"] = ["testnet-account"]
    limits["max_position_qty"] = []
    runtime = {
        "schema_version": "mft-testnet-runtime.v1",
        "market_snapshot_hash": intent.market_snapshot_hash,
        "market_watermark_ms": intent.market_watermark_ms,
        "risk_policy_version": "operator-risk.v1",
        "risk_limits": limits,
    }
    intent_path = tmp_path / "intent.json"
    runtime_path = tmp_path / "runtime.json"
    intent_path.write_text(intent.model_dump_json(), encoding="utf-8")
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    return intent_path, runtime_path


def test_cli_writes_unsigned_review_only_proposal(tmp_path, capsys) -> None:
    intent, runtime = _write_inputs(tmp_path)
    output = tmp_path / "proposal.json"

    code = testnet_mandate_proposal.main(
        ["--intent", str(intent), "--runtime-input", str(runtime), "--out", str(output)],
        clock_ms=lambda: NOW,
    )

    assert code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    rendered = json.loads(capsys.readouterr().out)
    assert document["status"] == "HUMAN_REVIEW_REQUIRED"
    assert rendered["proposal_hash"] == document["proposal_hash"]
    assert document["unsigned_mandate"]["approved_by"] == []
    assert document["execution_authority"] is False


def test_cli_fails_closed_without_leaking_paths(tmp_path, capsys) -> None:
    intent, runtime = _write_inputs(tmp_path)
    body = json.loads(runtime.read_text(encoding="utf-8"))
    body["market_snapshot_hash"] = "f" * 64
    runtime.write_text(json.dumps(body), encoding="utf-8")
    output = tmp_path / "proposal.json"

    code = testnet_mandate_proposal.main(
        ["--intent", str(intent), "--runtime-input", str(runtime), "--out", str(output)],
        clock_ms=lambda: NOW,
    )

    assert code == 3
    rendered = capsys.readouterr().out
    assert str(intent) not in rendered
    assert str(runtime) not in rendered
    assert not output.exists()


def test_cli_has_no_credentials_network_adapter_or_submission_surface() -> None:
    source = Path(testnet_mandate_proposal.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "BINANCE_TESTNET_API_KEY" not in source
    assert "BINANCE_TESTNET_API_SECRET" not in source
    assert not any(
        name.startswith(("live.exchange", "requests", "httpx", "urllib"))
        for name in imports
    )
    options = {
        option
        for action in testnet_mandate_proposal.build_parser()._actions
        for option in action.option_strings
    }
    assert not any("submit" in option or "credential" in option for option in options)
