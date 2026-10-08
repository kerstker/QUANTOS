import type {GoldTradeReview as Review} from '@/lib/types/research';
import {Panel} from './components';
import {stamp} from './data';
const label=(s:string)=>({MATCHING_OBSERVED_IDENTITY:'编号及方向一致',MATCHING_REPORTED_CASH:'金额一致',MATCHING_OBSERVED_QUANTITY:'数量一致',NOT_OBSERVED:'未查到关联记录',WINDOW_INCOMPLETE:'窗口成交不足',MISMATCH:'存在差异'}[s]??s);
export function GoldTradeReview({review:r}:{review:Review}) {
 return <Panel title="逐笔交易复盘"><p className="s-pad">已核对 {r.total_trades} 笔成交，{r.mismatched_trades} 笔存在差异，{r.unmatched_cash_count} 条资金记录未关联到成交。下表最多展示最近500条。金额一致不代表完整历史。</p>
 <div style={{overflowX:'auto'}}><table className="s-table"><thead><tr><th>成交时间</th><th>方向／数量</th><th>手续费</th><th>订单关联</th><th>手续费核对</th><th>已实现盈亏核对</th></tr></thead><tbody>{r.trades.map((t,i)=><tr key={i}><td>{stamp(t.time_ms,true)}</td><td>{t.side==='BUY'?'买入':'卖出'} · {t.quantity}</td><td>{t.commission} {t.commission_asset}</td><td>{label(t.order_check)}</td><td>{label(t.commission_check)}</td><td>{label(t.realized_pnl_check)}</td></tr>)}</tbody></table></div>
 <details className="s-pad"><summary>订单、撤单余量与条件单关联</summary><div style={{overflowX:'auto'}}><table className="s-table"><thead><tr><th>订单状态</th><th>成交笔数</th><th>已成交数量</th><th>未成交余量</th><th>核对</th></tr></thead><tbody>{r.orders.map((o,i)=><tr key={i}><td>{o.status}</td><td>{o.fill_count}</td><td>{o.reported_executed_quantity}</td><td>{o.unfilled_quantity}</td><td>{label(o.quantity_check)}</td></tr>)}</tbody></table></div>{r.conditional_orders.map((o,i)=><p key={i}>{o.status} · 实际订单：{label(o.child_check)}</p>)}</details>
 <p className="s-pad">缺少期初持仓和完整订单过程，暂不计算账户净盈亏，也不推断人工交易理由。</p></Panel>;
}
