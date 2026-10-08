"""The actual issuer role rereads fixed public ENABLE before native signing."""

import io
import json
import os
from types import SimpleNamespace

import pytest

from live.execution import demo_authority
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_store import IssuerStore
from tests.live.test_demo_gold_delegated_issuer import native_issuer as native_issuer
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tools import demo_issuer as cli

_ROOT_READER = demo_authority._read_root_owned_json
_PROTECTED = demo_authority._require_protected


class Channel(io.StringIO):
    def fileno(self):
        return 1


@pytest.fixture
def native_cli(native_issuer, tmp_path, monkeypatch):
    from live.execution import demo_gold_delegation_enable as enabling

    f = native_issuer
    public = tmp_path / "public-enable"
    public.mkdir(mode=0o755)
    complete = dict(
        schema="DEMO_GOLD_DELEGATION_ENABLE_COMPLETE_V1",
        enable_sha256=_digest(f.enable),
        consumer_enabled=True,
        submission_enabled=False,
        economic_authority=False,
    )
    for name, value in (("enable.json", f.enable), ("complete.json", complete)):
        target = public / name
        target.write_text(json.dumps(value))
        target.chmod(0o444)
    monkeypatch.setattr(enabling, "PUBLIC_ROOT", public)
    original_metadata = enabling._public_metadata

    def mapped(info):
        return SimpleNamespace(
            **{name: getattr(info, name) for name in ("st_mode", "st_nlink", "st_size")},
            st_uid=0 if info.st_uid == os.getuid() else info.st_uid,
            st_gid=0 if info.st_gid == os.getgid() else info.st_gid,
        )

    # Only synthetic owner IDs are mapped. Native file type, links, modes,
    # exact bytes, re-read, lock and directory inode checks still execute.
    monkeypatch.setattr(enabling, "_public_metadata", lambda info, **kw: original_metadata(mapped(info), **kw))
    ancestors = {(parent.stat().st_dev, parent.stat().st_ino) for parent in public.parents}

    def protected(info, *, directory):
        synthetic = mapped(info)
        if directory and (info.st_dev, info.st_ino) in ancestors:
            # This checkout's development ancestors are not installed custody.
            # Only their existing inode identities get a virtual protected mode.
            synthetic.st_mode &= ~0o022
        return _PROTECTED(synthetic, directory=directory)

    monkeypatch.setattr(demo_authority, "_require_protected", protected)
    monkeypatch.setattr(enabling, "_read_root_owned_json", _ROOT_READER)
    uid = f.registry["issuer_uid"]
    monkeypatch.setattr(os, "geteuid", lambda: uid)
    monkeypatch.setattr(os, "getegid", lambda: uid)
    monkeypatch.setattr(os, "getgroups", lambda: [uid])
    monkeypatch.setattr(cli, "configuration", lambda _: (tmp_path, f.registry, f.base_policy))
    monkeypatch.setattr(cli, "private_pipe", lambda _: None)
    monkeypatch.setattr(cli, "read_frame", lambda *a, **kw: json.dumps(f.handoff).encode())
    monkeypatch.setattr(cli, "now_ms", lambda: f.now_ms)
    monkeypatch.setattr(cli, "_result", lambda root, registry, value: cli._retain(root / "results", value))
    keys = []

    def load_key(registry, policy):
        assert registry == f.registry and policy == f.base_policy
        keys.append(1)
        return f.private

    monkeypatch.setattr(cli, "load_key", load_key)
    f.public = public
    f.keys = keys
    f.cli_root = tmp_path
    f.enabling = enabling
    assert (
        enabling.load_enabled(
            registry=f.registry,
            source_commit="c" * 40,
            account_ref_sha256=f.base_policy["account_ref_sha256"],
            now_ms=f.now_ms,
            role="issuer",
        )
        == f.enable
    )
    return f


def test_real_issuer_role_public_loader_and_native_signature(native_cli):
    f = native_cli
    output = Channel()
    assert cli.run(source_commit="c" * 40, input_fd=123, output=output) == 0, output.getvalue()
    grant = json.loads(output.getvalue())
    assert grant["schema"] == "DEMO_EXECUTION_SESSION_GRANT_V1"
    assert grant["proposal_sha256"] == f.handoff["proposal_sha256"]
    with __import__("contextlib").closing(IssuerStore(f.ledger)) as log:
        assert [row["kind"] for row in log.snapshot()] == ["RESERVED", "RESERVED", "SIGNED"]
    assert f.keys == [1]


@pytest.mark.parametrize("damage", ["missing_enable", "partial", "wrong_role", "changed_proof"])
def test_unavailable_or_invalid_public_proof_never_opens_signer(native_cli, monkeypatch, damage):
    f = native_cli
    output = Channel()
    if damage == "missing_enable":
        (f.public / "enable.json").unlink()
    elif damage == "partial":
        (f.public / "complete.json").unlink()
    elif damage == "wrong_role":
        monkeypatch.setattr(os, "getgroups", lambda: [f.registry["issuer_uid"], f.registry["executor_uid"]])
    else:
        f.handoff["proposal"]["delegated_admission"]["result"]["status"] = "FORGED"
        f.handoff["proposal"]["context"]["delegated_admission_sha256"] = _digest(f.proof)
        f.handoff["proposal_sha256"] = _digest(f.handoff["proposal"])
    assert cli.run(source_commit="c" * 40, input_fd=123, output=output) == 3
    report = json.loads(output.getvalue())
    assert report["status"] == "NOT_ISSUED" and report["phase"] == "POLICY_REVIEW"
    assert f.keys == []
    with __import__("contextlib").closing(IssuerStore(f.ledger)) as log:
        assert log.snapshot() == f.history


@pytest.mark.parametrize("after_sign", [False, True])
def test_real_public_revocation_during_refresh_retains_native_consumption(native_cli, monkeypatch, after_sign):
    from live.execution import demo_issuer as issuer

    f = native_cli
    output = Channel()
    if after_sign:
        native = issuer._sign

        def sign(*args):
            value = native(*args)
            (f.public / "complete.json").unlink()
            return value

        monkeypatch.setattr(issuer, "_sign", sign)
    else:
        native = IssuerStore.reserve

        def reserve(self, *args, **kwargs):
            result = native(self, *args, **kwargs)
            (f.public / "complete.json").unlink()
            return result

        monkeypatch.setattr(IssuerStore, "reserve", reserve)
    assert cli.run(source_commit="c" * 40, input_fd=123, output=output) == 3
    report = json.loads(output.getvalue())
    assert report["status"] == "GRANT_DELIVERY_UNCONFIRMED"
    with __import__("contextlib").closing(IssuerStore(f.ledger)) as log:
        assert [row["kind"] for row in log.snapshot()] == (
            ["RESERVED", "RESERVED", "SIGNED"] if after_sign else ["RESERVED", "RESERVED"]
        )
    assert f.keys == [1] and "envelope" not in report
