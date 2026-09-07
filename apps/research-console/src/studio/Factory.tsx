import {useEffect,useRef,useState} from 'react';
import {fetchFactory,controlFactory} from '@/lib/api/ai-factory-api';
import {getOperator,getRunnerToken,setOperator,setRunnerToken} from '@/lib/api/research-runner-api';
import {Header,Panel,Empty,ErrorNote,Badge} from './components';
import {Chart} from './Charts';
import {useResource,fmt,stamp,download,statusLabel} from './data';
import './factory.css';
import {FactoryEvidence} from './FactoryEvidence';

const STAGES:Record<string,number>={READY:0,VALIDATING_INPUT:0,RUNNING:1,PAUSED:1,OUTER_VALIDATION:2,COMPLETED:3};
export function Factory() {
 const [selected,setSelected]=useState<number|null>(null),[mode,setMode]=useState('baseline'),[budget,setBudget]=useState(3),[attempt,setAttempt]=useState(1);
 const [objective,setObjective]=useState('研究 BTC 小时级价格动量与主动成交量信息，比较规则、Ridge 和 GBM，评估费用后的稳定性。');
 const [operator,editOperator]=useState(getOperator),[token,editToken]=useState(getRunnerToken),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const pending=useRef<{body:string;id:string}|null>(null);
 const resource=useResource(`factory:${selected}`,signal=>fetchFactory(selected,signal),10000);
 const assigned=useRef(false);
 useEffect(()=>{if(resource.data&&!assigned.current){assigned.current=true;setAttempt(Math.min(1000,Math.max(0,...resource.data.sessions.map(s=>s.attempt))+1));}},[resource.data]);
 const data=resource.data,session=data?.selected,trials=data?.trials??[],result=session?.result;
 const run=async(action:string)=>{
  const request=action==='create'?{action,attempt,mode,budget,objective}:{action,attempt:session?.attempt};
  const body=JSON.stringify(request);if(pending.current?.body!==body)pending.current={body,id:crypto.randomUUID()};
  setBusy(true);setMessage('');setOperator(operator.trim());setRunnerToken(token.trim());
  try {const response=await controlFactory(request,pending.current.id);setSelected(response.session.attempt);setMessage(action==='pause'?'已请求暂停，当前实验完成后停止。':action==='cancel'?'已请求取消，已完成实验将保留。':'请求已提交，进度会自动更新。');pending.current=null;resource.refresh();}
  catch(e){setMessage(e instanceof Error?e.message:'请求失败');}finally{setBusy(false);}
 };
 const terminal=!!session&&['COMPLETED','CANCELLED'].includes(session.state);
 const curve=result?.outer?.equity??[];
 return <>
 <Header eyebrow="AI RESEARCH FACTORY / BTC" title="从研究想法，到可复现的实验" action={<button className="s-button" onClick={()=>download('quantos-factory.json',data)} disabled={!data}>导出研究记录</button>}>
  设定目标和实验预算，工厂依次训练、验证并保存每一次尝试。模型提案与实际实验使用同一条执行链。
 </Header>
 <ErrorNote error={resource.error} retry={resource.refresh}/>
 <div className="f-provider"><div><strong>{data?.provider.status==='CONFIGURED_UNVERIFIED'?'模型接口已配置 · 连接未验证':'AI 模型尚未配置'}</strong><p>{data?.provider.model||'先运行基线研究；完成服务端接入后再启用 AI 编排。'}</p></div><span className="s-badge">研究模式 · 无交易授权</span></div>
 <div className="f-layout"><div>
 <Panel title="创建研究任务" note="每次任务固定数据、模型范围与预算">
 <form className="f-form" onSubmit={e=>{e.preventDefault();void run('create');}}>
 <label>研究目标<textarea value={objective} onChange={e=>setObjective(e.target.value)} maxLength={2000} required rows={4}/></label>
 <div className="f-fields"><label>任务编号<input type="number" min={1} max={1000} value={attempt} onChange={e=>{assigned.current=true;setAttempt(Number(e.target.value));}} required/></label><label>实验预算<select value={budget} onChange={e=>setBudget(Number(e.target.value))}>{[3,6,9,12,18].map(n=><option key={n} value={n}>{n} 次实验</option>)}</select></label></div>
 <label>编排方式<select value={mode} onChange={e=>setMode(e.target.value)}><option value="baseline">基线研究 · 无需语言模型</option><option value="ai" disabled={data?.provider.status!=='CONFIGURED_UNVERIFIED'}>AI 编排 · 模型选择下一次实验</option></select></label>
 <details><summary>研究执行连接</summary><label>操作记录名称<input value={operator} onChange={e=>editOperator(e.target.value)} autoComplete="off"/></label><label>研究执行令牌<input type="password" value={token} onChange={e=>editToken(e.target.value)} autoComplete="off"/></label><p>使用现有研究服务令牌；这里不接收模型或交易所密钥。</p></details>
 <p className="f-muted">实验完成后自动运行固定压力与组合诊断，并启用自然时间预测观察。</p>
 <button className="s-button primary" disabled={busy||!operator.trim()||!token.trim()}>启动研究工厂</button>
 </form></Panel>
 <Panel title="任务历史"><div className="f-history">{data?.sessions.map(s=><button className={s.attempt===session?.attempt?'selected':''} key={s.attempt} onClick={()=>setSelected(s.attempt)}><span>#{s.attempt} · {s.config.mode==='ai'?'AI 编排':'基线研究'}</span><Badge value={s.state}/><small>{s.config.objective}</small></button>)}{!data?.sessions.length&&<p>尚无研究任务。创建后，记录将在此持续保留。</p>}</div></Panel>
 </div><div>
 <Panel title={session?`任务 #${session.attempt}`:'实验控制台'} note={session?.config.objective??'等待启动第一个研究任务'}>
 {session?<><div className="f-toolbar"><Badge value={session.state}/><span>{trials.length} / {session.config.budget} 次实验</span><button className="s-button" disabled={busy||terminal||session.command==='pause'} onClick={()=>void run('pause')}>暂停</button><button className="s-button" disabled={busy||terminal||!['PAUSED','FAILED'].includes(session.state)} onClick={()=>void run('resume')}>恢复</button><button className="s-button" disabled={busy||terminal||session.command==='cancel'} onClick={()=>void run('cancel')}>取消</button></div>
 <progress aria-label="实验预算完成进度" value={trials.length} max={session.config.budget}/>
 <div className="f-stages">{['固定输入','训练与内层比较','外层验证','保存模型'].map((name,i)=><div key={name} className={(i<=(STAGES[session.state]??0))?'reached':''}><span>0{i+1}</span>{name}</div>)}</div>
 <div className="f-metrics"><div><small>完成实验</small><strong>{trials.filter(t=>t.status==='PASSED').length}<em> / {trials.length}</em></strong></div><div><small>研究比较模型</small><strong>{result?.selected?.model.toUpperCase()??'—'}</strong></div><div><small>外层净损益 · USDC</small><strong>{fmt(result?.outer?.net_pnl)}</strong></div><div><small>外层最大回撤</small><strong>{result?.outer?.drawdown===undefined?'—':`${fmt(result.outer.drawdown*100)}%`}</strong></div></div>
 {result?.inner&&result.inner.score<=0&&<p className="f-message" role="status">内层风险调整净效用未超过空仓基线。该模型仅用于研究观察，没有已证明的可交易优势。</p>}
 {result?.reason&&<p role="alert">任务未完成：{result.reason}</p>}
 </>:<Empty title="让每个研究想法留下证据">先用 3 次实验比较规则、Ridge 与 GBM。不会生成占位收益或伪造 AI 结果。</Empty>}
 <p role="status" className="f-message">{message}</p>
 </Panel>
 <Chart title="外层技术验证 · 账户权益" rows={curve.map(r=>({label:stamp(r.time,true),time:r.time,equity:Number(r.equity)}))} series={[{key:'equity',label:'技术回放权益'}]} unit="USDC" note="真实账本技术回放；包含费用、资金费与风险约束，历史接收时间未知；历史外层窗口可能被不同任务重复使用，不建立独立经济证据。"/>
 <Panel title="实验对比" note="内层结果用于模型选择；外层结果不回传给模型">
 <div className="s-table-wrap" role="region" aria-label="实验对比表格" tabIndex={0}><table className="s-table"><thead><tr><th>实验</th><th>模型</th><th>执行结果</th><th>净损益</th><th>回撤</th><th>预测 RMSE</th></tr></thead><tbody>{trials.map(t=><tr key={t.ordinal}><td>#{t.ordinal+1}</td><td>{t.spec?.model.toUpperCase()??'—'}</td><td><Badge value={t.status==='PASSED'?'done':t.status}/>{t.reason&&<small>{t.reason}</small>}</td><td>{fmt(t.metrics?.net_pnl)}</td><td>{t.metrics?.drawdown===undefined?'—':`${fmt(t.metrics.drawdown*100)}%`}</td><td>{fmt(t.rmse,6)}</td></tr>)}</tbody></table></div>{!trials.length&&<p className="f-muted">实验开始后自动显示结果。</p>}
 </Panel>
 <FactoryEvidence data={data} busy={busy||!operator.trim()||!token.trim()} action={name=>void run(name)}/>
 <Panel title="运行事件" note="包括暂停、失败和操作记录"><ol className="f-events" aria-label="运行事件列表" tabIndex={0}>{data?.events.slice(0,20).map(e=><li key={e.seq}><time>{stamp(e.at,true)}</time><strong>{({TRIAL:'实验已记录',CREATED:'任务已创建',CONTROL:'控制请求',OPERATOR_ACTION:'操作记录'} as Record<string,string>)[e.kind]??statusLabel(e.kind)}</strong><span>{typeof e.payload.reason==='string'?e.payload.reason:typeof e.payload.action==='string'?e.payload.action:''}</span></li>)}</ol></Panel>
 </div></div></>;
}
