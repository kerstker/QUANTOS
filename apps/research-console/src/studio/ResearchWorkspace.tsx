import {useLocation,useNavigate} from '@/lib/router';
import type {ResearchWorkspaceContext} from '@/lib/types/research-workspace';

export function useResearchWorkspace(){
 const location=useLocation(),navigate=useNavigate();
 const params=new URLSearchParams(location.search),workspace=params.get('workspace')??'current';
 const value=Number(params.get('attempt'));
 const attempt=Number.isInteger(value)&&value>=1&&value<=1000?value:null;
 const select=(id:string)=>{
  const next=new URLSearchParams(location.search);next.delete('attempt');
  if(id==='current')next.delete('workspace');else next.set('workspace',id);
  navigate(`${location.pathname}${next.size?`?${next}`:''}`);
 };
 const selectAttempt=(id:number)=>{
  const next=new URLSearchParams(location.search);next.set('attempt',String(id));
  navigate(`${location.pathname}?${next}`);
 };
 const href=(path:string,id?:number)=>{
  const next=new URLSearchParams();if(workspace!=='current')next.set('workspace',workspace);
  if(id!==undefined)next.set('attempt',String(id));
  return `${path}${next.size?`?${next}`:''}`;
 };
 return {workspace,attempt,select,selectAttempt,href};
}

export function ResearchWorkspaceSelector({context,selected,change,disabled=false}:{
 context:ResearchWorkspaceContext|null;selected:string;change:(id:string)=>void;disabled?:boolean;
}){
 const choices=context?.workspaces??[{id:'current',label:'当前研究',writable:true},...(selected==='current'?[]:[{id:selected,label:selected,writable:false}])];
 return <div className="f-workspace">
  <label>研究空间<select aria-label="研究空间" value={selected} onChange={e=>change(e.target.value)} disabled={disabled}>
   {choices.map(row=><option value={row.id} key={row.id}>{row.label}{row.writable?'':' · 只读'}</option>)}
  </select></label>
  <p>{selected==='current'?'研究操作提交到当前空间的执行服务。':'只读查看冻结研究；创建、恢复和输入导入已停用。采集进度以记录时间为准。'}</p>
 </div>;
}
