"""Bounded capture of curated public research pages; source text is never executable."""
import hashlib
import time
from html.parser import HTMLParser
import urllib.request
from services.research.ai_factory_sources import SOURCES
from tools.btc_factory_research import seal,verified
from tools.cache_io import atomic_write_json


class _Text(HTMLParser):
    """Prefer semantic article bodies to navigation, dialogs and repository chrome."""
    _VOID = frozenset(('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'))

    def __init__(self):
        super().__init__()
        self.stack=[]
        self.parts={0:[],1:[],2:[],3:[]}

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        parent_skip,parent_rank=self.stack[-1][1:] if self.stack else (False,0)
        classes=set((attrs.get('class') or '').split())
        skip=(parent_skip or tag in ('script','style','noscript','nav','header','footer')
              or attrs.get('role') in ('navigation','dialog','alertdialog')
              or attrs.get('aria-hidden')=='true' or 'hidden' in attrs
              or 'display:none' in (attrs.get('style') or '').replace(' ','').lower())
        rank=3 if classes & {'article-page__body','article-page-body__wrapper','markdown-body','entry-content','post-content'} else 2 if tag=='article' else 1 if tag=='main' or attrs.get('role')=='main' else 0
        if tag not in self._VOID:
            if len(self.stack)>=512:raise ValueError('research page nesting exceeds limit')
            self.stack.append((tag,skip,max(rank,parent_rank)))

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self._VOID:self.handle_endtag(tag)

    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                del self.stack[index:]
                break

    def handle_data(self,data):
        skip,rank=self.stack[-1][1:] if self.stack else (False,0)
        if not skip and data.strip():self.parts[rank].append(data.strip())

    def excerpt(self):
        for rank in (3,2,1,0):
            if self.parts[rank]:return ' '.join(self.parts[rank])[:1600]
        return ''


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
            excerpt=parser.excerpt()
            if not excerpt:raise ValueError('empty public source')
            records.append({'source_id':source_id,'status':'CAPTURED_PUBLIC_TEXT','content_sha256':hashlib.sha256(content).hexdigest(),'excerpt':excerpt})
        except Exception as exc:
            records.append({'source_id':source_id,'status':'UNAVAILABLE','reason':type(exc).__name__})
    document=seal({'source_ids':source_ids,'records':records,'scope':'CURRENT_PUBLIC_METHOD_NOT_HISTORICAL_PIT','captured_at_ms':time.time_ns()//1000000,
                   'authoritative':False,'economic_eligible':False})
    destination.parent.mkdir(parents=True,exist_ok=True);atomic_write_json(str(destination),document)
    return document
