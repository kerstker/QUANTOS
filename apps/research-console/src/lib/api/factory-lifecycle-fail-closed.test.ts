import {afterEach,expect,it,vi} from 'vitest';
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs();sessionStorage.clear();});

it('refuses a campaign mutation when the research runner is not configured',async()=>{
 vi.resetModules();vi.stubEnv('VITE_RUNNER_URL','');
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 const {controlCampaign}=await import('./ai-factory-lifecycle-api');
 await expect(controlCampaign({action:'create',campaign_id:1,workspace:'current'})).rejects.toThrow('研究执行服务尚未配置');
 expect(fetch).not.toHaveBeenCalled();
});
it('refuses a campaign mutation when the runner token is unset',async()=>{
 vi.resetModules();vi.stubEnv('VITE_RUNNER_URL','http://127.0.0.1:8420');
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 const {controlCampaign}=await import('./ai-factory-lifecycle-api');
 await expect(controlCampaign({action:'create',campaign_id:1,workspace:'current'})).rejects.toThrow('研究执行令牌尚未配置');
 expect(fetch).not.toHaveBeenCalled();
});
it('still posts the configured campaign mutation once base and token are present',async()=>{
 vi.resetModules();vi.stubEnv('VITE_RUNNER_URL','http://127.0.0.1:8420');
 const fetch=vi.fn(async()=>({ok:true,json:async()=>({})}));vi.stubGlobal('fetch',fetch);
 const {setRunnerToken}=await import('./research-runner-api');setRunnerToken('test-token');
 const {controlCampaign}=await import('./ai-factory-lifecycle-api');
 await controlCampaign({action:'stop',campaign_id:4,workspace:'current'});
 expect(fetch).toHaveBeenCalledTimes(1);
 const [url,init]=fetch.mock.calls[0] as unknown as [string,RequestInit];
 expect(url).toBe('http://127.0.0.1:8420/factory-campaign');
 expect((init.headers as Record<string,string>)['X-Runner-Token']).toBe('test-token');
});
