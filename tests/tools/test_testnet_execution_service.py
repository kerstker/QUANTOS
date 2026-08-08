from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import pytest

from quantos.contracts.mft_execution import create_testnet_execution_intent
from quantos.contracts.portfolio import TargetPosition
from services.risk.pre_trade.config import RiskLimits
from tools import testnet_execution_service as cli


NOW = 2_000_000


def _intent(*, requested: bool = True):
    return create_testnet_execution_intent(
        portfolio_id="hourly-paper",
        alpha_ids=("a" * 64,),
        decided_at_ms=NOW - 10,
        expires_at_ms=NOW + 10_000,
        targets=(TargetPosition(instrument_id="BTC", target_weight=0.25),),
        market_watermark_ms=NOW - 50,
        market_snapshot_hash="1" * 64,
        workflow_receipt_hash="2" * 64,
        validation_artifact_hashes=("3" * 64,),
        policy_version="mft-testnet.v1",
        policy_hash="4" * 64,
        testnet_submission_requested=requested,
    )


def _write_intent(tmp_path, *, requested: bool = True):
    path = tmp_path / "intent.json"
    path.write_text(_intent(requested=requested).model_dump_json(), encoding="utf-8")
    return path


@dataclass
class _Record:
    state: object
    reason_codes: tuple[str, ...] = ()


@dataclass
class _Run:
    status: str
    command: _Record | None = None


class _Service:
    def __init__(self, status: str = "PREVIEW_READY") -> None:
        self.status = status
        self.enqueued = []
        self.calls = []

    def enqueue(self, intent, *, recorded_at_ms):
        self.enqueued.append((intent, recorded_at_ms))

    def run_once(self, *, now_ms, worker_id, submit_enabled):
        self.calls.append((now_ms, worker_id, submit_enabled))
        return _Run(self.status)


class _Runner:
    def __init__(self, service: _Service) -> None:
        self.service = service
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _base_args(tmp_path, *, requested: bool = True):
    return [
        "--intent",
        str(_write_intent(tmp_path, requested=requested)),
        "--authority",
        str(tmp_path / "authority.json"),
        "--mandate",
        str(tmp_path / "mandate.json"),
        "--attestations",
        str(tmp_path / "attestations.json"),
        "--kill-switch",
        str(tmp_path / "kill-switch.json"),
        "--command-db",
        str(tmp_path / "commands.db"),
        "--recovery-db",
        str(tmp_path / "recovery.db"),
    ]


def _submit_args(tmp_path):
    return [
        *_base_args(tmp_path),
        "--runtime-input",
        str(tmp_path / "runtime.json"),
        "--ledger-db",
        str(tmp_path / "ledger.db"),
        "--audit-db",
        str(tmp_path / "audit.db"),
        "--testnet-submit",
    ]


def _write_authority_files(tmp_path) -> None:
    (tmp_path / "authority.json").write_text(
        json.dumps(
            {
                "schema_version": "mft-testnet-authority.v1",
                "workflow_receipt_hash": "2" * 64,
                "validation_artifact_hashes": ["3" * 64],
                "market_snapshot_hash": "1" * 64,
                "market_watermark_ms": NOW - 50,
                "market_contiguous": True,
                "policy_version": "mft-testnet.v1",
                "policy_hash": "4" * 64,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "mandate.json").write_text(
        json.dumps(
            {
                "mandate_id": "human-testnet-mandate",
                "venue": "testnet",
                "exchange": "binance",
                "allowed_instruments": ["BTC"],
                "max_single_order_notional": 1_000_000.0,
                "max_gross_notional": 1_000_000.0,
                "max_net_notional": 1_000_000.0,
                "approved_by": ["human-operator"],
                "issued_at": NOW - 1_000,
                "expires_at": NOW + 1_000,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "attestations.json").write_text(
        json.dumps(
            {
                "hourly-paper": {
                    "kind": "validation_pass",
                    "attested_by": "human-validator",
                    "attested_at": NOW - 500,
                    "validation_result_hash": "3" * 64,
                }
            }
        ),
        encoding="utf-8",
    )


def _write_runtime(tmp_path) -> None:
    limits = asdict(
        RiskLimits(
            allowed_instruments=frozenset({"BTC"}),
            allowed_accounts=frozenset({"testnet-account"}),
        )
    )
    limits["allowed_instruments"] = ["BTC"]
    limits["allowed_accounts"] = ["testnet-account"]
    limits["max_position_qty"] = []
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": "mft-testnet-runtime.v1",
                "market_snapshot_hash": "1" * 64,
                "market_watermark_ms": NOW - 50,
                "risk_policy_version": "operator-risk.v1",
                "risk_limits": limits,
                "aum": 100_000.0,
                "prices": {"BTC": 60_000.0},
                "current_positions": {},
                "account_positions": {},
                "account": {
                    "account_id": "testnet-account",
                    "as_of_ms": NOW - 25,
                    "cash": 100_000.0,
                    "equity": 100_000.0,
                    "day_pnl": 0.0,
                    "peak_equity": 100_000.0,
                },
                "market": {
                    "BTC": {
                        "price_as_of_ms": NOW - 50,
                        "market_open": True,
                        "est_daily_volume_units": 1_000_000_000.0,
                        "instrument_vol": 0.5,
                    }
                },
                "gate_max_staleness_ms": 1_000,
                "account_max_age_ms": 1_000,
            }
        ),
        encoding="utf-8",
    )


def test_cli_defaults_to_preview_and_has_only_explicit_testnet_submit(tmp_path, capsys) -> None:
    service = _Service()
    runner = _Runner(service)
    seen = []

    def build(args, intent, now_ms, environ):
        seen.append((args, intent, now_ms, environ))
        return runner

    code = cli.main(
        _base_args(tmp_path),
        clock_ms=lambda: NOW,
        runner_factory=build,
        environ={},
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output == {
        "command_state": None,
        "reason_codes": [],
        "status": "PREVIEW_READY",
        "submission_mode": "PREVIEW",
    }
    assert service.calls == [(NOW, "testnet-execution-service", False)]
    assert service.enqueued[0][0] == _intent()
    assert seen[0][0].testnet_submit is False
    assert runner.closed is True

    parser = cli.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--testnet-submit" in option_strings
    assert not any("mainnet" in option or "live" in option for option in option_strings)


def test_explicit_flag_is_the_only_path_that_enables_submission(tmp_path, capsys) -> None:
    service = _Service(status="COMPLETED")
    runner = _Runner(service)

    code = cli.main(
        _submit_args(tmp_path),
        clock_ms=lambda: NOW,
        runner_factory=lambda *args: runner,
        environ={},
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["submission_mode"] == "BINANCE_FUTURES_TESTNET"
    assert service.calls == [(NOW, "testnet-execution-service", True)]
    assert runner.closed is True


def test_cli_redacts_factory_errors_and_never_prints_secret_or_path(tmp_path, capsys) -> None:
    secret = "api-secret-never-print"
    sensitive_path = str(tmp_path / "private-key.json")

    def explode(*args):
        raise RuntimeError(f"{secret} at {sensitive_path}")

    code = cli.main(
        _submit_args(tmp_path),
        clock_ms=lambda: NOW,
        runner_factory=explode,
        environ={},
    )

    raw = capsys.readouterr().out
    output = json.loads(raw)
    assert code == 3
    assert output == {
        "error_code": "STARTUP_ERROR_RUNTIMEERROR",
        "status": "ERROR",
        "submission_mode": "BINANCE_FUTURES_TESTNET",
    }
    assert secret not in raw
    assert sensitive_path not in raw


def test_testnet_credentials_are_environment_only_and_missing_is_classified() -> None:
    assert cli.load_testnet_credentials(
        {
            "BINANCE_TESTNET_API_KEY": " key ",
            "BINANCE_TESTNET_API_SECRET": " secret ",
        }
    ) == ("key", "secret")

    with pytest.raises(cli.CliConfigurationError, match="CREDENTIALS_MISSING"):
        cli.load_testnet_credentials({})


def test_submit_requires_runtime_files_but_preview_does_not(tmp_path) -> None:
    preview = cli.build_parser().parse_args(_base_args(tmp_path))
    cli.validate_args(preview)

    submit = cli.build_parser().parse_args(
        [*_base_args(tmp_path), "--testnet-submit"]
    )
    with pytest.raises(cli.CliConfigurationError, match="SUBMIT_RUNTIME_MISSING"):
        cli.validate_args(submit)


def test_production_preview_revalidates_authority_without_credentials(tmp_path, capsys) -> None:
    _write_authority_files(tmp_path)

    code = cli.main(
        _base_args(tmp_path),
        clock_ms=lambda: NOW,
        environ={},
    )

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["status"] == "PREVIEW_READY"
    assert output["command_state"] == "VALIDATED"
    assert output["submission_mode"] == "PREVIEW"


def test_production_submit_runner_constructs_testnet_only_without_network(tmp_path) -> None:
    _write_authority_files(tmp_path)
    _write_runtime(tmp_path)
    args = cli.build_parser().parse_args(_submit_args(tmp_path))
    cli.validate_args(args)

    runner = cli.build_runner(
        args,
        _intent(),
        NOW,
        {
            "BINANCE_TESTNET_API_KEY": "test-key",
            "BINANCE_TESTNET_API_SECRET": "test-secret",
        },
    )
    try:
        assert runner.service._venue.venue_mode == "testnet"
        assert runner.service._venue.venue_name == "binance"
    finally:
        runner.close()


def test_submit_rejects_mandate_above_runtime_policy_before_credentials(tmp_path) -> None:
    _write_authority_files(tmp_path)
    _write_runtime(tmp_path)
    mandate_path = tmp_path / "mandate.json"
    mandate = json.loads(mandate_path.read_text(encoding="utf-8"))
    mandate["max_gross_notional"] = 5_000_000.01
    mandate_path.write_text(json.dumps(mandate), encoding="utf-8")
    args = cli.build_parser().parse_args(_submit_args(tmp_path))
    cli.validate_args(args)

    class CredentialsMustNotBeRead(dict):
        def get(self, key, default=None):
            raise AssertionError(f"credential access preceded mandate policy validation: {key}")

    with pytest.raises(cli.CliConfigurationError, match="MANDATE_EXCEEDS_SYSTEM_POLICY"):
        cli.build_runner(args, _intent(), NOW, CredentialsMustNotBeRead())


def test_authority_provider_revalidates_mandate_policy_after_runner_setup(tmp_path) -> None:
    _write_authority_files(tmp_path)
    _write_runtime(tmp_path)
    args = cli.build_parser().parse_args(_submit_args(tmp_path))
    runtime = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))
    ceiling = cli.derive_testnet_mandate_ceiling(_intent(), runtime)
    provider = cli._authority_provider(args, mandate_ceiling=ceiling)
    mandate_path = tmp_path / "mandate.json"
    mandate = json.loads(mandate_path.read_text(encoding="utf-8"))
    mandate["max_net_notional"] = 3_000_000.01
    mandate_path.write_text(json.dumps(mandate), encoding="utf-8")

    with pytest.raises(cli.CliConfigurationError, match="MANDATE_EXCEEDS_SYSTEM_POLICY"):
        provider(_intent(), NOW)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (
            lambda body: body.update({"market_snapshot_hash": "f" * 64}),
            "RUNTIME_MARKET_SNAPSHOT_MISMATCH",
        ),
        (
            lambda body: body["account"].update({"as_of_ms": NOW - 5_000}),
            "RUNTIME_ACCOUNT_SNAPSHOT_STALE",
        ),
    ),
)
def test_submit_runtime_snapshot_binding_and_account_freshness_fail_closed(
    tmp_path, mutate, reason
) -> None:
    _write_runtime(tmp_path)
    runtime_path = tmp_path / "runtime.json"
    body = json.loads(runtime_path.read_text(encoding="utf-8"))
    mutate(body)
    runtime_path.write_text(json.dumps(body), encoding="utf-8")
    args = cli.build_parser().parse_args(_submit_args(tmp_path))

    with pytest.raises(cli.CliConfigurationError, match=reason):
        cli._prepare_runtime(args, _intent(), NOW)
