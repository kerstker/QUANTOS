import {useId,useRef,useState} from 'react';
import {Link} from '@/lib/router';
import {fetchLifecycle,controlCampaign} from '@/lib/api/ai-factory-lifecycle-api';
import {getOperator,getRunnerToken,setOperator,setRunnerToken} from '@/lib/api/research-runner-api';
import {Header,Panel,Empty,ErrorNote,Badge} from './components';
import {Chart} from './Charts';
import {GoldFactoryFlow} from './GoldFactoryFlow';
import {GoldCollectionStatus} from './GoldCollectionStatus';
import {GoldBarPrograms} from './GoldBarPrograms';
import {GoldResearchResults} from './GoldResearchResults';
import {ModelCalibration} from './ModelCalibration';
import {useResource,fmt,stamp,download,statusLabel} from './data';
import './factory.css';
import './factory-lifecycle.css';
import {ResearchWorkspaceSelector,useResearchWorkspace} from './ResearchWorkspace';

export function FactoryLifecycle(){
 const view=useResearchWorkspace(),{workspace}=view;
 const resource=useResource(`factory-lifecycle:${workspace}`,signal=>fetchLifecycle(signal,workspace),15000),data=resource.data;
 const canWrite=workspace==='current'&&data?.workspace?.id===workspace&&data.workspace.writable&&!resource.error;
 const activeWorkspace=useRef(workspace);activeWorkspace.current=workspace;
 const [tab,setTab]=useState(0),id=useId(),[query,setQuery]=useState('');
 const [sourceQuery,setSourceQuery]=useState('');
 const [protocol,setProtocol]=useState('SINGLE_V1');
 const [market,setMarket]=useState('BTC'),[inputSelection,setInputSelection]=useState('');
 const [collectFirst,setCollectFirst]=useState(false),[collectionSeconds,setCollectionSeconds]=useState(30),[collectionBatches,setCollectionBatches]=useState(1);
 const [collectionPolicy,setCollectionPolicy]=useState('FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1');
 const [feedbackMode,setFeedbackMode]=useState(false);
 const gold=market==='XAUUSDT';
 const nextInput=Math.max(0,...(data?.gold_inputs?.items.map(item=>item.input_id)??[]),...(data?.collection_requests?.map(item=>item.request.input_id)??[]))+1;
 const snapshot=data?.gold_inputs?.items.find(item=>`${item.input_id}:${item.input_sha256}`===inputSelection);
 const inputId=snapshot?.input_id,inputHash=snapshot?.input_sha256;
 const [objective,setObjective]=useState('研究 BTC 小时级价格、波动与主动成交信息的组合，在相同成本和风险约束下比较新模型。');
 const [trigger,setTrigger]=useState('schedule'),[champion,setChampion]=useState(0);
 const [cycles,setCycles]=useState(3),[budget,setBudget]=useState(3),[interval,setInterval]=useState(24),[mode,setMode]=useState('ai');
 const [sourceIds,setSourceIds]=useState(['buildalpha','aqr','rd_agent']);
 const [operator,editOperator]=useState(getOperator),[token,editToken]=useState(getRunnerToken);
 const pendingCreate=useRef<{intent:string;campaign_id:number;input_id:number}|null>(null);
 const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const [chosenModel,setChosenModel]=useState<number|null>(null);
 const model=data?.models.find(m=>m.attempt===chosenModel)??data?.models[0];
 const multiplier=mode==='ai'?13:7;
 const act=async(action:string,campaign_id?:number)=>{
  if(!canWrite||busy||(action==='create'&&gold&&!snapshot))return;
  setBusy(true);setMessage('');setOperator(operator.trim());setRunnerToken(token.trim());
  const intent=JSON.stringify({objective,mode,budget,cycles,interval,sourceIds,trigger,champion,protocol,market,inputId,inputHash,feedbackMode,collectFirst,collectionSeconds,collectionBatches,collectionPolicy});
  if(action==='create'&&pendingCreate.current?.intent!==intent)pendingCreate.current={intent,campaign_id:Math.max(0,...(data?.campaigns.map(c=>c.campaign_id)??[]),...(data?.collection_requests?.map(c=>c.campaign_id)??[]))+1,input_id:nextInput};
  const next=campaign_id??pendingCreate.current!.campaign_id;
  const config=gold?{market:'XAUUSDT',mode:'ai',budget:1,input_id:inputId,input_sha256:inputHash,...(collectFirst&&action==='create'?{collection_input_id:pendingCreate.current!.input_id,collection_seconds:collectionSeconds,collection_batches:collectionBatches,...(collectionPolicy!=='FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1'?{collection_decision_policy:collectionPolicy}:{})}:{}),...(feedbackMode?{execution_feedback_mode:'after_first_round'}:{})}:{mode,budget,...(mode==='ai'?{research_protocol:protocol}:{}),source_ids:sourceIds,trigger,champion_attempt:trigger==='drift'?champion:null};
  try {await controlCampaign(action==='create'?{action:gold&&collectFirst?'collect_create':action,workspace,campaign_id:next,objective,max_cycles:cycles,interval_hours:interval,...config}:{action,workspace,campaign_id:next});if(activeWorkspace.current!==workspace)return;if(action==='create')pendingCreate.current=null;setMessage(action==='stop'?'计划已停止，正在执行的轮次会保留并完成；如需立即暂停，请进入对应任务。':gold&&collectFirst&&action==='create'?'采集任务已登记；成功后自动创建研究计划，可在研究任务页查看进度。':'计划已保存，调度状态将在下一次刷新显示。');resource.refresh();}
  catch(e){setMessage(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}
 };
 const tabs=['生命周期','持续研究','模型演进','来源对标'];
 return <>
 <Header eyebrow="ALPHA FACTORY / LIFECYCLE" title="让研究持续推进，让每一步可追溯" action={<Link className="s-button primary" to={view.href('/studio/factory')}>打开实验工厂 →</Link>}>从公开方法到研究计划，再到冻结模型与前向观察。执行、组合和经济证据按各自实际状态呈现。</Header>
 <ErrorNote error={resource.error} retry={resource.refresh}/>
 <ResearchWorkspaceSelector context={data} selected={workspace} change={id=>{pendingCreate.current=null;setMessage('');setChosenModel(null);view.select(id);}} disabled={busy}/>
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
 <GoldBarPrograms catalog={data?.gold_bar_programs}/>
 {data&&<GoldCollectionStatus rows={data.collection_requests??[]}/>}
 {data&&<GoldFactoryFlow data={data} inspect={attempt=>{setTab(1);requestAnimationFrame(()=>{const element=document.getElementById(`gold-result-${attempt}`);if(element instanceof HTMLDetailsElement)element.open=true;element?.scrollIntoView({block:'center',behavior:'smooth'});});}}/>}
 <div className="lf-stages">{data?.capabilities.map(c=><article key={c.id}><div><span className="lf-number">{c.id}</span><Badge value={c.status}/></div><h2>{c.title}</h2><p>{c.detail}</p></article>)}</div>
 <Panel title="三个持续循环" note="研究任务完成、模型可观察、实际交易合格是三个不同状态"><div className="lf-loops"><div><h3>研究发现</h3><p>来源 → 假设 → 特征与模型 → 内层比较 → 下一实验</p><Link to={view.href('/studio/factory')}>查看实际实验与失败记录 →</Link></div><div><h3>模型演进</h3><p>冻结 → 预测 → 成熟标签 → 基线比较 → 新挑战者</p><button className="s-button" onClick={()=>setTab(2)}>查看模型诊断</button></div><div><h3>执行反馈</h3><p>目标持仓 → 订单与对账 → 成本归因 → 校准</p><Link to="/studio/modules/mft-execution">查看执行证据与缺口 →</Link></div></div></Panel>
 </>}
 {tab===1&&<div className="lf-split"><Panel title="创建持续研究计划" note="配置一次范围，预算内分轮推进；暂停与停止不删除历史">
 <form className="f-form" onSubmit={e=>{e.preventDefault();void act('create');}}>
 <label>研究市场<select value={market} onChange={e=>{setMarket(e.target.value);pendingCreate.current=null;if(e.target.value==='XAUUSDT'){setCycles(Math.min(cycles,6));setObjective('研究 XAUUSDT 黄金合约的价格与成交信息，比较成本后的策略表现，保留失败与淘汰记录。');}}}><option value="BTC">BTC 小时级研究</option><option value="XAUUSDT">XAUUSDT 黄金合约</option></select></label>
 <label>研究目标<textarea required rows={4} maxLength={2000} value={objective} onChange={e=>setObjective(e.target.value)}/></label>
 <div className="f-fields"><label>最多轮次<input type="number" min={1} max={gold?6:12} value={cycles} onChange={e=>setCycles(Number(e.target.value))}/></label>{!gold&&<label>每轮主实验<select value={budget} onChange={e=>setBudget(Number(e.target.value))}>{[3,6,9,12,18].map(n=><option key={n}>{n}</option>)}</select></label>}</div>
 {gold?<><label>黄金数据快照<select required value={snapshot?inputSelection:''} onChange={e=>setInputSelection(e.target.value)}><option value="">选择已保存的数据</option>{data?.gold_inputs?.items.map(item=><option key={`${item.input_id}:${item.input_sha256}`} value={`${item.input_id}:${item.input_sha256}`}>快照 #{item.input_id} · {stamp(item.start_ms,true)} → {stamp(item.end_ms,true)} · {item.frame_count} 帧</option>)}</select></label>{snapshot&&<p className="f-muted">已绑定快照 #{snapshot.input_id} · {snapshot.decision_count} 个决策时点。同一计划固定使用这份数据。</p>}{!data?.gold_inputs?.items.length&&<p role="status">暂无可用黄金数据快照，采集并保存数据后可启动研究。</p>}{gold&&canWrite&&<Link className="s-text-link" to="/studio/new?kind=gold_prepare_input">装配已保存的采集数据 →</Link>}{gold&&canWrite&&!!data?.gold_inputs?.items.length&&<Link className="s-text-link" to="/studio/new?kind=gold_collect_input">采集新的黄金研究数据 →</Link>}{!!data?.gold_inputs?.errors.length&&<p role="alert">部分数据快照读取失败：{data.gold_inputs.errors.join(' · ')}</p>}{data?.gold_inputs?.truncated&&<p className="f-muted">本次仅展示有限数量的快照。</p>}<label><input type="checkbox" checked={collectFirst} onChange={e=>setCollectFirst(e.target.checked)}/>先采集新数据，完成后自动启动计划</label>{collectFirst&&<><label>决策时机<select value={collectionPolicy} onChange={e=>setCollectionPolicy(e.target.value)}><option value="FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1">模板固定时刻</option><option value="FIRST_RECEIVED_WATERMARK_V1">窗口数据到齐时</option><option value="AI_DESIGNED_WATERMARK_V1">AI 先设计窗口，再采集研究</option><option value="AI_DESIGNED_ZONE_V1">AI 设计区域与退出规则</option></select></label><p className="f-muted">AI 模式先由本地模型提出窗口宽度、等待上限和窗口数，再开始新采集。数据到齐模式以首个已收到的窗口边界确定决策时刻；超过所选时效或数据有缺口，本批停止。区域设计模式还生成相对首个报价的价格区域、止损和止盈；数量和成本沿用模板。其余模式保留模板价格区域。</p><label>采集批次<select value={collectionBatches} onChange={e=>setCollectionBatches(Number(e.target.value))}>{[1,2,3,6].map(n=><option key={n} value={n}>{n} 批</option>)}</select></label><label>成交采集时长（秒）<input type="number" min={15} max={60} value={collectionSeconds} onChange={e=>setCollectionSeconds(Number(e.target.value))}/></label><p className="f-muted">所选快照作为参数模板，采集结果使用新的编号；所选设计在采集前固定，随后按当前目标和预算开始研究。每批结束后按同一冷却间隔等待，再采集下一份快照。</p></>}<label><input type="checkbox" checked={feedbackMode} onChange={e=>setFeedbackMode(e.target.checked)}/>首轮后等待 Demo 整轮对账反馈</label><p className="f-muted">启用后，后续轮次等待对应策略完成开平仓与资金核对；未产生可执行策略时可能保持等待。创建研究计划不会发出交易指令。</p></>:<>
 <label>触发方式<select value={trigger} onChange={e=>setTrigger(e.target.value)}><option value="schedule">固定间隔 · 有限轮次</option><option value="drift">成熟预测落后零基线时研究挑战者</option></select></label>
 {trigger==='drift'&&<label>观察中的基准模型<select value={champion} onChange={e=>setChampion(Number(e.target.value))}><option value={0}>选择冻结模型</option>{data?.models.map(m=><option key={m.attempt} value={m.attempt}>模型 #{m.attempt} · {m.spec?.model}</option>)}</select></label>}
 </>}
 <label>轮次冷却间隔（小时）<input type="number" min={1} max={168} value={interval} onChange={e=>setInterval(Number(e.target.value))}/></label>
 {!gold&&<>
 <label>编排方式<select value={mode} onChange={e=>setMode(e.target.value)}><option value="ai">本地模型编排</option><option value="baseline">确定性基线</option></select></label>
 {mode==='ai'&&<><label>AI 研究协议<select value={protocol} onChange={e=>setProtocol(e.target.value)}><option value="SINGLE_V1">单次提案 · 每次实验最多 1 次调用</option><option value="THREE_ROLES_V1">提案 → 反证 → 综合 · 最多 3 次调用</option></select></label><p className="f-muted">复用同一本地模型，每次提案总限时 60 秒，每次调用最多输出 400 Token。三角色只提交一个候选；过程对照同时比较两种协议，失败计入预算。</p></>}
 <fieldset><legend>方法来源（最多 8 项）</legend><label>筛选可选来源<input type="search" value={sourceQuery} onChange={e=>setSourceQuery(e.target.value)} placeholder="名称、类别或研究方法"/></label><p className="lf-source-limit">已选择 {sourceIds.length} / 8 · 可从全部 {data?.sources.length??0} 个来源中选择</p><div className="lf-source-choices">{data?.sources.filter(s=>`${s.name} ${s.category} ${s.adopt}`.toLowerCase().includes(sourceQuery.toLowerCase())).map(s=><label key={s.id}><input type="checkbox" disabled={!sourceIds.includes(s.id)&&sourceIds.length>=8} checked={sourceIds.includes(s.id)} onChange={e=>setSourceIds(e.target.checked?[...sourceIds,s.id].slice(0,8):sourceIds.filter(i=>i!==s.id))}/>{s.name}</label>)}</div></fieldset>
 </>}
 <details><summary>研究执行连接</summary><label>操作记录名称<input value={operator} onChange={e=>editOperator(e.target.value)} autoComplete="off"/></label><label>研究令牌<input type="password" value={token} onChange={e=>editToken(e.target.value)} autoComplete="off"/></label></details>
 {gold?<p className="lf-budget">最多 <strong>{collectFirst?collectionBatches:1}</strong> 批，每批 <strong>{cycles}</strong> 轮，共 <strong>{cycles*(collectFirst?collectionBatches:1)}</strong> 次 AI 假设与成本实验；失败计入预算，同一快照内排除已尝试候选。</p>:<p className="lf-budget">最多 <strong>{cycles*budget}</strong> 次主实验，连同三折过程对照最多 <strong>{cycles*budget*multiplier}</strong> 次候选评估。失败消耗尝试，固定压力诊断另计。主实验不重复已尝试规格；历史窗口复用不建立独立证据。</p>}
 <button className="s-button primary" disabled={!canWrite||busy||!data?.scheduler_enabled||!token.trim()||!operator.trim()||(gold?(!snapshot||(collectFirst&&(nextInput>1000||!Number.isInteger(collectionSeconds)||collectionSeconds<15||collectionSeconds>60||!Number.isInteger(collectionBatches)||collectionBatches<1||collectionBatches>6))||cycles<1||cycles>6):(!sourceIds.length||cycles*budget>72||(trigger==='drift'&&!champion)))}>启动持续研究计划</button>
 {(!operator.trim()||!token.trim())&&<p className="f-muted">请在“研究执行连接”中填写操作记录名称与研究令牌。</p>}
 {!data?.scheduler_enabled&&<p>研究服务尚未启用持续调度。</p>}
 </form></Panel><div className="lf-stack">{!!data?.collection_programs?.length&&<Panel title="周期采集与研究" note="失败采集也消耗批次，间隔后继续；暂停和停止阻止后续研究及新批次，已提交的采集可能完成">{data.collection_programs.map(program=><article key={program.root_id}><h3>采集组 #{program.root_id}</h3><Badge value={program.state}/><p>已预留 {program.batches.length} / {program.max_batches} 批 · 计划 {program.batches.map(b=>`#${b.campaign_id}`).join('、')}</p><div className="lf-row"><button className="s-button" disabled={!canWrite||busy||program.state!=='RUNNING'} onClick={()=>void act('pause_program',program.root_id)}>暂停整组</button><button className="s-button" disabled={!canWrite||busy||program.state!=='PAUSED'} onClick={()=>void act('resume_program',program.root_id)}>恢复整组</button><button className="s-button" disabled={!canWrite||busy||!['RUNNING','PAUSED'].includes(program.state)} onClick={()=>void act('stop_program',program.root_id)}>停止整组</button></div></article>)}</Panel>}{!!data?.collection_requests?.length&&<Panel title="采集与计划交接">{data.collection_requests.map(item=><article key={item.campaign_id}><h3>计划 #{item.campaign_id}</h3><p>模板 #{item.request.config.input_id} → 新快照 #{item.request.input_id} · {item.request.seconds} 秒成交采集</p><GoldCollectionStatus rows={[item]} compact/><Link to={view.href('/studio/research')}>查看采集任务及失败原因 →</Link></article>)}</Panel>}<Panel title="计划与预算" note="冷却时间从上一轮结束计算；停止计划不撤销正在执行的研究">
 {data?.campaigns.length?data.campaigns.map(c=>{const rounds=data.rounds.filter(r=>r.campaign_id===c.campaign_id),last=rounds[0];return <article className="lf-campaign" key={c.campaign_id}><div className="lf-row"><h3>计划 #{c.campaign_id}</h3><span className="f-muted">{({RUNNING:'计划进行中',PAUSED:'计划已暂停',STOPPED:'计划已停止',COMPLETED:'计划已完成'} as Record<string,string>)[c.state]??statusLabel(c.state)}</span></div><p>{c.config.market==='XAUUSDT'?'XAUUSDT 黄金合约':'BTC 小时级研究'} · {c.config.objective}</p><progress aria-label={`计划 ${c.campaign_id} 预算预留`} value={rounds.length} max={c.config.max_cycles}/><p>{rounds.length} / {c.config.max_cycles} 轮已预留 · 每轮 {c.config.budget} 次主实验</p><p>调度条件：{({EXECUTION_REVIEW_SCHEDULER_CHECK_REQUIRED:'等待调度器核对执行复盘',ROUND_IN_PROGRESS:'当前轮次处理中',COOLDOWN:'等待冷却',WAITING_MATURE_DIAGNOSTIC:'等待成熟诊断',WAITING_OBSERVATION:'等待观察',STALE_OBSERVATION:'观察已过期',SCHEDULE:'等待队列',BUDGET_EXHAUSTED:'预算已用完',MODEL_UNDERPERFORMS_ZERO_BASELINE:'模型落后基线，准备研究挑战者'} as Record<string,string>)[c.wait_reason]??statusLabel(c.wait_reason)}</p><p>最早下一轮：{c.state!=='RUNNING'?'计划未运行':last?.state==='RESERVED'?'当前轮次处理中':c.next_due_ms!==null?stamp(c.next_due_ms,true):'等待调度'}</p><div className="lf-row"><button className="s-button" disabled={!canWrite||busy||c.state!=='RUNNING'} onClick={()=>void act('pause',c.campaign_id)}>暂停计划</button><button className="s-button" disabled={!canWrite||busy||c.state!=='PAUSED'} onClick={()=>void act('resume',c.campaign_id)}>恢复计划</button><button className="s-button" disabled={!canWrite||busy||!['RUNNING','PAUSED'].includes(c.state)} onClick={()=>void act('stop',c.campaign_id)}>停止计划</button></div></article>}):<Empty title="尚无持续研究计划">先固定目标、来源和预算。系统会保留每轮研究身份及失败记录。</Empty>}
 </Panel><Panel title="轮次轨迹"><div className="s-table-wrap" role="region" aria-label="计划轮次" tabIndex={0}><table className="s-table"><thead><tr><th>计划 / 轮次</th><th>任务</th><th>状态</th><th>创建时间</th></tr></thead><tbody>{data?.rounds.map(r=><tr key={`${r.campaign_id}-${r.ordinal}`}><td>#{r.campaign_id} / {r.ordinal+1}</td><td>{data.campaigns.find(c=>c.campaign_id===r.campaign_id)?.config.market==='XAUUSDT'?(data.gold_results?.some(item=>item.attempt===r.attempt)?<a href={`#gold-result-${r.attempt}`}>黄金研究 #{r.attempt} · 查看结果 →</a>:<Link to={view.href('/studio/research')}>黄金研究 #{r.attempt} · 查看任务 →</Link>):<Link to={view.href('/studio/factory',r.attempt)}>研究 #{r.attempt} →</Link>}</td><td><Badge value={r.state}/></td><td>{stamp(r.created_ms,true)}</td></tr>)}</tbody></table></div></Panel><GoldResearchResults rows={data?.gold_results??[]}/></div></div>}
 {tab===2&&<>
 <Panel title="冻结模型与挑战者" note="先比较自然预测与零收益预测基线；168 条成熟标签只是诊断起点，重叠标签不是独立样本">
 <div className="s-table-wrap" role="region" aria-label="模型生命周期" tabIndex={0}><table className="s-table"><thead><tr><th>模型</th><th>观察状态</th><th>预测 / 标签</th><th>RMSE / 零基线</th><th>下一步</th></tr></thead><tbody>{data?.models.map(m=><tr key={m.attempt}><td><button className="s-button" onClick={()=>setChosenModel(m.attempt)}>#{m.attempt} {m.spec?.model}</button><small>{m.spec?.transform??'原始特征'}</small></td><td><Badge value={m.state}/></td><td>{m.predictions} / {m.labels}</td><td>{fmt(m.diagnostics.rmse,5)} / {fmt(m.diagnostics.zero_baseline_rmse,5)}</td><td>{m.diagnostics.recommendation==='RESEARCH_CHALLENGER'?'建议研究新挑战者':'继续观察'} · <Link to={view.href('/studio/factory',m.attempt)}>观察控制 →</Link></td></tr>)}</tbody></table></div>
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
