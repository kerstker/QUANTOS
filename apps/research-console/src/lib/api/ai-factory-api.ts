import {RESEARCH_CONSOLE_API_BASE} from './research-console-api';
import {RESEARCH_RUNNER_BASE,getRunnerToken,getOperatorHeaders} from './research-runner-api';
import type {FactoryPayload} from '../types/ai-factory';
import {assertWorkspace,assertWritableWorkspace} from './research-workspace';
export async function fetchFactory(attempt:number|null,signal:AbortSignal,workspace='current'):Promise<FactoryPayload> {
 if(!RESEARCH_CONSOLE_API_BASE)throw new Error('研究数据 API 尚未配置');
 const url=new URL('/api/v1/console/ai-factory',RESEARCH_CONSOLE_API_BASE);if(attempt)url.searchParams.set('attempt',String(attempt));
 url.searchParams.set('workspace',workspace);
 const response=await fetch(url,{signal});if(!response.ok)throw new Error(`读取失败 (${response.status})`);
 const body=await response.json();
 if(body.authoritative!==false || body.read_only!==true || !Array.isArray(body.trials)||!Array.isArray(body.sessions)||!body.provider)throw new Error('工厂数据契约不匹配');
 assertWorkspace(body,workspace);
 if(body.errors?.length)throw new Error(body.errors.join(' · '));return body;
}
export async function controlFactory(request:Record<string,unknown>,id:string) {
 assertWritableWorkspace(String(request.workspace??'current'));
 if(!RESEARCH_RUNNER_BASE)throw new Error('研究执行服务尚未配置');
 const response=await fetch(`${RESEARCH_RUNNER_BASE}/factory`,{method:'POST',headers:{'Content-Type':'application/json','X-Runner-Token':getRunnerToken(),...getOperatorHeaders(),'Idempotency-Key':id},body:JSON.stringify(request),signal:AbortSignal.timeout(15000)});
 const body=await response.json();if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:`请求失败 (${response.status})`);return body;
}

export async function importProfitabilityInput(attempt:number,kind:string,content:string,id:string,workspace='current') {
 assertWritableWorkspace(workspace);
 if(!RESEARCH_RUNNER_BASE)throw new Error('研究执行服务尚未配置');
 const response=await fetch(`${RESEARCH_RUNNER_BASE}/profitability-input`,{method:'POST',headers:{'Content-Type':'application/json','X-Runner-Token':getRunnerToken(),...getOperatorHeaders(),'Idempotency-Key':id},body:JSON.stringify({attempt,kind,content,workspace}),signal:AbortSignal.timeout(15000)});
 const body=await response.json();if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:`导入失败 (${response.status})`);return body;
}
