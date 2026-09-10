import {useState} from 'react';
import type {PassiveExecutionStudy,PassiveAccountEvidence} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {Chart} from './Charts';
import './passive-execution.css';

const status:Record<string,string>={SENT:'已发送，等待到达',ACK:'场所确认',OPEN:'排队中',PARTIALLY_FILLED:'部分成交',FILLED:'全部成交',REJECTED:'拒绝',CANCEL_SENT:'已发送撤单',CANCELLED:'已撤单',CANCEL_REJECTED:'撤单拒绝',EXPIRED:'已到期'};
const reasons:Record<string,string>={POST_ONLY_WOULD_CROSS:'到达时会立即成交',STALE_BOOK:'盘口已过时',OUTSIDE_KNOWN_BOOK_RANGE:'价格超出已知盘口',ORDER_NOT_OPEN:'订单尚未到达或已结束',TIME_IN_FORCE:'有效期结束',CLIENT_CANCEL_ARRIVED:'撤单已到达',UNKNOWN_TOUCH_AFTER_TRADES:'成交后最优盘口暂时未知',EXPIRED_BEFORE_ARRIVAL:'到达前已过期'};
const iso=(ms:number)=>Number.isFinite(new Date(ms).getTime())?new Date(ms).toISOString():'未知';
const clock=(ms:number)=>iso(ms)==='未知'?'未知':iso(ms).slice(11,23);

function AccountEvidence({account,quote}:{account:PassiveAccountEvidence;quote:string}) {
 const rows=account.equity_history.map(p=>({label:clock(p.timestamp),equity:Number(p.equity),drawdown:Number(p.drawdown)*100}));
 const fields:[keyof PassiveAccountEvidence['reconciliation'],string][]=[['initial_equity','初始权益'],['ending_equity','期末权益'],
  ['price_pnl','价格损益（含未平仓估值）'],['fee','模拟手续费（扣除）'],['spread','账本点差费用（扣除）'],
  ['impact','账本冲击费用（扣除）'],['funding','已结算资金费（有符号）'],['liquidation_fee','模拟强平费用（扣除）'],
  ['insurance_adjustment','保险调整'],['adl_adjustment','减仓调整'],['attributed_change','成本后权益变化'],['residual','对账差额']];
 return <>
 <p className="f-message">统一 Decimal 账本已核算本情景；模拟费率 {account.simulated_fee_rate}，真实费用未知，队列尚无真实回执校准。保证金采用保守模拟假设，不能据此认定实际盈利。</p>
 <div className="s-table-wrap s-account-costs" role="region" aria-label="唯一账本成本归因" tabIndex={0}><table className="s-table"><thead><tr><th>成本与权益</th><th>{quote} · 精确账本值</th></tr></thead><tbody>{fields.map(([key,label])=><tr key={key}><td>{label}</td><td>{account.reconciliation[key]}</td></tr>)}</tbody></table></div>
 <p>期末未平仓 {Object.entries(account.ending_state.positions).map(([symbol,p])=>`${symbol}：${p.quantity} BTC`).join('；')||'无'}。窗口结束保留持仓与排队状态；期末权益包含标记价估值。</p>
 <div className="s-analytics-grid"><Chart title="被动回放 · 模拟权益" rows={rows} series={[{key:'equity',label:'权益'}]} unit={quote} note={`最近 ${rows.length} 个账本检查点，共 ${account.history_count} 个；完整历史保存在研究材料中。`}/>
 <Chart title="被动回放 · 模拟回撤" rows={rows} series={[{key:'drawdown',label:'回撤'}]} unit="%" note="按账本事件检查点记录；模型假设尚未经过实际场所校准。"/></div>
 </>;
}

export function PassiveExecutionEvidence({data}:{data?:PassiveExecutionStudy|null}) {
 const [profile,setProfile]=useState(0);
 const trial=data?.trials.find(t=>t.profile_ms===profile)??data?.trials[0];
 const books=data?.quality.book_samples??[];
 const rows=books.map(b=>({label:clock(b.available_at_ms),bid:Number(b.bids[0]?.[0]),ask:Number(b.asks[0]?.[0]),lag:b.available_at_ms-b.transaction_time_ms}));
 return <>
 <Panel title="被动成交 · 原始数据与队列回放" note="固定动作、共享成交量、按价格和到达顺序排队；撤单按到达场所的时间生效。">
 <div className="s-execution-body">
 {!data?<p className="f-message">尚未导入原始逐笔数据。选择下方「P5 原始盘口与队列」，登记采集文件和固定动作后检查。</p>:<>
 <p><strong>{data.symbol}</strong> · {iso(data.start_ms).slice(0,10)} UTC · 共同可用窗口 {clock(data.start_ms)} — {clock(data.end_ms)} · {data.trials_attempted} 次尝试</p>
 <p className="f-message">深度增量 {data.quality.counts.depth_updates} · 聚合成交 {data.quality.counts.aggregate_trades} · 标记价 {data.quality.counts.mark_updates} · 重复包 {data.quality.counts.duplicates}</p>
 <p className="f-message">{data.account_environment?'已提供冻结的公开规则、容量与资金费日程；每个成功情景保留统一账本结果。实际队列位置和真实场所费用仍待回执验证。':'公开盘口无法证明实际队列位置；当前输入尚未接入成本账本，也未完成真实回执校准。这里显示模型成交诊断，净收益和真实费用仍未知。'}</p>
 {!!data.quality.failures.length&&<ul>{data.quality.failures.map((f,i)=><li key={i}>{f.reason}</li>)}</ul>}
 {data.trials.length===0?<p>已验证输入，尚无固定动作；没有生成订单或成交结论。</p>:<>
 <label>队列延迟情景 <select value={trial?.profile_ms??0} onChange={e=>setProfile(Number(e.target.value))}>{data.trials.map(t=><option key={t.profile_ms} value={t.profile_ms}>{t.profile_ms} ms · {t.status==='FAILED'?'失败':'模型对照'}</option>)}</select></label>
 <p className="f-message">订单、撤单及回执使用所选固定延迟。外部撤量先从队尾扣减，新增数量排在队尾；没有根据收益或成交数量挑选最佳情景。</p>
 {trial?.status==='FAILED'?<p role="status">本次情景失败：{trial.reason??'未知原因'}，原始记录已保留。</p>:trial&&<>
 <p>{trial.fill_count} 次成交 · {trial.censored_orders} 个窗口截尾订单（结束时仍未完成）</p>
 {trial.account&&<AccountEvidence account={trial.account} quote={data.quote}/>}
 <div className="s-table-wrap" role="region" aria-label="被动订单结果" tabIndex={0}><table className="s-table"><thead><tr><th>订单</th><th>方向 / 价格</th><th>到达时前方数量</th><th>成交 / 剩余</th><th>状态</th><th>原因</th></tr></thead><tbody>{trial.orders?.map(o=><tr key={o.order_id}><td>{o.order_id}</td><td>{o.side==='BUY'?'买':'卖'} / {o.price}</td><td>{o.queue_ahead_at_arrival??'未知'}</td><td>{o.filled_quantity} / {o.remaining_quantity}</td><td>{status[o.status]??o.status}{o.censored&&' · 窗口截尾'}</td><td>{o.reason?(reasons[o.reason]??o.reason):'—'}</td></tr>)}</tbody></table></div>
 <details><summary>逐单时间轨迹</summary><p>时间为 Unix 毫秒。场所时间与假设的客户端接收时间分列；发送时间来自固定动作。显示最近 200 条，共 {trial.event_count} 条，完整轨迹保存在研究材料中。</p>
 <div className="s-table-wrap" role="region" aria-label="订单时钟轨迹" tabIndex={0}><table className="s-table"><thead><tr><th>订单</th><th>状态</th><th>发送时间</th><th>场所时间</th><th>假设接收时间</th><th>成交量</th></tr></thead><tbody>{trial.events?.map((e,i)=><tr key={i}><td>{e.order_id}</td><td>{status[e.status]??e.status}</td><td>{e.client_time_ms??'—'}</td><td>{e.venue_time_ms??'—'}</td><td>{e.client_receive_ms??'—'}</td><td>{e.quantity??'—'}</td></tr>)}</tbody></table></div></details>
 </>}
 </>}
 </>}
 </div>
 </Panel>
 {data&&<div className="s-analytics-grid"><Chart title="原始盘口 · 买一与卖一" rows={rows} series={[{key:'bid',label:'买一'},{key:'ask',label:'卖一'}]} unit={data.quote} note="按本机实际可用时间，每秒首个盘口采样。完整增量保存在原始文件中。"/>
 <Chart title="行情接收延迟" rows={rows} series={[{key:'lag',label:'可用时间 − 场所时间'}]} unit="ms" note="包含网络、处理与首次快照等待；不等同真实订单延迟。"/></div>}
 </>;
}
