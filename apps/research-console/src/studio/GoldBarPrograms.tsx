import type {GoldBarProgramCatalog} from '@/lib/types/ai-factory-lifecycle';
import {Panel} from './components';
import {stamp} from './data';

const labels:Record<string,string>={
 WAITING_CLOSED_BAR:'等待预定收盘',COLLECTING:'采集窗口数据',PROPOSING_HYPOTHESIS:'生成研究假设',
 USING_RETAINED_HYPOTHESIS:'已接收留存假设',WINDOW_ADVANCED:'已安排后续窗口',
 RESEARCH_SUBMITTED:'已提交实验',RESEARCH_FAILED:'实验失败',RESEARCH_RECOVERY_SUBMITTED:'已提交结果恢复',
 REVIEW_SUBMITTED:'已提交复核',REVIEW_FAILED:'复核失败',ROUND_COMPLETE:'轮次完成',PROGRAM_LOSS_STOP:'触发停止条件',
 RESEARCH_PROGRAM_COMPLETE:'计划完成',RESEARCH_PROGRAM_INCOMPLETE:'计划未完成',RESEARCH_PROGRAM_STOPPED_LOSS_LIMIT:'达到亏损上限，已停止',
};
export function GoldBarPrograms({catalog}:{catalog?:GoldBarProgramCatalog}){
 if(!catalog||catalog.status==='NO_SNAPSHOT')return null;
 return <Panel title="黄金收盘策略实验" note="展示保存的研究进度。没有实时进程确认；计划完成也不代表策略合格。">
  {catalog.status==='DATA_BLOCKED'&&<p role="status">部分进度记录不可读取，状态待核对。</p>}
  {catalog.items.map(row=><article key={row.program_id}>
   <h3>实验计划 #{row.program_id}</h3>
   <p>{labels[row.terminal_status??row.recorded_stage??'']??'阶段未知'}</p>
   <progress aria-label={`计划 ${row.program_id} 已完成轮次`} value={row.completed_rounds} max={row.planned_rounds}/>
   <p>{row.completed_rounds} / {row.planned_rounds} 轮完成 · 每轮 {row.window_bars} 根收盘</p>
   <p>最后阶段记录：{stamp(row.recorded_at_ms,true)} · 进程状态未确认</p>
   {row.error_type&&<p>失败类型：{row.error_type}</p>}
  </article>)}
  {catalog.truncated&&<p>仅展示有界范围内的记录，更多历史未载入。</p>}
 </Panel>;
}
