import type {ProfitabilityStudy as Study} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {ProfitabilityAttribution} from './ProfitabilityAttribution';
import {fmt} from './data';

export function ProfitabilityStudy({data,busy,complete,action}:{data?:Study|null;busy:boolean;complete:boolean;action:()=>void}) {
 const p1=data?.p1;
 const policies:Record<string,string>={BASELINE_COST_UTILITY:'原成本效用',BAND_20_20:'20 bp 进出',HYSTERESIS_20_5:'20 / 5 bp 进出',HYSTERESIS_30_10:'30 / 10 bp 进出'};
 const stops:Record<string,string>={STOP_NO_INNER_INCREMENT:'未见内层增量',STOP_NO_ACTIVE_OUTER_EVIDENCE:'外层没有成交证据',STOP_COST_UNSTABLE_INCREMENT:'部分成本情景下增量不稳定'};
 return <>
 <Panel title="盈利验证 · 有限实验" note="依次运行归因、四种持仓规则与有限等待，采集同步价格；联合面板或轨迹到位时才运行对应诊断" action={<button className="s-button" disabled={busy||!complete||!!data?.failures.length} onClick={action}>{data?'检查后续输入':'运行盈利验证'}</button>}>
 <p className="f-message">{p1?`内层选定 ${policies[p1.selected_policy]??p1.selected_policy} · 已记录 ${p1.trials_attempted} 次尝试。${p1.decision.startsWith('STOP_')?`停止扩搜：${stops[p1.decision]??p1.decision}。`:'保持规格冻结，等待自然前向；不代表已经盈利。'}`:'每个冻结实验保留一份独立验证记录。中断或失败保留，不用反复重跑覆盖结果。'}</p>
 {data?.failures.map(f=><p key={f.stage} role="status">{f.stage.toUpperCase()}：{f.reason}。请检查实验记录。</p>)}
 {p1&&<div className="s-table-wrap" role="region" aria-label="成本后持仓政策比较" tabIndex={0}><table className="s-table"><thead><tr><th>政策</th><th>费用 / 滑点 bp</th><th>净收益 USDC</th><th>回撤</th><th>成交数</th><th>换手 / 初始权益</th></tr></thead><tbody>{p1.cases.map(r=><tr key={`${r.policy}-${r.fee_bps}-${r.slippage_bps}`}><td>{policies[r.policy]??r.policy}</td><td>{r.fee_bps} / {r.slippage_bps}</td><td>{fmt(r.metrics.net_pnl)}</td><td>{fmt(r.metrics.drawdown===undefined?undefined:r.metrics.drawdown*100)}%</td><td>{r.metrics.fills??'—'}</td><td>{r.metrics.execution_feedback?.profitability_attribution?.turnover_initial_equity??'—'}</td></tr>)}</tbody></table></div>}
 <p className="f-message">同数据、同预测、同风险预算。参数仅在内层选择；此处历史已被使用，不重新声称为未见样本。真实费用与独立盈利结论仍待验证。</p>
 </Panel>
 {data?.p2&&<Panel title="交易时机 · 立即执行与有限等待" note="最多等待一小时，风险减仓即时执行；总收益差额包含错失行情及仓位变化">
 <p className="f-message">{data.p2.decision==='STOP_NO_TIMING_OPPORTUNITIES'?'没有可验证的等待机会，停止该方向扩搜。':'配对诊断已形成，规格冻结等待前向验证。'}</p>
 <div className="s-table-wrap" role="region" aria-label="有限等待配对结果" tabIndex={0}><table className="s-table"><thead><tr><th>费用 / 滑点 bp</th><th>等待次数</th><th>超时次数</th><th>净收益差额</th><th>毛收益 / 库存机会差额</th></tr></thead><tbody>{data.p2.pairs.map(r=><tr key={`${r.fee_bps}-${r.slippage_bps}`}><td>{r.fee_bps} / {r.slippage_bps}</td><td>{r.effect.wait_decisions??'—'}</td><td>{r.effect.timeouts??'—'}</td><td>{r.effect.delta_wait_minus_immediate?.net_pnl??'未知'}</td><td>{r.effect.delta_wait_minus_immediate?.gross_reference_pnl??'未知'}</td></tr>)}</tbody></table></div>
 </Panel>}
 {data?.p0&&<ProfitabilityAttribution data={data.p0.metrics.execution_feedback?.profitability_attribution}/>}
 </>;
}
