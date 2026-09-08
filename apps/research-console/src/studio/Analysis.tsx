import {useState} from 'react';
import {Link} from '@/lib/router';
import {fetchMarketDashboard,fetchTechnicalReplay,fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import {CONSOLE_MODULES} from '@/workbench/module-registry';
import type {RunsState} from './Research';
import type {ServiceHealth} from '@/lib/types/research';
import {OperationalHealth} from './OperationalHealth';
import {Chart,Heatmap,type ChartRow} from './Charts';
import {analyzeEquity,correlation,histogram} from './analytics';
import {Badge,Empty,ErrorNote,Header,Loading,Metric,Panel} from './components';
import {fmt,KIND_LABELS,stamp,useResource} from './data';
const date=(ms:number)=>new Date(ms).toISOString().slice(0,16).replace('T',' ');
const daily=(points:{time:number;value:number}[])=>points.map(p=>({label:date(p.time).slice(0,10),time:p.time,value:p.value}));

export function Market() {
 const [symbol,setSymbol]=useState('BTCUSDT'),[paused,setPaused]=useState(false);
 const resource=useResource('market:'+symbol,s=>fetchMarketDashboard(symbol,s),paused?0:10000);
 const data=resource.error?null:resource.data;
 const candles=data?.candles.map(p=>({...p,label:date(p.time).slice(5)}))??[];
 const last=candles[candles.length-1];const latestSample=data?.samples[data.samples.length-1];const sample=data&&latestSample&&data.as_of_ms-latestSample.time<=30000?latestSample:undefined;
 const samples=data?.samples.map(p=>({...p,label:date(p.time).slice(5),funding:p.funding_rate*10000,basis:(p.mark/p.index-1)*10000}))??[];
 const depth=(side:'bids'|'asks')=>{let cumulative=0;return (data?.depth?.[side]??[]).map(([price,quantity])=>({label:fmt(price),price,quantity,cumulative:cumulative+=quantity}));};
 return <><Header eyebrow="MARKET / PUBLIC DATA" title="行情终端" action={<div className="s-controls"><select aria-label="行情标的" value={symbol} onChange={e=>setSymbol(e.target.value)}>{(data?.symbols??['BTCUSDT','BTCUSDC','ETHUSDT','SOLUSDT']).map(s=><option key={s}>{s}</option>)}</select><button className="s-button" onClick={()=>setPaused(p=>!p)}>{paused?'恢复刷新':'暂停刷新'}</button><button className="s-button" onClick={resource.refresh}>刷新</button></div>}>公开永续市场 · {symbol} · UTC时间 · 历史缺口不补画为真实行情。</Header>
 <ErrorNote error={resource.error} retry={resource.refresh}/>{resource.loading&&!data?<Loading/>:<><div className="s-metrics"><Metric label="最近完整分钟收盘" value={fmt(last?.close)} note={last?.label??'尚无完整K线'}/><Metric label="标记价格" value={data?.status==='AVAILABLE'?fmt(sample?.mark):'—'} note={data?.status==='AVAILABLE'?'公开市场观测':'当前报价不可用'}/><Metric label="资金费率" value={data?.status==='AVAILABLE'?fmt(sample?sample.funding_rate*10000:null)+' bp':'—'} note="单期费率，不代表已入账现金流"/><Metric label="数据状态" value={data?.status??'UNAVAILABLE'} note={data?.observed_at_ms?stamp(data.observed_at_ms,true):'该标的尚无采集记录'}/></div>
 <Chart title={symbol+' · 分钟K线'} rows={candles} series={['open','high','low','close'].map((key,i)=>({key,label:['开','高','低','收'][i]}))} kind="candle" unit={symbol.endsWith('USDC')?'USDC':'USDT'} note="仅显示已收盘分钟；暂停只停止自动请求。"/>
 <Chart title="成交量" rows={candles} series={[{key:'volume',label:'基础币成交量'}]} kind="bar"/>
 <div className="s-analytics-grid"><Chart title="标记与指数" rows={samples} series={[{key:'mark',label:'标记价'},{key:'index',label:'指数价'}]}/><Chart title="标记溢价" rows={samples} series={[{key:'basis',label:'标记 / 指数 − 1'}]} unit="bp"/><Chart title="资金费率观察" rows={samples} series={[{key:'funding',label:'资金费率'}]} unit="bp"/><Panel title="盘口状态" note="仅当前、连续的公开深度可用于展示"><Badge value={data?.depth?'REAL':'UNAVAILABLE'}/><p className="s-pad">{data?.depth?'下方累计数量来自保留的20档快照。':'盘口过期、断流或缺失时隐藏；不会从K线推导订单簿。'}</p></Panel><Chart title="买盘累计深度" rows={depth('bids')} series={[{key:'cumulative',label:'累计基础币数量'}]} kind="bar"/><Chart title="卖盘累计深度" rows={depth('asks')} series={[{key:'cumulative',label:'累计基础币数量',color:'#c34b55'}]} kind="bar"/></div></>}
 </>;
}

export function Analysis({runs,initialRun=''}:{runs:RunsState;initialRun?:string}) {
 const [chosen,setChosen]=useState(initialRun),[policy,setPolicy]=useState('FACTORY'),[slip,setSlip]=useState(5),[stop,setStop]=useState(true);
 const choices=runs.rows.filter(r=>r.kind==='btc_factory'&&r.status==='done');const run=choices.find(r=>r.run_id===chosen)??choices[0];
 const result=useResource('analysis:'+run?.run_id,s=>run?fetchTechnicalReplay(run.run_id,s):Promise.resolve(null));
 const report=result.data?.technical_replay?.factory_report;
 const selected=report?.cases.find(c=>c.policy===policy&&c.slip_bps===slip&&c.stop===stop);
 const points=(selected?.equity??[]).map(p=>({time:p.time,value:Number(p.equity)}));const stats=analyzeEquity(points);
 const policies=[...new Set(report?.cases.map(c=>c.policy)??[])];
 const comparable=policies.map(name=>report?.cases.find(c=>c.policy===name&&c.slip_bps===slip&&c.stop===stop));
 const returns=comparable.map(c=>analyzeEquity((c?.equity??[]).map(p=>({time:p.time,value:Number(p.equity)}))).returns);
 const months=[...new Set(stats.monthly.map(p=>p.label.slice(0,4)))];
 const rejected=Object.entries(selected?.metrics?.order_reasons??{}).map(([label,value])=>({label,value}));
 return <><Header eyebrow="RESEARCH / ANALYTICS" title="研究分析" action={run&&<Link className="s-button" to={'/research/runs/'+run.run_id}>查看原始运行</Link>}>成本后的技术结果、风险和政策对照。图表不会赋予结果经济权威。</Header><ErrorNote error={runs.error??result.error} retry={()=>{runs.refresh();result.refresh();}}/>
 <Panel title="选择实验与情景"><div className="s-analysis-controls"><label>工厂运行<select aria-label="分析运行" value={run?.run_id??''} onChange={e=>setChosen(e.target.value)}><option value="" disabled>选择运行</option>{choices.map(r=><option key={r.run_id} value={r.run_id}>实验 {String(r.params.attempt)} · {stamp(r.created_ts)}</option>)}</select></label><label>政策<select aria-label="分析政策" value={policy} onChange={e=>setPolicy(e.target.value)}>{policies.map(p=><option key={p}>{p}</option>)}</select></label><label>滑点<select aria-label="分析滑点" value={slip} onChange={e=>setSlip(Number(e.target.value))}>{[2,5,10].map(n=><option key={n} value={n}>{n} bp</option>)}</select></label><label className="s-checkbox"><input type="checkbox" checked={stop} onChange={e=>setStop(e.target.checked)}/>启用亏损停机</label></div></Panel>
 {result.loading&&!report?<Loading/>:!points.length?<Empty title="所选情景没有完整技术权益">请选择已经完成的工厂运行；失败情景不会填零。</Empty>:<>
 <div className="s-metrics"><Metric label="区间收益" value={fmt(stats.totalReturn)+'%'} note="相对首个观测权益"/><Metric label="最大回撤" value={fmt(stats.maxDrawdown)+'%'} note="相对历史观测峰值"/><Metric label="年化波动率" value={fmt(stats.annualVol)+(stats.annualVol===null?'':'%')} note="仅连续日频；365天"/><Metric label="描述性 Sharpe" value={fmt(stats.sharpe)} note="仅连续日频；零基准，不是准入检验"/></div>
 <Chart title="技术权益" rows={daily(stats.points)} series={[{key:'value',label:'净权益'}]} unit="USDC"/>
 <div className="s-analytics-grid"><Chart title="回撤水下图" rows={daily(stats.drawdown)} series={[{key:'value',label:'回撤',color:'#c34b55'}]} unit="%"/><Chart title="逐观测收益" rows={daily(stats.returns)} series={[{key:'value',label:'收益'}]} kind="bar" unit="%" note={stats.regularDaily?'连续日频':'存在间隔；逐观测收益不能当日收益'}/><Chart title="收益分布" rows={histogram(stats.returns.map(p=>p.value)).map(b=>({label:fmt(b.low)+'～'+fmt(b.high),value:b.count}))} series={[{key:'value',label:'观测次数'}]} kind="bar"/><Chart title="20日滚动年化波动" rows={daily(stats.rollingVol)} series={[{key:'value',label:'波动率'}]} unit="%" note="缺日或样本不足时不计算"/></div>
 <Heatmap title="月度观测收益" columns={Array.from({length:12},(_,i)=>String(i+1)+'月')} rows={months} values={months.map(year=>Array.from({length:12},(_,i)=>stats.monthly.find(p=>p.label===year+'-'+String(i+1).padStart(2,'0'))?.value??null))} unit="%，首尾月可能是不完整月份"/>
 <div className="s-analytics-grid"><Chart title="持仓与敞口" rows={(selected?.equity??[]).map(p=>({label:date(p.time).slice(0,10),value:Number(p.quantity)*Number(p.mark)}))} series={[{key:'value',label:'有符号名义敞口'}]} unit="USDC"/><Chart title="费用与资金费" rows={selected?.metrics?[{label:'手续费',value:Number(selected.metrics.fees)},{label:'资金费现金流',value:Number(selected.metrics.funding)}]:[]} series={[{key:'value',label:'账本金额'}]} kind="bar" unit="USDC"/><Chart title="订单决策原因" rows={rejected} series={[{key:'value',label:'事件数'}]} kind="bar"/><Chart title="政策净收益对照" rows={comparable.map((c,i)=>({label:policies[i],value:c?.metrics?.net_pnl}))} series={[{key:'value',label:'净收益'}]} kind="bar" unit="USDC"/></div>
 <Heatmap title="政策收益相关性" columns={policies} rows={policies} values={returns.map(a=>returns.map(b=>correlation(a,b)))} unit="只配对共同观测时间；常量或少于3个样本不计算"/>
 <Heatmap title="成本与风险情景" columns={['2bp · 停机','5bp · 停机','10bp · 停机','2bp · 无停机','5bp · 无停机','10bp · 无停机']} rows={policies} values={policies.map(p=>[true,false].flatMap(halt=>[2,5,10].map(cost=>report?.cases.find(c=>c.policy===p&&c.stop===halt&&c.slip_bps===cost)?.metrics?.net_pnl??null)))} unit="技术净收益 USDC；失败情景保留缺失"/>
 </>}
 </>;
}

export function Models({runs}:{runs:RunsState}) {
 const [chosen,setChosen]=useState('');const choices=runs.rows.filter(r=>r.status==='done'&&['btc_mechanism','btc_forward','perpetual_alpha','orderblock_research'].includes(r.kind));
 const run=choices.find(r=>r.run_id===chosen)??choices[0];const result=useResource('models:'+run?.run_id,s=>run?fetchTechnicalReplay(run.run_id,s):Promise.resolve(null));const report=result.data?.technical_replay;
 return <><Header eyebrow="MODELS / DIAGNOSTICS" title="模型诊断">查看模型误差、信息增量、前向成熟度和实际计算结果。</Header><ErrorNote error={runs.error??result.error} retry={()=>{runs.refresh();result.refresh();}}/><Panel title="模型实验"><select className="s-wide-select" aria-label="模型实验" value={run?.run_id??''} onChange={e=>setChosen(e.target.value)}><option value="" disabled>选择实验</option>{choices.map(r=><option key={r.run_id} value={r.run_id}>{KIND_LABELS[r.kind]} · {String(r.params.attempt)} · {stamp(r.created_ts)}</option>)}</select></Panel>
 {result.loading&&!report?<Loading/>:!report?<Empty title="尚无可读模型结果"/>:<><div className="s-analytics-grid">{Object.entries(report.series).map(([name,values])=><Chart key={name} title={name} rows={values.map(p=>({label:p.label,value:p.value}))} series={[{key:'value',label:name}]} kind="bar" unit={name.includes('bps')?'bp':name.includes('r2')?'fraction':''}/>)}{report.forward_report&&<><Chart title="前向样本成熟度" rows={report.forward_report.models.map(m=>({label:m.model,completed:m.completed,pending:m.pending}))} series={[{key:'completed',label:'已成熟'},{key:'pending',label:'待成熟'}]} kind="bar"/><Chart title="冻结模型 RMSE" rows={report.forward_report.models.map(m=>({label:m.model,value:m.rmse_bps??undefined}))} series={[{key:'value',label:'RMSE'}]} kind="bar" unit="bp"/></>}</div>{!Object.keys(report.series).length&&!report.forward_report&&<Empty title="此实验尚未提供可绘制诊断序列"/>}<Panel title="解释与限制">{report.caveats.map((c,i)=><p className="s-pad" key={i}>{c}</p>)}</Panel></>}
 </>;
}

export function Operations({runs}:{runs:RunsState}) {
 const result=useResource('operations',s=>Promise.all(['system-health','data-catalog','agents'].map(id=>fetchConsoleModuleSnapshot<Record<string,unknown>>(CONSOLE_MODULES.find(m=>m.id===id)!.endpoint,CONSOLE_MODULES,s))),15000);
 const health=result.error?undefined:result.data?.[0],catalog=result.error?undefined:result.data?.[1];const services=Array.isArray(health?.services)?health.services as ServiceHealth[]:[];
 const market=catalog?.binance_realtime as Record<string,unknown>|undefined;const stream=market?.stream as Record<string,unknown>|undefined;
 const events=stream?.event_counts as Record<string,unknown>|undefined;
 const chartEvents:ChartRow[]=Object.entries(events??{}).filter(([,n])=>typeof n==='number').map(([label,value])=>({label,value:value as number}));
 return <><Header eyebrow="OPERATIONS / CONTINUOUS RESEARCH" title="运行监控" action={<button className="s-button" onClick={result.refresh}>刷新状态</button>}>服务、公开采集、任务队列和异常原因；每15秒更新。</Header><ErrorNote error={result.error??runs.error} retry={()=>{result.refresh();runs.refresh();}}/>
 <OperationalHealth services={services}/>
 <div className="s-analytics-grid"><Chart title="研究任务状态" rows={runs.available?['queued','running','done','error','cancelled'].map(status=>({label:status,value:runs.rows.filter(r=>r.status===status).length})):[]} series={[{key:'value',label:'登记数量'}]} kind="bar"/><Chart title="研究类型分布" rows={runs.available?Object.entries(KIND_LABELS).map(([kind,label])=>({label,value:runs.rows.filter(r=>r.kind===kind).length})):[]} series={[{key:'value',label:'运行数'}]} kind="bar"/>
 <Chart title="公开流事件累计" rows={result.error?[]:chartEvents} series={[{key:'value',label:'事件数'}]} kind="bar" note="来自采集器计数；不是成交量或下单数"/><Panel title="公开数据新鲜度"><dl className="s-facts"><div><dt>流连接</dt><dd>{stream?.effective_streaming===true?'正常':'未确认'}</dd></div><div><dt>快照年龄</dt><dd>{fmt(stream?.age_ms,0)} ms</dd></div><div><dt>端到端延迟</dt><dd>{fmt(stream?.lag_ms,0)} ms</dd></div><div><dt>拒绝帧</dt><dd>{fmt(stream?.rejected_frames,0)}</dd></div></dl><Link className="s-text-link s-pad" to="/studio/modules/data-catalog">查看完整数据源诊断 →</Link></Panel></div>
 <div className="s-controls"><Link className="s-button" to="/studio/modules/agents">研究 Agent 状态</Link><Link className="s-button" to="/studio/modules/events">审计事件</Link><Link className="s-button" to="/studio/modules/incident-response">异常处理</Link><Link className="s-button" to="/studio/system">连接设置</Link></div>
 </>;
}
