"""Bounded capture of curated public research pages; source text is never executable."""
import hashlib
import time
from html.parser import HTMLParser
import urllib.request
from services.research.ai_factory_sources import SOURCES
from tools.btc_factory_research import seal,verified
from tools.cache_io import atomic_write_json


class _Text(HTMLParser):
    def __init__(self):super().__init__();self.skip=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip and data.strip():self.parts.append(data.strip())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def fetch_source(source_id):
    # Only fixed, reviewed HTTPS URLs; no arbitrary URLs, credentials or recursive crawling.
    url=SOURCES[source_id]['url']
    request=urllib.request.Request(url,headers={'User-Agent':'QUANTOS-Research/1.0','Accept':'text/html,text/plain'})
    with urllib.request.build_opener(_NoRedirect()).open(request,timeout=15) as response:
        content=response.read(1048577)
        if len(content)>1048576:raise ValueError('research page exceeds limit')
    return content


def capture_sources(destination,source_ids,*,fetch=fetch_source):
    if not source_ids or len(source_ids)>8 or any(s not in SOURCES for s in source_ids):raise ValueError('unknown source scope')
    if destination.is_symlink():raise ValueError('unsafe source capture path')
    if destination.exists():
        result=verified(destination)
        if result['source_ids']!=source_ids:raise ValueError('source capture identity changed')
        return result
    records=[]
    for source_id in source_ids:
        try:
            content=fetch(source_id)
            if not isinstance(content,bytes) or len(content)>1048576:raise ValueError('invalid public source response')
            parser=_Text();parser.feed(content.decode('utf-8',errors='replace'))
            excerpt=' '.join(parser.parts)[:1600]
            if not excerpt:raise ValueError('empty public source')
            records.append({'source_id':source_id,'status':'CAPTURED_PUBLIC_TEXT','content_sha256':hashlib.sha256(content).hexdigest(),'excerpt':excerpt})
        except Exception as exc:
            records.append({'source_id':source_id,'status':'UNAVAILABLE','reason':type(exc).__name__})
    document=seal({'source_ids':source_ids,'records':records,'scope':'CURRENT_PUBLIC_METHOD_NOT_HISTORICAL_PIT','captured_at_ms':time.time_ns()//1000000,
                   'authoritative':False,'economic_eligible':False})
    destination.parent.mkdir(parents=True,exist_ok=True);atomic_write_json(str(destination),document)
    return document
