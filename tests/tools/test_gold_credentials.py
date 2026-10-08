import json
import os
import subprocess
import pytest
from tools.gold_credentials import save_credentials, get_credentials, main

KEY='K'*32
SECRET='S'*64


def test_private_file_roundtrip_and_permissions(tmp_path):
    root=tmp_path/'vault'
    save_credentials(KEY,SECRET,root=root)
    assert get_credentials(root=root)==(KEY,SECRET)
    assert root.stat().st_mode & 0o777==0o700
    assert all(p.stat().st_mode & 0o777==0o600 for p in root.iterdir())


def test_missing_profile_prompts_and_explicit_prompt_ignores_storage(tmp_path):
    calls=[]
    def prompt():calls.append(1);return KEY,SECRET
    assert get_credentials(root=tmp_path/'missing',prompt=prompt)==(KEY,SECRET)
    assert not (tmp_path/'missing').exists()
    assert get_credentials(root=tmp_path,source='prompt',prompt=prompt)==(KEY,SECRET)


@pytest.mark.parametrize('mode',['symlink','permissions','malformed'])
def test_private_file_rejects_unsafe_source(tmp_path,mode):
    root=tmp_path/'vault';save_credentials(KEY,SECRET,root=root)
    path=root/'credentials.json'
    if mode=='symlink':
        path.rename(root/'other.json');path.symlink_to(root/'other.json')
    elif mode=='permissions':path.chmod(0o644)
    else:path.write_text('{"api_key":"bad"}')
    with pytest.raises(ValueError):get_credentials(root=root)


def test_default_check_never_reads_credentials(capsys,tmp_path,monkeypatch):
    monkeypatch.setattr('tools.gold_credentials.get_credentials',lambda **kw:pytest.fail('secret access'))
    assert main([])==0
    assert json.loads(capsys.readouterr().out)['credential_access'] is False


def test_invalid_existing_credentials_do_not_silently_prompt(tmp_path):
    root=tmp_path/'vault';save_credentials(KEY,SECRET,root=root)
    (root/'credentials.json').write_text('{}')
    def forbidden():raise AssertionError('silent fallback')
    with pytest.raises(ValueError):get_credentials(root=root,prompt=forbidden)


def test_existing_store_not_overwritten_without_explicit_replace(tmp_path):
    root=tmp_path/'vault';save_credentials(KEY,SECRET,root=root)
    with pytest.raises(ValueError):save_credentials('A'*32,'B'*32,root=root)
    assert get_credentials(root=root)==(KEY,SECRET)
    save_credentials('A'*32,'B'*32,root=root,replace=True)
    assert get_credentials(root=root)==('A'*32,'B'*32)


def test_operator_observation_uses_saved_credentials_without_prompt(tmp_path,monkeypatch,capsys):
    import tools.gold_credentials as credentials
    import tools.gold_account_observe as observe
    root=tmp_path/'vault';save_credentials(KEY,SECRET,root=root)
    monkeypatch.setattr(credentials,'DEFAULT_ROOT',root)
    monkeypatch.setattr(credentials,'prompt_credentials',lambda:pytest.fail('asked again'))
    monkeypatch.setattr(observe.sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(observe.sys.stderr,'isatty',lambda:True)
    def collect(destination,**kwargs):
        assert kwargs['api_key']==KEY and kwargs['api_secret']==SECRET
        return {'status':'STOPPED','reason':'DURATION_COMPLETE'}
    monkeypatch.setattr(observe,'observe',collect)
    assert observe.main(['--operator-observe','--output-dir',str(tmp_path/'out')])==0
    output=capsys.readouterr().out
    assert KEY not in output and SECRET not in output


def test_hardlinked_private_file_rejected(tmp_path):
    root=tmp_path/'vault';save_credentials(KEY,SECRET,root=root)
    os.link(root/'credentials.json',root/'other.json')
    with pytest.raises(ValueError):get_credentials(root=root)


def test_operator_store_outputs_only_safe_summary(tmp_path,monkeypatch,capsys):
    import tools.gold_credentials as credentials
    monkeypatch.setattr(credentials,'DEFAULT_ROOT',tmp_path/'vault')
    monkeypatch.setattr(credentials.sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(credentials.sys.stderr,'isatty',lambda:True)
    monkeypatch.setattr(credentials,'prompt_credentials',lambda:(KEY,SECRET))
    assert credentials.main(['--operator-store'])==0
    output=capsys.readouterr().out
    assert KEY not in output and SECRET not in output
    assert json.loads(output)['status']=='CREDENTIALS_SAVED'
    assert credentials.main(['--operator-store'])==2
    assert json.loads(capsys.readouterr().out)['reason']=='CREDENTIALS_ALREADY_SAVED'
