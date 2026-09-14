"""Successor activation preserves stores and can finish an interrupted install."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from tools import demo_successor_setup as tool, demo_operator_setup as setup
from services.validation.demo_submission import registry_sha256
from tests.tools.test_demo_successor_setup import fixture
from tests.tools.test_demo_operator_setup import stop_bytes
from tests.tools.test_binance_demo_execution_origin import NOW


def prepared(tmp_path,monkeypatch):
    origin,registry,private,context=fixture(tmp_path)
    spec=setup.propose(origin=origin,registry=registry,source_commit='c'*40,now_ms=NOW+60000,position_context=context)
    approval=dict(schema='DEMO_OPERATOR_ENROLLMENT_V1',spec_sha256=setup.digest(spec),operator_id='isolated-operator',
        approved_at_ms=NOW+60000,purpose=setup.PURPOSE)
    stop=stop_bytes(tmp_path)
    manifest,files=setup.assemble(spec=spec,approval=approval,origin=origin,registry=registry,private_key=private,
        stop_image=stop,now_ms=NOW+60000,position_context=context)
    materials=tool.installer.validate_materials(manifest=manifest,files=files,existing_registry=registry,
        source_commit='c'*40,account_ref=origin['account_ref_sha256'],now_ms=NOW+60000)
    base=tmp_path/'protected';base.mkdir()
    for name in ('CONFIG','EXECUTOR','ISSUER','CONTROLLER'):
        path=base/name;path.mkdir();monkeypatch.setattr(tool.installer,name,path)
    (tool.installer.CONTROLLER/'tasks').mkdir()
    root=base/'setup';root.mkdir();monkeypatch.setattr(tool,'SETUP',root)
    stage=root/'cycles'/setup.digest(approval);stage.mkdir(parents=True)
    (stage/'result.json').write_bytes(setup.canonical(dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1',
        status='INSTALLED_PENDING_CYCLE',enrollment_sha256=setup.digest(approval),spec_sha256=setup.digest(spec),
        source_commit='c'*40,task_sha256=manifest['tasks'][0],orders_submitted=False,economic_authority=False)))
    old_policy=deepcopy(materials['policy']);old=registry['keys'][0]
    old_policy.update(policy_id='demo-old-policy',registry_sha256=registry_sha256(registry),
        authority_id=old['authority_id'],key_id=old['key_id'],valid_from_ms=old['valid_from_ms'],valid_until_ms=old['valid_until_ms'])
    for name,body in (('authority.json',setup.canonical(registry)),('policy.json',setup.canonical(old_policy)),
                      ('profile.json',b'{"old":"profile"}'),('signing-key.json',b'{"old":"isolated signing fixture"}')):
        (tool.installer.CONFIG/name).write_bytes(body)
    registry_path=base/'registry.json';registry_path.write_bytes(setup.canonical(registry))
    monkeypatch.setattr(tool.installer,'REGISTRY',registry_path)
    monkeypatch.setattr(tool,'_read_root_owned_json',lambda path:Path(path).read_bytes())
    monkeypatch.setattr(tool.installer,'now_ms',lambda:NOW+60000)
    monkeypatch.setattr(setup,'_stop_image',lambda **kw:stop)
    def write(path,body,**kw):
        with Path(path).open('xb') as f:f.write(body)
    monkeypatch.setattr(tool.installer,'write_new',write)
    protected={tool.installer.EXECUTOR/name:b'existing immutable isolated '+name.encode() for name in
        ('dispatch.sqlite3','oms.sqlite3','recovery.sqlite3','kill-switch.sqlite3','cancellations.sqlite3')}
    protected[tool.installer.ISSUER/'issuance.sqlite3']=b'prior grant counts and policy budgets'
    for path,body in protected.items():path.write_bytes(body)
    return materials,files,setup.canonical(registry),stage,protected


def test_activation_is_last_and_retry_keeps_preimages_orders_kill_and_issuance(tmp_path,monkeypatch):
    materials,files,preimage,stage,protected=prepared(tmp_path,monkeypatch)
    replace=tool._replace;calls=[]
    def crash(path,body,**kw):
        calls.append(path)
        if len(calls)==3:raise OSError('isolated activation interruption')
        assert tool.installer.REGISTRY.read_bytes()==preimage
        replace(path,body,**kw)
    monkeypatch.setattr(tool,'_replace',crash)
    with pytest.raises(OSError):tool.install_successor(materials,files,registry_preimage=preimage,stage=stage)
    originals={p.name:p.read_bytes() for p in (stage/'previous-files').iterdir()}
    assert tool.installer.REGISTRY.read_bytes()==preimage
    monkeypatch.setattr(tool,'_replace',replace)
    tool.install_successor(materials,files,registry_preimage=preimage,stage=stage)
    assert tool.installer.REGISTRY.read_bytes()==setup.canonical(materials['registry'])
    assert tool.active_setup()==stage
    assert {p.name:p.read_bytes() for p in (stage/'previous-files').iterdir()}==originals
    assert all(path.read_bytes()==body for path,body in protected.items())
    assert len(list((tool.installer.CONTROLLER/'tasks').iterdir()))==1
    tool.install_successor(materials,files,registry_preimage=preimage,stage=stage)
    assert all(path.read_bytes()==body for path,body in protected.items())


@pytest.mark.parametrize('damage',['active_prior','registry_changed','kill_changed'])
def test_changed_custody_inputs_stop_before_any_activation(tmp_path,monkeypatch,damage):
    materials,files,preimage,stage,protected=prepared(tmp_path,monkeypatch)
    if damage=='active_prior':
        old=json.loads(preimage);old['keys'][0]['valid_until_ms']=NOW+90000;preimage=setup.canonical(old)
    elif damage=='registry_changed':tool.installer.REGISTRY.write_bytes(b'changed registry')
    else:monkeypatch.setattr(setup,'_stop_image',lambda **kw:b'changed stop state')
    before={p:p.read_bytes() for p in tool.installer.CONFIG.iterdir()}
    with pytest.raises(ValueError):tool.install_successor(materials,files,registry_preimage=preimage,stage=stage)
    assert all(p.read_bytes()==v for p,v in before.items()) and not (tool.SETUP/'active.json').exists()
    assert all(path.read_bytes()==body for path,body in protected.items())
