import {afterEach,expect,it,vi} from 'vitest';
import {assertWorkspace} from './research-workspace';
const current={id:'current',label:'当前研究',writable:true};
const frozen={id:'frozen',label:'冻结观察',writable:false};
const valid={workspace:frozen,workspaces:[current,frozen]};
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs();});

it('rejects a default-root response to a different workspace request',()=>{
 expect(()=>assertWorkspace({...valid,workspace:current},'frozen')).toThrow('研究空间不匹配');
 expect(()=>assertWorkspace({...valid,workspace:{...frozen,writable:true}},'frozen')).toThrow();
 expect(()=>assertWorkspace({...valid,workspaces:[frozen,frozen]},'frozen')).toThrow();
 expect(()=>assertWorkspace(valid,'frozen')).not.toThrow();
});
it('never sends research mutations for read-only workspaces',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 const {controlFactory,importProfitabilityInput}=await import('./ai-factory-api');
 const {controlCampaign}=await import('./ai-factory-lifecycle-api');
 await expect(controlFactory({action:'pause',attempt:4,workspace:'frozen'},'case')).rejects.toThrow('仅供查看');
 await expect(controlCampaign({action:'stop',campaign_id:4,workspace:'frozen'})).rejects.toThrow('仅供查看');
 await expect(importProfitabilityInput(4,'basis','{}','case','frozen')).rejects.toThrow('仅供查看');
 expect(fetch).not.toHaveBeenCalled();
});
it('transports the selected ID and rejects an old backend that ignores it',async()=>{
 vi.resetModules();vi.stubEnv('VITE_API_URL','http://127.0.0.1:8400');
 const body={read_only:true,authoritative:false,trials:[],sessions:[],provider:{},campaigns:[],models:[],sources:[],...valid};
 const fetch=vi.fn(async()=>({ok:true,json:async()=>body}));vi.stubGlobal('fetch',fetch);
 const {fetchFactory}=await import('./ai-factory-api');
 const {fetchLifecycle}=await import('./ai-factory-lifecycle-api');
 const signal=new AbortController().signal;
 await fetchFactory(4,signal,'frozen');await fetchLifecycle(signal,'frozen');
 for(const [url] of fetch.mock.calls as unknown as [URL][])expect(url.searchParams.get('workspace')).toBe('frozen');
 body.workspace=current;
 await expect(fetchFactory(4,signal,'frozen')).rejects.toThrow('研究空间不匹配');
 await expect(fetchLifecycle(signal,'frozen')).rejects.toThrow('研究空间不匹配');
});
