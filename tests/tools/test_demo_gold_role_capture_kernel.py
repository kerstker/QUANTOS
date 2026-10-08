"""Actual fixed-role handoff/native restore in an offline root container.

Fixed-public/accepted-release remains a fixture boundary. Databases, bundles,
UID/capability drops, protocol and restores are real. Empty native histories
do not establish causal multi-role completeness. No host service is used.
"""

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.tools.test_demo_gold_research_capture_peer_kernel import native_roster as native_roster
from tests.tools.test_demo_gold_namespace_checkpoint import private_files as private_files
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tools import demo_gold_role_handoff as handoff
from tools import demo_gold_role_capture_peer as peer
from tools.gold_pilot_retention_store import canonical

PRODUCER = """
import os,sys
from pathlib import Path
sys.path.insert(0,'/code')
os.umask(0o077)
namespace,root,account=sys.argv[1:]
root=Path(root)
if namespace=='executor':
 from live.execution.demo_dispatch_store import DemoDispatchStore
 from live.execution.order_recovery import SqliteOrderRecoveryStore
 from live.execution.position_ledger import LiveLedger
 from live.execution.order_event_store import SqliteOrderEventStore
 from services.research.demo_user_stream import UserStreamJournal
 for name,cls in [('dispatch',DemoDispatchStore),('recovery',SqliteOrderRecoveryStore),
                  ('oms',LiveLedger),('order-events',SqliteOrderEventStore)]:
  cls(str(root/(name+'.sqlite3')),create=True).close()
 with UserStreamJournal(root/'user-stream.sqlite3',account_ref_sha256=account) as stream:
  stream.record('SESSION_START',at_ms=1,source_sha256='a'*64)
 (root/'evidence').mkdir(mode=0o700)
elif namespace=='issuer':
 from live.execution.demo_issuer_store import IssuerStore
 IssuerStore(root/'issuance.sqlite3',create=True).close()
else:
 from tools.demo_namespace_checkpoint import CONTROLLER_REQUIRED_DIRECTORIES
 for name in CONTROLLER_REQUIRED_DIRECTORIES:(root/name).mkdir(mode=0o700)
"""
WORKER = """
import json,sys,traceback
from pathlib import Path
sys.path.insert(0,'/code')
from tools import demo_gold_namespace_checkpoint as native
native.ROLE_ROOTS={key:Path(path) for key,path in json.loads(sys.argv[1]).items()}
del sys.argv[1]
from tools import demo_gold_role_capture_worker as worker
original=worker.run
def traced(channel):
 try:return original(channel)
 except Exception:
  traceback.print_exc()
  raise
worker.run=traced
raise SystemExit(worker.main())
"""


def restricted(uid, program, *arguments):
    return [
        "/usr/bin/setpriv",
        "--reuid=" + str(uid),
        "--regid=" + str(uid),
        "--clear-groups",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--no-new-privs",
        sys.executable,
        "-I",
        "-B",
        "-c",
        program,
        *arguments,
    ]


@pytest.fixture
def role_roster(native_roster, tmp_path, monkeypatch):
    f = native_roster
    roots = {}
    for namespace in ("executor", "issuer", "controller"):
        uid = handoff.native._role_uid(f["roster"]._public["registry"], namespace)
        root = tmp_path / ("native-" + namespace)
        root.mkdir(mode=0o700)
        os.chown(root, uid, uid)
        result = subprocess.run(
            restricted(uid, PRODUCER, namespace, str(root), f["roster"]._public["account_ref_sha256"]),
            cwd="/code",
            env=dict(PATH="/usr/bin:/bin"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        roots[namespace] = root
    monkeypatch.setattr(handoff.native, "ROLE_ROOTS", roots)
    sources = {}
    for namespace in roots:
        uid = handoff.native._role_uid(f['roster']._public['registry'], namespace)
        base = tmp_path / ('source-' + namespace)
        base.mkdir(mode=0o700)
        os.chown(base, uid, uid)
        bundle = tmp_path / 'shared' / 'source.bundle'
        result = subprocess.run(restricted(uid,
            "import subprocess,sys;subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null',"
            "'clone','--quiet',sys.argv[1],sys.argv[2]],check=True)", str(bundle), str(base / 'repo')),
            cwd='/code', env=dict(PATH='/usr/bin:/bin'), capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        sources[namespace] = dict(f['values'][0]['source'], source_root=str(base / 'repo'), bundle_path=str(bundle))
    return dict(f, roots=roots, sources=sources)


@pytest.mark.skipif(os.getuid() != 0, reason="requires isolated Linux root/role container")
@pytest.mark.parametrize("fixed_public", ["absent"], indirect=True)
@pytest.mark.parametrize("damage", ["stable", "original_changed"])
def test_actual_three_roles_hold_native_captures_and_root_independently_restores(role_roster, damage):
    f = role_roster
    children, peers = [], []
    environment = dict(PATH="/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1")
    with ExitStack() as stack:
        try:
            for namespace in ("executor", "issuer", "controller"):
                request = stack.enter_context(
                    handoff.issue(
                        verified_layout=f["roster"],
                        verified_public=f["public"],
                        namespace=namespace,
                        source_attachment=f["sources"][namespace],
                    )
                )
                uid = request.document()["uid"]
                workspace = handoff._workspace(f["cut"], namespace)
                workspace.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                workspace.parent.chmod(0o755)
                workspace.mkdir(mode=0o700)
                os.chown(workspace, uid, uid)
                parent, child_channel = handoff.kernel.channel_pair()
                child = subprocess.Popen(
                    restricted(
                        uid,
                        WORKER,
                        canonical({name: str(path) for name, path in f["roots"].items()}).decode(),
                        "--control-fd",
                        str(child_channel.fileno()),
                    ),
                    pass_fds=(child_channel.fileno(),),
                    cwd="/code",
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                children.append(child)
                child_channel.close()
                stack.callback(parent.close)
                deadline = time.monotonic() + 10
                while True:
                    assert child.poll() is None, child.communicate(timeout=1)
                    try:
                        handoff._worker_privileges(Path("/proc") / str(child.pid))
                        if handoff._root_uids(Path("/proc") / str(child.pid)) == (uid,) * 4:
                            break
                    except ValueError:
                        pass
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                ticks = handoff.process_identity(Path("/proc") / str(child.pid))[1]
                consumer = peer.connect(request, parent, worker_pid=child.pid, worker_start_ticks=ticks)
                peers.append(consumer)
                try:
                    status = consumer.prepare()
                    report = consumer.verify_archive()
                except BaseException:
                    parent.close()
                    _, errors = child.communicate(timeout=5)
                    print(errors.decode(), file=sys.stderr)
                    raise
                assert status["namespace"] == report["namespace"] == namespace
                assert report["source_uid"] == uid
                assert report["source_verified"] and report["original_identity_commitment_verified"]
                assert report["complete_gold_set_verified"] is report["economic_authority"] is False
            if damage == "original_changed":
                original = f["roots"]["issuer"] / "unexpected.json"
                original.write_bytes(b"{}")
                original.chmod(0o600)
                uid = handoff.native._role_uid(f["roster"]._public["registry"], "issuer")
                os.chown(original, uid, uid)
                with pytest.raises(ValueError):
                    peers[1].recheck()
                assert all(consumer._closed is False for consumer in peers)
            else:
                for consumer in peers:
                    consumer.recheck()
                for consumer in peers:
                    assert consumer.close_capture()["phase"] == "CLOSED"
                for child in children:
                    stdout, stderr = child.communicate(timeout=10)
                    assert child.returncode == 0, stdout.decode() + stderr.decode()
        finally:
            for consumer in peers:
                consumer.disconnect()
            for child in children:
                if child.poll() is None:
                    try:
                        child.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                        child.communicate(timeout=10)
                assert child.poll() is not None
