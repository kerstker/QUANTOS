import type {ProfitabilityStudy,ForwardProfitability} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {fmt,stamp} from './data';

export function ProfitabilityReadiness({study,forward}:{study?:ProfitabilityStudy|null;forward?:ForwardProfitability|null}) {
 return <Panel title="新信息与前向证据" note="数据验收、研究结果与盈利结论分开显示；缺口不按功能数量抵扣">
 <div className="s-table-wrap" role="region" aria-label="盈利验证证据缺口" tabIndex={0}><table className="s-table"><thead><tr><th>阶段</th><th>当前证据</th><th>下一步</th></tr></thead><tbody>
 <tr><td>P3 现货—永续信息</td><td>{study?.p3_study?`${study.p3_study.trials_attempted} 次尝试 · ${study.p3_study.decision}`:study?.p3?.status??'尚无同步采集回执'}</td><td>分别积累 USDC / USDT 可知时间明确的样本，检验独立增量。</td></tr>
 <tr><td>P4 资金费与 OI</td><td>{study?.p4?`${study.p4.trials_attempted} 次尝试 · ${study.p4.decision}`:'六组受限规格已定义，未形成成本后实验结论'}</td><td>接入可用时间明确的联合面板，完成同日、同波动和同价格状态控制。</td></tr>
 <tr><td>P5 成交校准</td><td>{study?.p5?.status??'缺少真实场所轨迹'}</td><td>核查固定动作、逐单数量、时间与状态；独立验证来源。</td></tr>
 <tr><td>P6 持续证据</td><td>{study?.p6?.cases?`已形成 ${study.p6.cases.length} 组模拟成本账户`:forward?.common_mature_labels===undefined?'尚无可比较窗口':`共同成熟标签 ${forward.common_mature_labels}`}</td><td>保持冻结设置，积累共同窗口与跨时期证据；账户结果见“前向账户”。</td></tr>
 </tbody></table></div>
 {study?.p3?.quotes.map(r=><p className="f-message" key={r.quote}>{r.quote} · 现货基差 {fmt(r.feature.basis*10000)} bp · 指数溢价 {fmt(r.feature.index_premium*10000)} bp · 实际可知 {stamp(r.feature.decision_ms,true)}</p>)}
 {study?.p3?.failures.map((r,i)=><p key={i} role="status">{r.quote}：{r.reason}</p>)}
 {study?.p5&&<p className="f-message">配对订单 {study.p5.paired_orders} · 数量绝对误差 {study.p5.quantity_absolute_error} · 状态时间绝对误差 {study.p5.state_time_absolute_error_ms} ms。导入来源尚未独立验真。</p>}
 {([study?.p3_study,study?.p4]).map((report,index)=>report&&<div key={index} className="s-table-wrap" role="region" aria-label={`${index===0?'基差':'资金费与 OI'}信息增量成本后对照`} tabIndex={0}><table className="s-table"><caption>{index===0?'P3 基差增量':'P4 资金费与 OI 增量'}</caption><thead><tr><th>对照编号</th><th>费用 / 滑点 bp</th><th>净收益 USDC</th><th>状态匹配样本</th><th>组内关联</th></tr></thead><tbody>{report.cases.map(r=><tr key={`${r.id}-${r.fee_bps}-${r.slippage_bps}`}><td>{r.id===0?(index===0?'价格 / 主动成交量基线':'价格基线'):`对照 ${r.id}`}</td><td>{r.fee_bps} / {r.slippage_bps}</td><td>{fmt(r.metrics.net_pnl)}</td><td>{r.state_controls.matched_rows}</td><td>{fmt(r.state_controls.within_state_covariance,6)}</td></tr>)}</tbody></table></div>)}
 {forward&&<p className="f-message">当前窗口：预测缺口 {forward.missing_forecast_slots?.length??'未知'} · 到期标签缺口 {forward.missing_mature_label_slots?.length??'未知'} · 未成熟 {forward.pending_slots?.length??'未知'}。真实成交账户收益：未知；模拟账户单独展示，不将预测准确率视作盈利。</p>}
 </Panel>;
}
