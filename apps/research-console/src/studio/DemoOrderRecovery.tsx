import type {DemoOrderRecoverySnapshot} from '@/lib/types/binance-demo';
import {Empty, Panel} from './components';
import {stamp} from './data';

const labels: Record<string, string> = {
  MATCHED_OBSERVATIONS: '订单观察一致', ACTION_REQUIRED: '订单差异待处理', STALE: '查询快照已过期',
  DATA_BLOCKED: '查询时间不满足比较条件', IDENTITY_MISMATCH: '冻结订单身份不一致', JOURNAL_ADVANCED: '回执已更新，需要重新查询',
  NO_JOURNAL: '等待有效回执日志', BACKFILL_REQUIRED: '缺少逐笔成交回执', STATE_UPDATE_REQUIRED: '缺少订单状态回执',
  QUERY_UNRESOLVED: '查询尚未确认', RECEIPTS_MISSING: '未收到订单回执', OLDER_VENUE_OBSERVATION: '查询结果早于已知回执',
  TERMINAL_CONFLICT: '订单终态冲突', QUANTITY_REGRESSION: '查询累计成交倒退', STATE_CONFLICT: '订单状态转换冲突',
};
const reasons: Record<string, string> = {ACCOUNT_NOT_REQUESTED: '尚未发起私有查询', CREDENTIALS_MISSING: '操作员采集环境缺少凭证',
  REQUEST_TIMEOUT: '采集超时', RATE_LIMITED: '场所限流', PREVIOUS_QUERY_FAILED: '前项失败，后续查询停止',
  ORDER_NOT_FOUND_UNCONFIRMED: '场所未找到订单，不能据此确认订单不存在', IDENTITY_MISMATCH: '返回订单与冻结身份不符',
  RESPONSE_INVALID: '返回状态或数据无效', REQUEST_FAILED: '查询失败', CLOCK_UNRELIABLE: '采集时钟检查失败',
  AUTHENTICATION_REJECTED: '场所拒绝查询身份'};
const states: Record<string, string> = {ACK: '已确认', NEW: '已受理', PARTIALLY_FILLED: '部分成交', FILLED: '全部成交',
  CANCELLED: '已撤单', CANCELED: '已撤单', EXPIRED: '已失效', EXPIRED_IN_MATCH: '撮合时失效', UNKNOWN: '未知'};

export function DemoOrderRecovery({snapshot, refreshFailed}: {snapshot?: DemoOrderRecoverySnapshot; refreshFailed: boolean}) {
  const report = snapshot?.report;
  return <Panel title="订单恢复核对" note="同一冻结订单 · REST 查询与原始回执分别保留">
    {!report ? <Empty title={snapshot?.status === 'CORRUPT_OR_UNREADABLE' ? '订单查询快照无法验证' : '尚无订单查询快照'}>
      操作员完成只读订单查询后，这里会列出漏收成交、状态差异和未确认项。
    </Empty> : <div className="s-demo-replay">
      <div className="s-demo-replay-heading"><h3>{labels[report.status]}</h3>
        <span className="s-demo-source">{report.provenance === 'SYNTHETIC_TEST_FIXTURE' ? '合成查询测试数据' : '操作员只读查询 · 来源未独立验证'}</span></div>
      <p className="s-demo-description">比较累计成交数量和订单状态。查询快照不包含完整逐笔费用；差异不会自动改写账本或触发重新下单。</p>
      {refreshFailed && <p className="s-demo-notice" role="status">刷新失败，以下为上次读取的查询比较。</p>}
      {!!report.orders.length && <div className="s-table-wrap s-demo-table" role="region" aria-label="订单恢复比较表" tabIndex={0}><table className="s-table"><thead><tr>
        <th>订单 / 场所 ID</th><th>回执状态</th><th>查询状态</th><th>回执成交 / BTC</th><th>查询成交 / BTC</th><th>数量差 / BTC</th><th>核对结果</th>
      </tr></thead><tbody>{report.orders.map(o => <tr key={o.order_id}><td>{o.order_id}<small className="s-demo-cell-note">{o.venue_order_id}</small></td>
        <td>{states[o.receipt_state] ?? o.receipt_state}</td><td>{o.query_state === null ? '未知' : states[o.query_state] ?? o.query_state}</td>
        <td>{o.receipt_quantity ?? '未知'}</td><td>{o.query_quantity ?? '未知'}</td><td>{o.quantity_gap ?? '未知'}</td>
        <td><strong>{labels[o.status]}</strong>{o.reason !== 'ORDER_QUERY_SUCCEEDED' && <small className="s-demo-cell-note">{reasons[o.reason] ?? o.reason}</small>}</td></tr>)}</tbody></table></div>}
      <p className="s-demo-description">{report.status === 'MATCHED_OBSERVATIONS' ? '数量与状态相符；费用、现金、资金费和实时连接仍需独立核对。' : '先核实原始回执和查询时间，再处理差异。查不到订单、查询失败和快照过期均不能作为重试授权。'}</p>
      <details className="s-demo-identities"><summary>查看订单查询来源</summary><dl>
        <div><dt>采集开始</dt><dd>{stamp(report.observed_at_ms, true)}</dd></div>
        <div><dt>查询快照</dt><dd>{report.capture_hash}</dd></div>
        <div><dt>冻结规格</dt><dd>{report.manifest_hash}</dd></div>
        <div><dt>查询绑定日志</dt><dd>{report.journal_tail}</dd></div>
      </dl></details>
    </div>}
  </Panel>;
}
