import type {NativeOrderReceiptEvidence} from '@/lib/types/ai-factory';
import {Chart} from './Charts';

export function NativeOrderReceiptsEvidence({data}:{data:NativeOrderReceiptEvidence}) {
 const plot=data.fee_comparisons.filter(r=>r.comparison_available).map(r=>({label:`${r.profile_ms} ms`,
  reported:Number(data.reported_quote_commission),simulated:Number(r.simulated_fee)}));
 return <section role="region" aria-label="币安原始订单回执" className="s-native-receipts">
 <h3>币安原始订单回执 · {data.symbol}</h3>
 <p>{data.messages_count} 条消息 · {data.duplicate_messages} 条重复 · {data.trades_count} 笔唯一成交 · 报告累计成交 {data.reported_filled_quantity} BTC</p>
 <p className="f-message">{data.provenance==='SYNTHETIC_TEST_FIXTURE'?'合成原始消息，用于验证接线。':'离线导入的原始消息，来源未经独立认证。'} 真实已付费用仍未知；以下金额是消息中的报告值，与模拟账户分开保留。</p>
 <p>{data.missing_commission_trades} 笔成交缺少完整手续费 · {data.coverage.gaps.length} 处声明缺口 · {data.unobserved_order_ids.length} 个固定订单没有回执。</p>
 <div className="s-table-wrap s-account-costs" role="region" aria-label="回执报告费用" tabIndex={0}>
 <table className="s-table"><thead><tr><th>报告币种</th><th>手续费金额（有符号）</th></tr></thead><tbody>
 {Object.entries(data.reported_commission_by_asset).map(([asset,fee])=><tr key={asset}><td>{asset}</td><td>{fee}</td></tr>)}
 {!Object.keys(data.reported_commission_by_asset).length&&<tr><td colSpan={2}>没有完整币种与金额记录</td></tr>}
 </tbody></table></div>
 <p className="f-message">USDC、USDT、BNB 等分别列示，不进行缺少汇率证据的换算。缺失字段不会补成零；重复成交不重复累计费用。</p>
 <div className="s-table-wrap s-account-costs" role="region" aria-label="报告费用与模拟费用比较" tabIndex={0}>
 <table className="s-table"><thead><tr><th>延迟情景</th><th>模拟费用 · {data.quote}</th><th>报告值 − 模拟值 · {data.quote}</th></tr></thead><tbody>
 {data.fee_comparisons.map(r=><tr key={r.profile_ms}><td>{r.profile_ms} ms</td><td>{r.simulated_fee??'未知'}</td>
 <td>{r.comparison_available?r.reported_minus_simulated_fee??'未知':'未知'}</td></tr>)}
 </tbody></table></div>
 <p className="f-message">费用差包含成交数量差异，不等同费率改善或盈利改善。仅在声明窗口一致、订单与费用记录完整时计算；这些费用不参与延迟参数选择，也不再次扣入账本。</p>
 {!!plot.length&&<Chart title="报告费用与模拟费用" kind="bar" rows={plot}
  series={[{key:'reported',label:'回执报告值'},{key:'simulated',label:'模拟值'}]} unit={data.quote}
  note="按相同声明窗口比较总额，精确值见上表。负差额不能单独证明执行改善。"/>}
 <details><summary>原始成交、四类时钟与来源</summary>
 <p>单位为 Unix 毫秒。E 为消息事件时间，T 为事务时间，订单 T 为成交时间，本机接收时间决定校准分段。</p>
 <div className="s-table-wrap" role="region" aria-label="原始成交时钟与费用" tabIndex={0}>
 <table className="s-table"><thead><tr><th>固定订单 / 成交 ID</th><th>数量 / 价格</th><th>角色</th><th>E / T / 订单 T / 本机接收</th><th>报告手续费</th></tr></thead><tbody>
 {data.trade_rows.map(r=><tr key={`${r.venue_order_id}:${r.trade_id}`}><td>{r.order_id} / {r.trade_id}</td>
 <td>{r.quantity} / {r.price}</td><td>{r.liquidity_role==='MAKER'?'maker':'taker'}</td>
 <td>{r.event_ms} / {r.transaction_ms} / {r.venue_trade_ms} / {r.received_at_ms}</td>
 <td>{r.reported_commission??'未知'} {r.commission_asset??'币种未知'}</td></tr>)}
 </tbody></table></div>
 <p>显示最近 {data.trade_rows.length} 笔，共 {data.trade_rows_total} 笔；全部消息保存在不可变输入，完整诊断保存在研究材料 {data.artifact}。</p>
 <p className="s-native-identity">输入摘要 <code>{data.input_hash}</code></p>
 <p className="s-native-identity">匿名账户摘要 <code>{data.account_ref_sha256}</code></p>
 </details></section>;
}
