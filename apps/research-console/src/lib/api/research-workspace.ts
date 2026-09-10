import type {ResearchWorkspace,ResearchWorkspaceContext} from '../types/research-workspace';

function isWorkspace(value:unknown):value is ResearchWorkspace {
 if(!value||typeof value!=='object')return false;
 const row=value as Record<string,unknown>;
 return typeof row.id==='string'&&/^[a-z][a-z0-9_-]{0,47}$/.test(row.id)&&
  typeof row.label==='string'&&row.label.trim().length>0&&row.label.length<=80&&
  typeof row.writable==='boolean'&&row.writable===(row.id==='current');
}
export function assertWorkspace(body:ResearchWorkspaceContext,expected:string):void {
 if(!isWorkspace(body.workspace)||body.workspace.id!==expected||!Array.isArray(body.workspaces)||
  !body.workspaces.length||body.workspaces.length>17||!body.workspaces.every(isWorkspace)||
  new Set(body.workspaces.map(row=>row.id)).size!==body.workspaces.length||
  !body.workspaces.some(row=>row.id===expected&&row.label===body.workspace.label&&row.writable===body.workspace.writable))
  throw new Error('研究空间不匹配，请检查服务连接后重试');
}

export function assertWritableWorkspace(workspace:string):void {
 if(workspace!=='current')throw new Error('当前研究空间仅供查看，请切回当前研究后操作');
}
