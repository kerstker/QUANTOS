import type {DemoCancelStatusSnapshot} from '@/lib/types/demo-cancel-status';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';
const labels={NOT_CLAIMED:'尚无发送标记',CLAIMED_RESPONSE_UNKNOWN:'已保留发送标记，结果未知',RESPONSE_OBSERVED:'已取得响应，仍需查询恢复'};
export function DemoCancellation({snapshot,refreshFailed}:{snapshot?:DemoCancelStatusSnapshot;refreshFailed:boolean}){
  const r=snapshot?.report;
  return <Panel title="撤单与查询恢复" note="仅原订单 · 发送前持久化 · 结果不明时查询原编号">
    {!r?<Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'撤单记录无法验证':snapshot?.status==='DATA_BLOCKED'?'撤单记录时间待核查':'暂无撤单执行记录'}>
      显示固定批次的撤单尝试、响应和未知状态；订单终态由查询与私有回执另行核对。
    </Empty>:<div className="s-demo-replay">
      <h3>{r.phase==='RESERVED_AWAITING_ATTEMPTS'?'撤单批准已保留，尚无发送标记':'撤单后等待原编号查询与私有回执'}</h3>
      <p className="s-demo-description">记录截至 {stamp(r.observed_at_ms,true)}。接口响应不代替订单恢复完成；撤单期间仍可能发生部分或全部成交。</p>
      {refreshFailed&&<p role="status">刷新失败，以下保留上次成功读取的历史撤单记录。</p>}
      <div className="s-metrics">
        <Metric label="本批原订单" value={r.order_count}/><Metric label="已保留发送标记" value={r.claimed_orders}/>
        <Metric label="已保存响应结果" value={r.response_orders}/><Metric label="结果未知的请求" value={r.unresolved_responses}/>
      </div>
      <details className="s-demo-identities"><summary>查看撤单逐单记录</summary>
        <div className="s-table-wrap s-demo-table s-demo-oms-orders" role="region" aria-label="撤单逐单结果" tabIndex={0}><table className="s-table">
          <thead><tr><th>原订单编号</th><th>交易所订单编号</th><th>撤单记录</th><th>响应订单状态</th><th>响应累计成交</th></tr></thead>
          <tbody>{r.orders.map(o=><tr key={o.client_order_id}><td data-label="原订单编号">{o.client_order_id}</td>
            <td data-label="交易所订单编号">{o.venue_order_id}</td><td data-label="撤单记录">{labels[o.status]}</td>
            <td data-label="响应订单状态">{o.venue_status??'未知'}</td><td data-label="响应累计成交">{o.executed_quantity??'未知'}</td></tr>)}</tbody>
        </table></div>
        <dl>{[['撤单批准记录',r.grant_sha256],['原订单预留',r.reservation_sha256],['撤单日志快照',r.snapshot_sha256]]
          .map(([name,value])=><div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
        <p>本页不创建替代订单、不更新持仓或现金；最终状态请查看订单恢复和唯一账本。</p>
      </details>
    </div>}
  </Panel>;
}
