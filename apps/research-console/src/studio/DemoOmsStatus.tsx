import type {DemoOmsStatusSnapshot} from '@/lib/types/demo-oms-status';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';
import {DemoResidentStatus} from './DemoResidentStatus';
import type {DemoResidentStatusSnapshot} from '@/lib/types/demo-resident-status';

const titles={INPUTS_UNRESOLVED:'输入未齐',AWAITING_PROJECTION:'输入已配对，等待订单状态投影',
  PROJECTION_INTERRUPTED:'订单状态投影中断，等待恢复',OMS_TERMINAL_PROJECTED:'订单状态恢复已完成'};
const reasons:Record<string,string>={
  ORIGINAL_ID_QUERY_MISSING:'缺少原订单编号的查询结果。',
  ORIGINAL_ID_QUERY_UNKNOWN:'原订单查询尚未确认订单存在。',
  OMS_PROJECTION_INTERRUPTED:'写入订单状态时中断；恢复时先重放已保存事件，再确认完成数量。',
  RECEIPT_LEDGER_UNRESOLVED:'私有回执尚未满足唯一账本回放条件。',
  STREAM_GAPS_IN_WINDOW:'原成交窗口存在数据缺口；重连或账户归零不会消除这段缺口。',
  QUERY_CLOCK_BRACKET_UNRESOLVED:'缺少覆盖原成交窗口的有效场所时钟记录。',
  PRIVATE_RECEIPTS_MISSING:'缺少与原订单编号对应的私有回执。',
  FOREIGN_SAME_QUOTE_ORDER:'同计价账户出现其他订单，需要单独核对，不能并入本轮自动成交。',
  PRIVATE_ORDER_NOT_TERMINAL:'私有回执中仍有订单未结束。',
  PRIVATE_QUERY_STATE_OR_QUANTITY_DIFFERENCE:'查询与私有回执的订单身份、终态或成交数量不符。',
  PRIVATE_QUERY_CLOCK_UNRESOLVED:'查询与私有回执的时间关系尚未满足核验条件。',
  BATCH_RESOLUTION_INCOMPLETE:'固定批次中仍有订单未完成配对。',
};
export function DemoOmsStatus({snapshot,refreshFailed,resident,asOf}:{snapshot?:DemoOmsStatusSnapshot;refreshFailed:boolean;
  resident?:DemoResidentStatusSnapshot;asOf?:number}) {
  const r=snapshot?.report;
  return <Panel title="订单恢复进度" note="原订单查询与私有回执配对 · 原订单系统幂等恢复">
    <DemoResidentStatus snapshot={resident} asOf={asOf??0} refreshFailed={refreshFailed}/>
    {!r ? <Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'订单恢复记录无法验证':snapshot?.status==='DATA_BLOCKED'?'订单恢复记录时间待核查':'等待订单恢复记录'}>
      取得固定批次的原订单查询、私有终态回执及持久化结果后，展示实际恢复进度。
    </Empty> : <div className="s-demo-replay">
      <div className="s-demo-replay-heading"><h3>{titles[r.phase]}</h3><span className="s-demo-source">DEMO · 技术验证</span></div>
      <p className="s-demo-description">记录截至 {stamp(r.observed_at_ms,true)}。配对成功后仍需完成订单状态写入；现金核对请查看唯一账本。</p>
      {refreshFailed&&<p role="status">刷新失败，以下保留上次成功读取的历史恢复记录。</p>}
      <div className="s-metrics">
        <Metric label="固定批次订单" value={r.order_count}/>
        <Metric label="查询与回执相符" value={r.matched_orders}/>
        <Metric label="已恢复订单" value={r.projected_orders??'未知'}/>
      </div>
      {!!r.issues.length&&<div className="s-demo-notice" role="status"><ul>{r.issues.map(i=><li key={i}>{reasons[i]??i}</li>)}</ul></div>}
      <details className="s-demo-identities"><summary>查看逐单结果与记录绑定</summary>
        {r.orders.length>0&&<div className="s-table-wrap s-demo-table s-demo-oms-orders" role="region" aria-label="订单恢复逐单结果" tabIndex={0}><table className="s-table"><thead><tr>
          <th>原订单编号</th><th>交易所订单编号</th><th>查询终态</th><th>累计成交数量</th><th>私有回执</th>
        </tr></thead><tbody>{r.orders.map(o=><tr key={o.client_order_id}>
          <td data-label="原订单编号">{o.client_order_id}</td><td data-label="交易所订单编号">{o.venue_order_id}</td>
          <td data-label="查询终态">{o.query_native_status}</td><td data-label="累计成交数量">{o.filled_quantity}</td>
          <td data-label="私有回执">{o.private_event_count} 条 · {stamp(o.last_received_at_ms,true)}</td>
        </tr>)}</tbody></table></div>}
        <dl>{[['固定批准预留',r.reservation_sha256],['订单提案',r.proposal_sha256],['恢复计划',r.plan_sha256],
          ['回执导入',r.import_sha256],['冻结回执清单',r.manifest_hash??'尚未形成'],['回执日志',r.journal_tail??'尚未形成']]
          .map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <p>订单状态恢复与资金核对分别记录；本页不确认现金闭合、盈利或交易准入。</p>
      </details>
    </div>}
  </Panel>;
}
