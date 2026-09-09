import type {PortfolioResearch as Evidence,PortfolioCandidate} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {Chart} from './Charts';
import {fmt,stamp} from './data';

const decisions:Record<string,string>={STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE:'缺少实际交易样本，停止该方向',STOP_NO_STABLE_NET_INCREMENT:'未形成稳定的成本后增量，停止扩搜',STOP_UNRESOLVED_COMPARISON:'比较未完整完成，请查看失败记录',NATURAL_FORWARD_REQUIRED:'历史比较出现增量，仍需自然前向验证'};
const stages:Record<string,string>={INNER_FIT:'计算内层模型预测',INNER_SEARCH:'比较有限权重',OUTER_COMPARISON:'权重已冻结，计算外层成本对照'};

export function PortfolioResearch({data,busy,complete,action}:{data?:Evidence|null;busy:boolean;complete:boolean;action:()=>void}) {
 const label=(spec?:PortfolioCandidate)=>!spec?'未选定':spec.quarters.every(w=>w===0)?'空仓':spec.quarters.map((w,i)=>w?`${data?.components?.[i]?.model??`模型 #${spec.component_ids[i]}`} ${w*25}%`:null).filter(Boolean).join(' + ');
 const cases=data?.outer_cases??[];
 const portfolio=cases.find(c=>c.name==='portfolio'&&c.fee_bps===4&&c.slippage_bps===5&&c.status==='PASSED');
 const baseline=cases.find(c=>c.name==='single_baseline'&&c.fee_bps===4&&c.slippage_bps===5&&c.status==='PASSED');
 const baselineByTime=new Map(baseline?.metrics?.equity?.map(r=>[r.time,Number(r.equity)]));
 const active=data?.status==='RUNNING'&&data.observed_at_ms!==undefined&&Date.now()-data.observed_at_ms<180000;
 return <Panel title="组合内层选择与整体回放" note="最多三个组件，四分之一权重网格及空仓基线。权重仅由内层选择；各情景共用相同资金、风险和成本口径。" action={<button className="s-button" disabled={busy||!complete||active||data?.status==='COMPLETED_TECHNICAL_ONLY'} onClick={action}>{data?'恢复组合研究':'运行组合研究'}</button>}>
 {!data?<p className="f-message">完成主研究后，组合阶段自动运行；已有实验也可单独启动。此处评估整个共享账户，全部尝试和失败均保留。</p>:<>
 <p role="status">{data.status==='RUNNING'?stages[data.stage??'']??'组合研究进行中':data.status==='COMPLETED_TECHNICAL_ONLY'?'组合研究已计算完成':'组合研究异常'}{data.decision?` · ${decisions[data.decision]??data.decision}`:''}</p>
 {data.reason&&<p className="f-message" role="alert">{data.reason} · 已有尝试保留，可恢复尚未执行的步骤。</p>}
 <div className="f-metrics"><div><small>内层所选组合</small><strong style={{fontSize:14}}>{label(data.selected)}</strong></div><div><small>内层单模型基线</small><strong style={{fontSize:14}}>{label(data.baseline)}</strong></div><div><small>已登记权重尝试</small><strong>{data.inner_trials_attempted??'未记录'}</strong></div></div>
 <div className="s-table-wrap" role="region" aria-label="组合内层尝试" tabIndex={0}><table className="s-table"><thead><tr><th>权重方案</th><th>内层净损益 USDC</th><th>最大回撤</th><th>成交数</th><th>结果</th></tr></thead><tbody>{data.inner_trials?.map(r=><tr key={r.spec.id}><td>{label(r.spec)}{r.spec.id===data.selected?.id?' · 已选':''}</td><td>{fmt(r.metrics?.net_pnl,4)}</td><td>{r.metrics?.drawdown==null?'未知':`${fmt(r.metrics.drawdown*100)}%`}</td><td>{r.metrics?.fills??'未知'}</td><td>{r.status==='PASSED'?'已计算':`失败 · ${r.reason??'原因未记录'}`}</td></tr>)}</tbody></table></div>
 <div className="s-table-wrap" role="region" aria-label="组合外层成本比较" tabIndex={0}><table className="s-table"><thead><tr><th>费用 / 滑点 bp</th><th>组合净损益 USDC</th><th>单模型净损益 USDC</th><th>净增量 USDC</th><th>组合回撤</th><th>组合成交</th></tr></thead><tbody>{data.comparisons?.map(c=><tr key={`${c.fee_bps}-${c.slippage_bps}`}><td>{c.fee_bps} / {c.slippage_bps}</td><td>{fmt(c.portfolio_net_pnl,4)}</td><td>{fmt(c.baseline_net_pnl,4)}</td><td>{fmt(c.net_pnl_delta,4)}</td><td>{fmt(c.portfolio_drawdown*100)}%</td><td>{c.portfolio_fills}</td></tr>)}</tbody></table></div>
 {portfolio?.metrics?.equity&&baseline?.metrics?.equity&&<Chart title="整体账户与单模型 · 同窗口净值" rows={portfolio.metrics.equity.map(r=>({time:r.time,label:stamp(r.time,true),portfolio:Number(r.equity),baseline:baselineByTime.get(r.time)}))} series={[{key:'portfolio',label:'冻结组合'},{key:'baseline',label:'单模型基线'}]} unit="USDC" note="各自从 1,500 USDC 开始；费用 4 bp、滑点 5 bp；曲线来自账户账本。"/>}
 <p className="f-message">已使用历史的技术比较；空仓或相互抵消不代表发现 Alpha。这里合并预测并统一决定持仓，尚不代表独立策略分账户运行或真实成交校准。</p>
 {data.timings&&<p>预测准备耗时：内层 {data.timings.inner_prediction_ms==null?'未知':fmt(data.timings.inner_prediction_ms/1000,2)} 秒 · 外层 {data.timings.outer_prediction_ms==null?'未知':fmt(data.timings.outer_prediction_ms/1000,2)} 秒。相同冻结预测复用缓存。</p>}
 </>}
 </Panel>;
}
