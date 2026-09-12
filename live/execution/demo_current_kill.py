"""Fresh protected stop-state reads for ordinary Demo POST preparation/sending.

No creation, legacy JSON import, bootstrap, reset, signer, or caller-selected
path. Each check opens a new read-only original state-store transaction.
"""
from pathlib import Path
import stat

from live.execution import demo_authority
from quantos.contracts.killswitch import KillSwitchState
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore,kill_switch_state_hash

_PATH=Path('/var/lib/quantos-demo-executor/kill-switch.sqlite3')


class DemoKillStateRejected(ValueError):
    pass


def _custody():
    registry=demo_authority.load_registry_for_executor();uid=registry['executor_uid']
    for parent in reversed(_PATH.parent.parents):
        demo_authority._require_protected(parent.lstat(),directory=True)
    root=_PATH.parent.lstat()
    if root.st_uid!=uid or root.st_mode&0o022 or not stat.S_ISDIR(root.st_mode):
        raise DemoKillStateRejected('DEMO_KILL_STATE_DIRECTORY_CUSTODY')
    for path in (_PATH,*(Path(str(_PATH)+suffix) for suffix in ('-wal','-shm','-journal'))):
        if path!=_PATH and not path.exists() and not path.is_symlink():continue
        info=path.lstat()
        if (info.st_uid!=uid or info.st_mode&0o022 or not stat.S_ISREG(info.st_mode) or
            info.st_nlink!=1 or info.st_size>64*1024*1024):
            raise DemoKillStateRejected('DEMO_KILL_STATE_FILE_CUSTODY')
    info=_PATH.lstat()
    return info.st_dev,info.st_ino


def require_expected(expected_state,*,now_ms):
    try:
        if type(expected_state) is not KillSwitchState or type(now_ms) is not int or now_ms<=0:
            raise DemoKillStateRejected('DEMO_KILL_STATE_EXPECTATION')
        identity=_custody()
        store=KillSwitchStateStore(str(_PATH),read_only=True)
        try:snapshot=store.verified_snapshot()
        finally:store.close()
        if _custody()!=identity:raise DemoKillStateRejected('DEMO_KILL_STATE_REPLACED')
        state=snapshot['state']
        if max(snapshot['recorded_at'],state.armed_at,state.reset_at)>now_ms:
            raise DemoKillStateRejected('DEMO_KILL_STATE_FROM_FUTURE')
        state_hash=kill_switch_state_hash(state)
        if state_hash!=kill_switch_state_hash(expected_state):
            raise DemoKillStateRejected('DEMO_KILL_STATE_CHANGED')
        return {'schema':'DEMO_CURRENT_KILL_STATE_V1','state_sha256':state_hash,
                'record_sha256':snapshot['record_hash'],'transition_count':snapshot['transition_count'],
                'latest_recorded_at_ms':snapshot['recorded_at'],'checked_at_ms':now_ms}
    except DemoKillStateRejected:raise
    except Exception:raise DemoKillStateRejected('DEMO_KILL_STATE_UNAVAILABLE') from None
