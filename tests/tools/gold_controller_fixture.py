"""Host/role mapping for controller pipe tests; leaf custody stays real."""
import os
from types import SimpleNamespace


def map_host_custody(tmp_path, monkeypatch):
    from tools import demo_supervisor as supervisor
    original = supervisor._require_protected
    ancestors = {(p.stat().st_dev, p.stat().st_ino) for p in tmp_path.parents}
    def protected(info, *, directory):
        mode = info.st_mode
        if (info.st_dev, info.st_ino) in ancestors:
            mode &= ~0o022
        return original(SimpleNamespace(st_mode=mode,
            st_uid=0 if info.st_uid == os.getuid() else info.st_uid), directory=directory)
    monkeypatch.setattr(supervisor, '_require_protected', protected)
