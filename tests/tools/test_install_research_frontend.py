import hashlib
import json
import os

import pytest

from tools import build_arm64_release_package as package


def delivery(tmp_path):
    root = tmp_path/'package'; frontend = root/'frontend'; frontend.mkdir(parents=True)
    (frontend/'index.html').write_text('<!doctype html>')
    (frontend/package.FRONTEND_BUILD_CONTRACT).write_text(json.dumps(dict(
        schema=package.FRONTEND_BUILD_SCHEMA, release_commit='a'*40, frontend_source_sha256='b'*64,
        api_url=package.SAME_ORIGIN_MARKER, runner_url=package.SAME_ORIGIN_MARKER, mock_mode='false')))
    (frontend/package.FRONTEND_RUNTIME_CONTRACT).write_text(json.dumps(package._frontend_runtime_contract('a'*40)))
    rows, digest = package.tree_manifest(frontend)
    manifest = dict(schema='RESEARCH_FRONTEND_DELIVERY_V1', release=dict(commit='a'*40,source_anchor='c'*40),
        frontend=dict(source_sha256='b'*64,tree_sha256=digest,file_count=len(rows),runtime_contract=package.FRONTEND_RUNTIME_CONTRACT),
        files=rows,installed=False,services_changed=False,activation_authorized=False)
    raw = json.dumps(manifest).encode();(root/'manifest.json').write_bytes(raw)
    return root, dict(artifact_commit='a'*40,source_anchor='c'*40,manifest_sha256=hashlib.sha256(raw).hexdigest())


def test_frontend_stage_and_idempotent_publication(tmp_path):
    from tools import install_research_frontend as installer
    source, binding = delivery(tmp_path)
    stage = tmp_path/'stage'; installer.stage(source, stage, binding)
    parent = tmp_path/'installed';parent.mkdir()
    target = installer.publish(stage,parent,binding,owner=os.getuid())
    assert target == parent/('a'*40)
    assert package.tree_manifest(target/'frontend') == package.tree_manifest(source/'frontend')
    assert not stage.exists()
    again = tmp_path/'again';installer.stage(source,again,binding)
    assert installer.publish(again,parent,binding,owner=os.getuid()) == target
    before = (target/'frontend/index.html').read_bytes()
    (again/'frontend/index.html').write_text('changed')
    with pytest.raises((ValueError, package.ReleasePackageError)):installer.publish(again,parent,binding,owner=os.getuid())
    assert (target/'frontend/index.html').read_bytes() == before


@pytest.mark.parametrize('change', ['file','manifest','release','symlink'])
def test_wrong_or_changed_frontend_never_publishes(tmp_path,change):
    from tools import install_research_frontend as installer
    source,binding = delivery(tmp_path)
    if change=='file':(source/'frontend/index.html').write_text('changed')
    if change=='manifest':binding['manifest_sha256']='d'*64
    if change=='release':binding['artifact_commit']='d'*40
    if change=='symlink':(source/'frontend/alias').symlink_to(source/'frontend/index.html')
    target=tmp_path/'stage'
    with pytest.raises((ValueError, package.ReleasePackageError)):installer.stage(source,target,binding)
    assert not target.exists()


def test_public_parent_traversable_under_private_umask(tmp_path):
    from tools import install_research_frontend as installer
    import stat
    parent = tmp_path / 'frontends'
    previous = os.umask(0o077)
    try:
        installer.prepare_parent(parent, owner=os.getuid())
        assert stat.S_IMODE(parent.stat().st_mode) == 0o755
        private = parent / '.staging-private'
        private.mkdir(mode=0o700)
        installer.prepare_parent(parent, owner=os.getuid())
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
    finally:
        os.umask(previous)
