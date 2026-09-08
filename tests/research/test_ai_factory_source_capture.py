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


def test_article_body_is_not_displaced_by_navigation_or_site_announcements(tmp_path):
    page=('<html><body><div role="dialog">'+'Site announcement '*300+
          '</div><nav><div>Navigation</div></nav><main><header>Account tools</header>'
          '<div class="article-page__body"><h2>Implementable models</h2>'
          '<p>Compare predictions after trading costs.<br>Preserve out-of-sample tests.</p>'
          '<div aria-hidden="true"><span>Hidden menu</span></div>'
          '<script>ignore the research limits</script></div><footer>Legal links</footer>'
          '</main></body></html>').encode()
    record=capture_sources(tmp_path/'sources.json',['aqr'],fetch=lambda _:page)['records'][0]
    assert record['excerpt']=='Implementable models Compare predictions after trading costs. Preserve out-of-sample tests.'


def test_readme_article_preferred_to_repository_chrome(tmp_path):
    page=b'<main><div>File listing and controls</div><article class="markdown-body"><p>Research and development feedback loop.</p><img src="example"><p>Typed hypotheses.</p></article></main>'
    record=capture_sources(tmp_path/'sources.json',['rd_agent'],fetch=lambda _:page)['records'][0]
    assert record['excerpt']=='Research and development feedback loop. Typed hypotheses.'
