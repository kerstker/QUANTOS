import {useId,useRef,useState} from 'react';
import {Link} from '@/lib/router';
import {fetchLifecycle,controlCampaign} from '@/lib/api/ai-factory-lifecycle-api';
import {getOperator,getRunnerToken,setOperator,setRunnerToken} from '@/lib/api/research-runner-api';
import {Header,Panel,Empty,ErrorNote,Badge} from './components';
import {Chart} from './Charts';
import {ModelCalibration} from './ModelCalibration';
import {useResource,fmt,stamp,download,statusLabel} from './data';
import './factory.css';
import './factory-lifecycle.css';

export function FactoryLifecycle(){
 const resource=useResource('factory-lifecycle',fetchLifecycle,15000),data=resource.data;
 const [tab,setTab]=useState(0),id=useId(),[query,setQuery]=useState('');
 const [sourceQuery,setSourceQuery]=useState('');
 const [objective,setObjective]=useState('研究 BTC 小时级价格、波动与主动成交信息的组合，在相同成本和风险约束下比较新模型。');
 const [trigger,setTrigger]=useState('schedule'),[champion,setChampion]=useState(0);
 const [cycles,setCycles]=useState(3),[budget,setBudget]=useState(3),[interval,setInterval]=useState(24),[mode,setMode]=useState('ai');
 const [sourceIds,setSourceIds]=useState(['buildalpha','aqr','rd_agent']);
 const [operator,editOperator]=useState(getOperator),[token,editToken]=useState(getRunnerToken);
 const pendingCreate=useRef<{intent:string;campaign_id:number}|null>(null);
 const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const [chosenModel,setChosenModel]=useState<number|null>(null);
 const model=data?.models.find(m=>m.attempt===chosenModel)??data?.models[0];
 const multiplier=mode==='ai'?10:7;
 const act=async(action:string,campaign_id?:number)=>{
  setBusy(true);setMessage('');setOperator(operator.trim());setRunnerToken(token.trim());
  const intent=JSON.stringify({objective,mode,budget,cycles,interval,sourceIds,trigger,champion});
  if(action==='create'&&pendingCreate.current?.intent!==intent)pendingCreate.current={intent,campaign_id:Math.max(0,...(data?.campaigns.map(c=>c.campaign_id)??[]))+1};
  const next=campaign_id??pendingCreate.current!.campaign_id;
  try {await controlCampaign(action==='create'?{action,campaign_id:next,objective,mode,budget,max_cycles:cycles,interval_hours:interval,source_ids:sourceIds,trigger,champion_attempt:trigger==='drift'?champion:null}:{action,campaign_id:next});if(action==='create')pendingCreate.current=null;setMessage(action==='stop'?'计划已停止，正在执行的轮次会保留并完成；如需立即暂停，请进入对应任务。':'计划已保存，调度状态将在下一次刷新显示。');resource.refresh();}
  catch(e){setMessage(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}
 };
 const tabs=['生命周期','持续研究','模型演进','来源对标'];
 return <>
 <Header eyebrow="ALPHA FACTORY / LIFECYCLE" title="让研究持续推进，让每一步可追溯" action={<Link className="s-button primary" to="/studio/factory">打开实验工厂 →</Link>}>从公开方法到研究计划，再到冻结模型与前向观察。执行、组合和经济证据按各自实际状态呈现。</Header>
 <ErrorNote error={resource.error} retry={resource.refresh}/>
 {!!data?.errors.length&&<p className="f-message" role="alert">部分证据不可用：{data.errors.join(' · ')}</p>}
 <div className="lf-summary" aria-label="工厂概况">
 <div><small>对标来源</small><strong>{data?.sources.length??'—'}<em> / 36</em></strong><span>机构与系统分别标明</span></div>
 <div><small>持续研究计划</small><strong>{data?.campaigns.filter(c=>c.state==='RUNNING').length??'—'}</strong><span>{data?.worker?`调度：${statusLabel(data.worker.status)}`:data?.scheduler_enabled?'已启用 · 等待心跳':'调度未启用'}</span></div>
 <div><small>冻结研究模型</small><strong>{data?.models.length??'—'}</strong><span>预测观察，不代表可交易策略</span></div>
 <div><small>实际执行与经济准入</small><strong className="lf-small">尚未闭合</strong><span>需要独立证据和场所回执</span></div>
 </div>
 <div className="f-evidence-nav" role="tablist" aria-label="Alpha 生命周期工作区">{tabs.map((t,i)=><button key={t} id={`${id}-${i}`} role="tab" aria-selected={tab===i} aria-controls={`${id}-panel`} tabIndex={tab===i?0:-1} onClick={()=>setTab(i)} onKeyDown={e=>{const n=e.key==='ArrowRight'?(i+1)%4:e.key==='ArrowLeft'?(i+3)%4:e.key==='Home'?0:e.key==='End'?3:null;if(n!==null){e.preventDefault();setTab(n);document.getElementById(`${id}-${n}`)?.focus();}}}>{t}</button>)}</div>
 <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${tab}`} className="lf-body" tabIndex={0}>
 {tab===0&&<>
 <div className="lf-stages">{data?.capabilities.map(c=><article key={c.id}><div><span className="lf-number">{c.id}</span><Badge value={c.status}/></div><h2>{c.title}</h2><p>{c.detail}</p></article>)}</div>
 <Panel title="三个持续循环" note="研究任务完成、模型可观察、实际交易合格是三个不同状态"><div className="lf-loops"><div><h3>研究发现</h3><p>来源 → 假设 → 特征与模型 → 内层比较 → 下一实验</p><Link to="/studio/factory">查看实际实验与失败记录 →</Link></div><div><h3>模型演进</h3><p>冻结 → 预测 → 成熟标签 → 基线比较 → 新挑战者</p><button className="s-button" onClick={()=>setTab(2)}>查看模型诊断</button></div><div><h3>执行反馈</h3><p>目标持仓 → 订单与对账 → 成本归因 → 校准</p><Link to="/studio/modules/mft-execution">查看执行证据与缺口 →</Link></div></div></Panel>
 </>}
 {tab===1&&<div className="lf-split"><Panel title="创建持续研究计划" note="配置一次范围，预算内分轮推进；暂停与停止不删除历史">
 <form className="f-form" onSubmit={e=>{e.preventDefault();void act('create');}}>
 <label>研究目标<textarea required rows={4} maxLength={2000} value={objective} onChange={e=>setObjective(e.target.value)}/></label>
 <div className="f-fields"><label>最多轮次<input type="number" min={1} max={12} value={cycles} onChange={e=>setCycles(Number(e.target.value))}/></label><label>每轮主实验<select value={budget} onChange={e=>setBudget(Number(e.target.value))}>{[3,6,9,12,18].map(n=><option key={n}>{n}</option>)}</select></label></div>
 <label>触发方式<select value={trigger} onChange={e=>setTrigger(e.target.value)}><option value="schedule">固定间隔 · 有限轮次</option><option value="drift">成熟预测落后零基线时研究挑战者</option></select></label>
 {trigger==='drift'&&<label>观察中的基准模型<select value={champion} onChange={e=>setChampion(Number(e.target.value))}><option value={0}>选择冻结模型</option>{data?.models.map(m=><option key={m.attempt} value={m.attempt}>模型 #{m.attempt} · {m.spec?.model}</option>)}</select></label>}
 <label>轮次冷却间隔（小时）<input type="number" min={1} max={168} value={interval} onChange={e=>setInterval(Number(e.target.value))}/></label>
 <label>编排方式<select value={mode} onChange={e=>setMode(e.target.value)}><option value="ai">本地模型编排</option><option value="baseline">确定性基线</option></select></label>
 <fieldset><legend>方法来源（最多 8 项）</legend><label>筛选可选来源<input type="search" value={sourceQuery} onChange={e=>setSourceQuery(e.target.value)} placeholder="名称、类别或研究方法"/></label><p className="lf-source-limit">已选择 {sourceIds.length} / 8 · 可从全部 {data?.sources.length??0} 个来源中选择</p><div className="lf-source-choices">{data?.sources.filter(s=>`${s.name} ${s.category} ${s.adopt}`.toLowerCase().includes(sourceQuery.toLowerCase())).map(s=><label key={s.id}><input type="checkbox" disabled={!sourceIds.includes(s.id)&&sourceIds.length>=8} checked={sourceIds.includes(s.id)} onChange={e=>setSourceIds(e.target.checked?[...sourceIds,s.id].slice(0,8):sourceIds.filter(i=>i!==s.id))}/>{s.name}</label>)}</div></fieldset>
 <details><summary>研究执行连接</summary><label>操作记录名称<input value={operator} onChange={e=>editOperator(e.target.value)} autoComplete="off"/></label><label>研究令牌<input type="password" value={token} onChange={e=>editToken(e.target.value)} autoComplete="off"/></label></details>
 <p className="lf-budget">最多 <strong>{cycles*budget}</strong> 次主实验，连同三折过程对照最多 <strong>{cycles*budget*multiplier}</strong> 次候选评估。失败消耗尝试，固定压力诊断另计。主实验不重复已尝试规格；历史窗口复用不建立独立证据。</p>
 <button className="s-button primary" disabled={busy||!data?.scheduler_enabled||!token.trim()||!operator.trim()||!sourceIds.length||cycles*budget>72||(trigger==='drift'&&!champion)}>启动持续研究计划</button>
 {(!operator.trim()||!token.trim())&&<p className="f-muted">请在“研究执行连接”中填写操作记录名称与研究令牌。</p>}
 {!data?.scheduler_enabled&&<p>研究服务尚未启用持续调度。</p>}
 </form></Panel><div className="lf-stack"><Panel title="计划与预算" note="冷却时间从上一轮结束计算；停止计划不撤销正在执行的研究">
 {data?.campaigns.length?data.campaigns.map(c=>{const rounds=data.rounds.filter(r=>r.campaign_id===c.campaign_id),last=rounds[0];return <article className="lf-campaign" key={c.campaign_id}><div className="lf-row"><h3>计划 #{c.campaign_id}</h3><span className="f-muted">{({RUNNING:'计划进行中',PAUSED:'计划已暂停',STOPPED:'计划已停止',COMPLETED:'计划已完成'} as Record<string,string>)[c.state]??statusLabel(c.state)}</span></div><p>{c.config.objective}</p><progress aria-label={`计划 ${c.campaign_id} 预算预留`} value={rounds.length} max={c.config.max_cycles}/><p>{rounds.length} / {c.config.max_cycles} 轮已预留 · 每轮 {c.config.budget} 次主实验</p><p>调度条件：{({ROUND_IN_PROGRESS:'当前轮次处理中',COOLDOWN:'等待冷却',WAITING_MATURE_DIAGNOSTIC:'等待成熟诊断',WAITING_OBSERVATION:'等待观察',STALE_OBSERVATION:'观察已过期',SCHEDULE:'等待队列',BUDGET_EXHAUSTED:'预算已用完',MODEL_UNDERPERFORMS_ZERO_BASELINE:'模型落后基线，准备研究挑战者'} as Record<string,string>)[c.wait_reason]??statusLabel(c.wait_reason)}</p><p>最早下一轮：{c.state!=='RUNNING'?'计划未运行':last?.state==='RESERVED'?'当前轮次处理中':c.next_due_ms!==null?stamp(c.next_due_ms,true):'等待调度'}</p><div className="lf-row"><button className="s-button" disabled={busy||c.state!=='RUNNING'} onClick={()=>void act('pause',c.campaign_id)}>暂停计划</button><button className="s-button" disabled={busy||c.state!=='PAUSED'} onClick={()=>void act('resume',c.campaign_id)}>恢复计划</button><button className="s-button" disabled={busy||!['RUNNING','PAUSED'].includes(c.state)} onClick={()=>void act('stop',c.campaign_id)}>停止计划</button></div></article>}):<Empty title="尚无持续研究计划">先固定目标、来源和预算。系统会保留每轮研究身份及失败记录。</Empty>}
 </Panel><Panel title="轮次轨迹"><div className="s-table-wrap" role="region" aria-label="计划轮次" tabIndex={0}><table className="s-table"><thead><tr><th>计划 / 轮次</th><th>任务</th><th>状态</th><th>创建时间</th></tr></thead><tbody>{data?.rounds.map(r=><tr key={`${r.campaign_id}-${r.ordinal}`}><td>#{r.campaign_id} / {r.ordinal+1}</td><td><Link to={`/studio/factory?attempt=${r.attempt}`}>研究 #{r.attempt} →</Link></td><td><Badge value={r.state}/></td><td>{stamp(r.created_ms,true)}</td></tr>)}</tbody></table></div></Panel></div></div>}
 {tab===2&&<>
 <Panel title="冻结模型与挑战者" note="先比较自然预测与零收益预测基线；168 条成熟标签只是诊断起点，重叠标签不是独立样本">
 <div className="s-table-wrap" role="region" aria-label="模型生命周期" tabIndex={0}><table className="s-table"><thead><tr><th>模型</th><th>观察状态</th><th>预测 / 标签</th><th>RMSE / 零基线</th><th>下一步</th></tr></thead><tbody>{data?.models.map(m=><tr key={m.attempt}><td><button className="s-button" onClick={()=>setChosenModel(m.attempt)}>#{m.attempt} {m.spec?.model}</button><small>{m.spec?.transform??'原始特征'}</small></td><td><Badge value={m.state}/></td><td>{m.predictions} / {m.labels}</td><td>{fmt(m.diagnostics.rmse,5)} / {fmt(m.diagnostics.zero_baseline_rmse,5)}</td><td>{m.diagnostics.recommendation==='RESEARCH_CHALLENGER'?'建议研究新挑战者':'继续观察'} · <Link to={`/studio/factory?attempt=${m.attempt}`}>观察控制 →</Link></td></tr>)}</tbody></table></div>
 {!data?.models.length&&<Empty title="尚无冻结模型">完成真实研究后，模型与观察状态会显示在这里。</Empty>}
 </Panel>
 <Chart title={`模型 #${model?.attempt??'—'} · 自然预测误差`} rows={(model?.diagnostics.daily??[]).map(r=>({...r,label:stamp(r.time,true),rmse:r.rmse*10000,baseline_rmse:r.baseline_rmse*10000}))} series={[{key:'rmse',label:'模型 RMSE'},{key:'baseline_rmse',label:'零预测基线'}]} unit="bp" note="按实际成熟标签分日汇总，低预测误差不等于可交易盈利。"/>
 <ModelCalibration model={model}/>
 <Panel title="同一时点模型比较" note="只比较共同成熟标签，不拼接不同窗口"><div className="s-table-wrap" role="region" aria-label="模型配对比较" tabIndex={0}><table className="s-table"><thead><tr><th>模型对</th><th>共同标签</th><th>MSE 差（左减右）</th><th>证据状态</th></tr></thead><tbody>{data?.comparisons.map(c=><tr key={`${c.left}-${c.right}`}><td>#{c.left} / #{c.right}</td><td>{c.shared_labels}</td><td>{fmt(c.mse_delta,8)}</td><td>{c.status}</td></tr>)}</tbody></table></div></Panel>
 </>}
 {tab===3&&<Panel title="36 个对标来源" note="20 家机构与 16 个量化或支撑系统；采用公开方法，不采信营销收益" action={<button className="s-button" disabled={!data} onClick={()=>download('quantos-benchmark-36.json',data?.sources)}>导出来源</button>}>
 <label className="lf-search">检索名称、类别或采用方式<input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="例如：成本、模型版本、国内"/></label>
 <div className="lf-sources">{data?.sources.filter(s=>`${s.name} ${s.category} ${s.adopt}`.toLowerCase().includes(query.toLowerCase())).map(s=><article key={s.id}><small>{s.category}</small><h3><a href={s.url} target="_blank" rel="noopener noreferrer">{s.name} ↗</a></h3><p>{s.adopt}</p><p className="lf-muted">{s.limitation}</p></article>)}</div>
 </Panel>}
 <p role="status" className="f-message">{message}</p>
 </div></>;
}
