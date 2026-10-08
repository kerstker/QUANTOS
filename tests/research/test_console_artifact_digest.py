import hashlib
from services.console.api.state_artifact import read_json_artifact, VALID, INVALID


def test_optional_digest_binds_exact_bytes_of_the_same_validated_read(tmp_path):
    p=tmp_path/'input.json'; raw=b'{ "value" : 1 }\n'; p.write_bytes(raw)
    assert read_json_artifact(p,trusted_root=tmp_path)==(VALID,{'value':1})
    assert read_json_artifact(p,trusted_root=tmp_path,with_digest=True)==(VALID,{
        'document':{'value':1},'input_sha256':hashlib.sha256(raw).hexdigest()})


def test_digest_option_does_not_bypass_link_or_json_validation(tmp_path):
    p=tmp_path/'input.json';p.write_text('{"value":1,"value":2}')
    assert read_json_artifact(p,trusted_root=tmp_path,with_digest=True)==(INVALID,None)
    p.unlink();p.symlink_to(tmp_path/'target.json');(tmp_path/'target.json').write_text('{}')
    assert read_json_artifact(p,trusted_root=tmp_path,with_digest=True)==(INVALID,None)
