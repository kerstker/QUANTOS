import type {FactoryLifecyclePayload} from '@/lib/types/ai-factory-lifecycle';
import {Panel} from './components';

const statuses:Record<string,string>={QUEUED:'采集等待队列',RUNNING:'正在采集公开数据',FAILED:'采集失败',CANCELLED:'采集已取消',RESEARCH_CREATED:'采集交接完成，已创建研究计划',HANDOFF_INCOMPLETE:'采集任务结束，研究计划尚未确认',UNKNOWN:'采集任务状态尚未确认'};
const reasons:Record<string,string>={GOLD_SIGNAL_INPUT_INCOMPLETE:'预定决策时刻的数据覆盖不完整，未发布研究输入。',GOLD_PAIRED_CAPTURE_FAILED:'报价或成交采集失败。',GOLD_COLLECTION_STREAM_INCOMPLETE:'成交数据流不完整。',GOLD_PREPARATION_SOURCE_CHANGED:'输入装配期间源资料发生变化。',GOLD_COLLECTION_REQUEST_CHANGED:'采集请求与留存记录不一致。',GOLD_COLLECTION_INPUT_ID_OCCUPIED:'目标数据编号已被占用。',COLLECTION_FAILED:'采集任务失败，原任务保留诊断记录。',RUNNER_EVIDENCE_UNAVAILABLE:'暂时无法核对原任务状态，请查看研究任务记录。',RESEARCH_PLAN_NOT_OBSERVED:'尚未读取到对应研究计划。'};

export function GoldCollectionStatus({rows,compact=false}:{rows:FactoryLifecyclePayload['collection_requests'];compact?:boolean}){
 if(!rows.length)return null;
 const content=<>{rows.map(row=>{const observation=row.observation;const state=observation?.status??'UNKNOWN';return <article key={row.campaign_id} className="lf-campaign" aria-label={`采集计划 ${row.campaign_id}`}>
  {!compact&&<h3>采集计划 #{row.campaign_id}</h3>}
  <strong>{statuses[state]??statuses.UNKNOWN}</strong>
  <p>决策时机：{!row.request?'尚未确认':row.request.decision_policy==='AI_DESIGNED_ZONE_V1'?'AI 设计区域与退出规则':row.request.decision_policy==='AI_DESIGNED_WATERMARK_V1'?'AI 先设计窗口，再采集研究':row.request.decision_policy==='FIRST_RECEIVED_WATERMARK_V1'?'窗口数据到齐时':row.request.decision_policy===undefined||row.request.decision_policy==='FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1'?'模板固定时刻':'尚未确认'}</p>
  {observation?.reason!==''&&<p>{reasons[observation?.reason??'RUNNER_EVIDENCE_UNAVAILABLE']??'请查看原研究任务诊断。'}</p>}
  {['FAILED','CANCELLED'].includes(state)&&!observation?.research_created&&<p>本次尚未进入 AI 研究、回测或执行。</p>}
  {observation?.run_id&&<small>任务 {observation.run_id}</small>}
 </article>;})}</>;
 return compact?content:<Panel title="黄金数据采集进度" note="采集成功后才创建研究计划；周期组的下一批按原预算和间隔调度">{content}</Panel>;
}
