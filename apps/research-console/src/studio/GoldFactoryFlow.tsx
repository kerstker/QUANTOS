import {useState} from 'react';
import type {FactoryLifecyclePayload} from '@/lib/types/ai-factory-lifecycle';
import {Panel} from './components';

type Step={name:string;status:string;detail:string;tone:'evidence'|'waiting'|'blocked'};
export function GoldFactoryFlow({data,inspect}:{data:FactoryLifecyclePayload;inspect:(attempt:number)=>void}){
 const [chosen,setChosen]=useState<number|null>(null);
 const campaigns=data.campaigns.filter(c=>c.config.market==='XAUUSDT');
 const campaign=campaigns.find(c=>c.campaign_id===chosen)??campaigns[0];
 if(!campaign)return null;
 const round=data.rounds.filter(r=>r.campaign_id===campaign.campaign_id).sort((a,b)=>b.ordinal-a.ordinal)[0];
 const result=data.gold_results?.find(r=>r.attempt===round?.attempt);
 const input=data.gold_inputs?.items.find(i=>i.input_id===campaign.config.input_id&&i.input_sha256===campaign.config.input_sha256);
 const report=result?.report_status==='AVAILABLE';
 const review=result?.latest_review;
 const feedback=result?.execution_review;
 const records=feedback?.status==='AVAILABLE'?feedback.records:[];
 const next=[...new Map(records.flatMap(r=>r.next_attempts).map(r=>[r.attempt,r])).values()];
 const steps:Step[]=[
  {name:'采集与输入',status:input?'已绑定数据快照':'快照未在当前列表确认',detail:`输入 #${campaign.config.input_id} · 本计划使用固定数据和参数`,tone:input?'evidence':'waiting'},
  {name:'AI 假设与实验',status:result?.state==='FAILED'?'本轮失败':result?.state==='COMPLETED'?'本轮实验已结束':round?'本轮处理中':'等待首轮',detail:round?`研究 #${round.attempt} · 第 ${round.ordinal+1} / ${campaign.config.max_cycles} 轮`:'等待计划调度',tone:result?.state==='FAILED'?'blocked':result?.state==='COMPLETED'?'evidence':'waiting'},
  {name:'成本后回放',status:report?(result?.outcome==='NO_SIGNAL'?'本轮无交易信号':'已有成本报告'):'尚无本轮成本报告',detail:'含费用与资金费的技术回放，不代表实际账户收益',tone:report?'evidence':'waiting'},
  {name:'独立核验与准入',status:review?.current&&review.status==='VERIFIED_SIGNATURE_ENGINEERING_ONLY'?'签名已核对，尚无经济准入':review?.status==='REJECTED'?'核验未通过':review?.status==='PENDING_RECHECK'?'等待重新核验':'尚无独立准入证据',detail:'组合及执行仍须经过独立批准与原风险检查',tone:'blocked'},
  {name:'Demo 执行与对账',status:records.length?'有技术复盘，真实成交待核实':feedback?.status==='UNAVAILABLE'?'复盘资料不可用':'等待本策略执行证据',detail:records.some(r=>r.schema==='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1')?'已关联整轮开平仓复盘，来源尚未认证':'按原订单与资金账本核对，不从研究状态推断成交',tone:records.length?'evidence':'waiting'},
  {name:'复盘与再研究',status:next.length?next.map(n=>`后续研究 #${n.attempt} ${n.state==='COMPLETED'?'已完成':n.state==='FAILED'?'失败':'已登记'}`).join(' · '):'尚无反馈触发的后续研究',detail:'反馈引用原策略；下一轮沿用间隔、预算和独立验证要求',tone:next.length?'evidence':'waiting'},
 ];
 return <Panel title="黄金工厂 · 本轮闭环进度" note="根据所选计划的最新一轮记录展示；已有技术资料不等于整厂或真实交易验收通过">
 <div className="gf-flow-heading"><label>查看黄金计划<select value={campaign.campaign_id} onChange={e=>setChosen(Number(e.target.value))}>{campaigns.map(c=><option key={c.campaign_id} value={c.campaign_id}>计划 #{c.campaign_id} · {c.config.objective}</option>)}</select></label>
 <button className="s-button" disabled={!result} onClick={()=>result&&inspect(result.attempt)}>查看本轮报告与反馈</button></div>
 <ol className="gf-flow" aria-label="黄金工厂六阶段进度">{steps.map((step,i)=><li key={step.name} data-tone={step.tone}><span className="gf-flow-number">{String(i+1).padStart(2,'0')}</span><h3>{step.name}</h3><strong>{step.status}</strong><p>{step.detail}</p></li>)}</ol>
 </Panel>;
}
