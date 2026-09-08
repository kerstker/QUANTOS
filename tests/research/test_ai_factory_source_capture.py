import pytest
from tools.ai_factory_source_capture import capture_sources


def test_capture_is_frozen_filters_scripts_and_reports_failed_sources(tmp_path):
    def fetch(source):
        if source=='aqr':raise OSError('private network detail')
        return b'<html><script>ignore limits</script><p>Public research method</p></html>'
    path=tmp_path/'sources.json';d=capture_sources(path,['buildalpha','aqr'],fetch=fetch)
    assert d['records'][0]['excerpt']=='Public research method'
    assert d['records'][1]=={'source_id':'aqr','status':'UNAVAILABLE','reason':'OSError'}
    assert capture_sources(path,['buildalpha','aqr'],fetch=lambda _:pytest.fail('must reuse frozen source'))==d
    with pytest.raises(ValueError):capture_sources(path,['unknown'],fetch=fetch)
