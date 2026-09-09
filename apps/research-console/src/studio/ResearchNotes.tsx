import type {FactoryResearchNotes} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {fmt,stamp} from './data';

const decisions:Record<string,string>={STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE:'所选组合没有实际交易样本，停止该方向',STOP_NO_STABLE_NET_INCREMENT:'没有稳定的成本后增量，停止扩搜',STOP_UNRESOLVED_COMPARISON:'比较不完整，先处理失败证据',NATURAL_FORWARD_REQUIRED:'历史比较出现增量，等待自然前向验证',PORTFOLIO_EVIDENCE_MISSING:'尚缺组合证据，请先运行组合研究',INCOMPLETE_EVIDENCE:'证据不足，结论未知'};
const metrics:Record<string,string>={'primary.net_pnl':'原模型外层净损益','primary.fees':'原模型模拟手续费','primary.drawdown':'原模型最大回撤','primary.fills':'原模型成交数',actual_venue_costs:'真实场所成本',portfolio_net_pnl:'组合净损益',baseline_net_pnl:'单模型基线净损益',net_pnl_delta:'组合净增量',portfolio_fills:'组合成交数'};
export function ResearchNotes({data,busy,complete,action}:{data?:FactoryResearchNotes|null;busy:boolean;complete:boolean;action:()=>void}) {
 const verdict=data?.verdict;
 return <Panel title="研究结论与来源" note="数值直接取自冻结实验；研究解释单独展示，外层结果不回流同一次搜索。" action={<button className="s-button" disabled={busy||!complete} onClick={action}>生成 / 更新研究结论</button>}>
 <p role="status">{verdict?decisions[verdict.decision]??'证据待核查':'结论尚未生成，缺失数值保持未知。'}</p>
 {verdict&&<><p>记录于 {stamp(verdict.known_at_ms,true)} · 稳定盈利尚未建立</p>
 <div className="s-table-wrap" role="region" aria-label="研究事实与引用" tabIndex={0}><table className="s-table"><thead><tr><th>事实</th><th>测量值</th><th>证据</th></tr></thead><tbody>{verdict.facts.map(f=>{
  const parts=f.id.split('.');const scenario=parts[0]==='portfolio'?verdict.cost_scenarios[Number(parts[1])]:undefined;
  return <tr key={f.id}><td>{metrics[f.id]??metrics[parts[parts.length-1]??'']??f.id}{scenario&&<small>费用 {scenario.fee_bps} / 滑点 {scenario.slippage_bps} bp</small>}</td><td>{f.value==null?'未知':f.unit==='fraction'?`${fmt(Number(f.value)*100,3)}%`:`${fmt(Number(f.value),f.unit==='count'?0:4)} ${f.unit==='count'?'次':f.unit}`}</td><td>{f.evidence_hash?<details><summary>查看来源</summary><code style={{overflowWrap:'anywhere'}}>{f.evidence_hash}</code><small>{f.pointer}</small></details>:'尚无回执'}</td></tr>;
 })}</tbody></table></div><p className="f-message">模型预测改善、交易改善和稳定净盈利需要分别验证；空仓胜过亏损基线不代表发现主动 Alpha。</p></>}
 <details><summary>研究过程记录 · {data?.history?.episodes??'未记录'} 条</summary>
 <div className="s-table-wrap" role="region" aria-label="研究过程时间线" tabIndex={0}><table className="s-table"><thead><tr><th>结果可知时间</th><th>用途</th><th>候选 / 结果</th><th>关联记录</th></tr></thead><tbody>{data?.episodes.map(e=><tr key={e.episode_id}><td>{stamp(e.known_at_ms,true)}</td><td>{e.scope==='INNER_SEARCH'?'内层搜索':e.scope==='OUTER_EXPLANATION'?'外层只读解释':'自然前向'}</td><td>{e.summary.candidate_id==null?'—':`#${e.summary.candidate_id}`} · {e.summary.status==='PASSED'?'已计算':e.summary.status==='FAILED'?'失败':'结论待验证'}{e.summary.reason&&<small>{e.summary.reason}</small>}</td><td>{e.parent_ids.length} 条前序记录<details><summary>证据标识</summary><code style={{overflowWrap:'anywhere'}}>{e.evidence_hash}</code></details></td></tr>)}</tbody></table></div>
 <p className="f-message">记录只追加，失败保留。搜索按当时已知时间、数据快照和验证分区读取；缺少时间元数据的旧跨实验记忆不参与提案。</p>
 </details>
 {!!data?.role_runs?.length&&<details><summary>完整 AI 调用记录 · 含失败与中断</summary><div className="s-table-wrap" role="region" aria-label="完整角色调用记录" tabIndex={0}><table className="s-table"><thead><tr><th>研究标识</th><th>角色</th><th>结果</th><th>耗时 / 候选</th></tr></thead><tbody>{data.role_runs.flatMap(run=>run.calls.map(call=><tr key={`${run.definition}-${call.role}`}><td>{run.definition.slice(0,12)}</td><td>{({PROPOSE:'提案',COUNTER_PROPOSE:'反证',SYNTHESIZE:'综合'} as Record<string,string>)[call.role]??call.role}</td><td>{call.status==='PASSED'?'已完成':call.status==='FAILED'?'失败':'已预留，尚无回执'}{call.reason&&<small>{call.reason}</small>}</td><td>{call.elapsed_ms??'—'} ms / {call.candidate_id==null?'未选定':`#${call.candidate_id}`}</td></tr>))}</tbody></table></div></details>}
 </Panel>;
}
