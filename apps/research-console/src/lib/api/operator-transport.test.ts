import {afterEach,expect,it,vi} from 'vitest';
afterEach(()=>{vi.unstubAllGlobals();vi.unstubAllEnvs();localStorage.clear();sessionStorage.clear();});
it('sends browser-safe Unicode operator tags through every research action',async()=>{
 vi.resetModules();vi.stubEnv('VITE_RUNNER_URL','http://127.0.0.1:8420');
 const requests:Headers[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_url,init)=>{requests.push(new Headers(init.headers));return {ok:true,json:async()=>({run_id:'r1'})};}));
 const api=await import('./research-runner-api');api.setOperator('研究员张三 · Alpha 🚀');api.setRunnerToken('test-token');
 const {controlFactory}=await import('./ai-factory-api');const {controlCampaign}=await import('./ai-factory-lifecycle-api');
 await api.triggerRun('data_refresh',{});await api.cancelRun('r1');
 await controlFactory({action:'create',attempt:1},'unicode-case');await controlCampaign({action:'create',campaign_id:1});
 expect(requests).toHaveLength(4);
 for(const headers of requests){
  expect(headers.get('X-Operator-Encoding')).toBe('base64-utf8-v1');
  const bytes=Uint8Array.from(atob(headers.get('X-Operator')!),c=>c.charCodeAt(0));
  expect(new TextDecoder().decode(bytes)).toBe('研究员张三 · Alpha 🚀');
  expect(headers.get('X-Runner-Token')).toBe('test-token');
 }
});
it.each(['alice','alpha%20literal','base64-utf8-v1:literal'])('preserves legacy ASCII tag %s exactly',async name=>{
 const {setOperator,getOperatorHeaders}=await import('./research-runner-api');setOperator(name);
 expect(getOperatorHeaders()).toEqual({'X-Operator':name});
});
it.each(['x'.repeat(129),'bad\nname','bad\u0000name','\ud800'])('rejects invalid tags before any request',async name=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 const {setOperator,cancelRun}=await import('./research-runner-api');setOperator(name);
 await expect(cancelRun('r1')).rejects.toThrow('操作记录名称');expect(fetch).not.toHaveBeenCalled();
});
it('counts Unicode code points consistently with the backend',async()=>{
 const {setOperator,getOperatorHeaders}=await import('./research-runner-api');setOperator('🚀'.repeat(128));
 expect(getOperatorHeaders()['X-Operator-Encoding']).toBe('base64-utf8-v1');
 setOperator('🚀'.repeat(129));expect(()=>getOperatorHeaders()).toThrow('操作记录名称');
});
