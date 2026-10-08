"""Install a bound research frontend beside its accepted immutable source.

No systemd changes, activation, credentials or trading configuration. Callers
must supply the reviewed delivery binding; there is no implicit latest release.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.build_arm64_release_package import _verify_packaged_frontend_contract, tree_manifest


def require(condition, reason):
    if not condition: raise ValueError(reason)


def verify(root, binding):
    require(type(binding) is dict and set(binding) == {'artifact_commit','source_anchor','manifest_sha256'},
            'FRONTEND_INSTALL_BINDING')
    for name, length in (('artifact_commit',40),('source_anchor',40),('manifest_sha256',64)):
        require(type(binding[name]) is str and re.fullmatch('[a-f0-9]{'+str(length)+'}',binding[name]) is not None,
                'FRONTEND_INSTALL_IDENTITY')
    root = Path(root).absolute()
    require(root.resolve() == root and root.is_dir(), 'FRONTEND_INSTALL_PATH')
    path = root/'manifest.json'; info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 1024*1024,
            'FRONTEND_INSTALL_MANIFEST_FILE')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == binding['manifest_sha256'], 'FRONTEND_INSTALL_MANIFEST_DIGEST')
    value = decode(raw)
    require(value.get('schema') == 'RESEARCH_FRONTEND_DELIVERY_V1'
        and value.get('release') == dict(commit=binding['artifact_commit'],source_anchor=binding['source_anchor'])
        and all(value.get(k) is False for k in ('installed','services_changed','activation_authorized')),
        'FRONTEND_INSTALL_SCOPE')
    _verify_packaged_frontend_contract(root,value)
    rows,_ = tree_manifest(root/'frontend')
    require(value.get('files') == rows and len(rows) <= 4096, 'FRONTEND_INSTALL_FILE_SET')
    require(sum((root/'frontend'/name).stat().st_size for name in rows) <= 256*1024*1024,
            'FRONTEND_INSTALL_SIZE')
    actual,_ = tree_manifest(root)
    require(set(actual) == {'manifest.json',*('frontend/'+name for name in rows)}, 'FRONTEND_INSTALL_EXTRA_FILES')
    return raw, rows


def sync_directory(path):
    fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def stage(source, destination, binding):
    """Copy into a new private staging directory and verify the copied bytes."""
    source = Path(source).absolute(); destination = Path(destination).absolute()
    raw,rows = verify(source,binding)
    require(destination.parent.resolve() == destination.parent, 'FRONTEND_INSTALL_STAGE_PARENT')
    destination.mkdir(mode=0o700)
    for name,digest in {'manifest.json':binding['manifest_sha256'],**{'frontend/'+k:v for k,v in rows.items()}}.items():
        path=source/name
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=64*1024*1024,
                    'FRONTEND_INSTALL_FILE')
            body=stream.read(64*1024*1024+1)
        require(hashlib.sha256(body).hexdigest()==digest,'FRONTEND_INSTALL_COPY_CHANGED')
        target=destination/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with target.open('xb') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        target.chmod(0o644)
    verify(destination,binding)
    for path in sorted((p for p in destination.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        path.chmod(0o755);sync_directory(path)
    destination.chmod(0o755);sync_directory(destination)
    require(verify(source,binding)[0]==raw,'FRONTEND_INSTALL_SOURCE_CHANGED')


def custody(path, owner):
    for item in (path,*path.rglob('*')):
        info=item.lstat()
        require(info.st_uid==owner and not info.st_mode&0o022 and
            (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink==1),
            'FRONTEND_INSTALL_CUSTODY')


def publish(staged, parent, binding, *, owner=0):
    """Atomic immutable publication; an existing release must match exactly."""
    staged=Path(staged);parent=Path(parent)
    verify(staged,binding);custody(staged,owner)
    target=parent/binding['artifact_commit']
    if os.path.lexists(target):
        custody(target,owner);verify(target,binding)
        return target
    os.rename(staged,target);sync_directory(parent);sync_directory(staged.parent)
    return target


def prepare_parent(parent, *, owner=0):
    parent=Path(parent)
    require(parent.parent.resolve()==parent.parent.absolute(),'FRONTEND_INSTALL_PARENT')
    parent.mkdir(mode=0o755,exist_ok=True)
    info=parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==owner and not info.st_mode&0o022,'FRONTEND_INSTALL_PARENT')
    parent.chmod(0o755,follow_symlinks=False)


def install(source, binding):
    require(os.getresuid()==(0,0,0),'FRONTEND_INSTALL_ROOT_REQUIRED')
    verify(source,binding)
    release=Path('/opt/quantos/releases')/binding['artifact_commit']
    for path in (release,*release.parents):
        info=path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,
                'FRONTEND_INSTALL_RELEASE_CUSTODY')
    from scripts.evidence_binding import assert_release_evidence_manifest,resolve_canonical_source_anchor
    assert_release_evidence_manifest(release,expected_release_commit=binding['artifact_commit'])
    require(resolve_canonical_source_anchor(release)==binding['source_anchor'],'FRONTEND_INSTALL_SOURCE_ANCHOR')
    parent=Path('/opt/quantos/frontends');prepare_parent(parent)
    workspace=Path(tempfile.mkdtemp(prefix='.staging-',dir=parent))
    staged=workspace/'package';stage(source,staged,binding)
    target=publish(staged,parent,binding)
    return dict(status='FRONTEND_INSTALLED_NOT_ACTIVATED',artifact_commit=binding['artifact_commit'],
                target=str(target),services_changed=False,submission_enabled=False)
