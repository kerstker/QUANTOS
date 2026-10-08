import {RESEARCH_CONSOLE_API_BASE} from './research-console-api';
import {RESEARCH_RUNNER_BASE,getRunnerToken,getOperatorHeaders} from './research-runner-api';
import type {FactoryLifecyclePayload} from '../types/ai-factory-lifecycle';
import {assertWorkspace,assertWritableWorkspace} from './research-workspace';
export async function fetchLifecycle(signal:AbortSignal,workspace='current'):Promise<FactoryLifecyclePayload>{
 const url=new URL('/api/v1/console/ai-factory-lifecycle',RESEARCH_CONSOLE_API_BASE);url.searchParams.set('workspace',workspace);
 const response=await fetch(url,{signal});
 if(!response.ok)throw new Error(`读取生命周期失败 (${response.status})`);
 const body=await response.json();
 if(body.read_only!==true||body.authoritative!==false||!Array.isArray(body.campaigns)||!Array.isArray(body.models)||!Array.isArray(body.sources))throw new Error('生命周期数据合同不匹配');
 assertWorkspace(body,workspace);
 return body;
}
export async function controlCampaign(body:Record<string,unknown>){
 assertWritableWorkspace(String(body.workspace??'current'));
 if(!RESEARCH_RUNNER_BASE)throw new Error('研究执行服务尚未配置');
 if(!getRunnerToken())throw new Error('研究执行令牌尚未配置');
 const response=await fetch(`${RESEARCH_RUNNER_BASE}/factory-campaign`,{method:'POST',headers:{'Content-Type':'application/json','X-Runner-Token':getRunnerToken(),...getOperatorHeaders()},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
 const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:`计划操作失败 (${response.status})`);return result;
}
