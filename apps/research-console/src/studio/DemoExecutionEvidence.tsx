import type {DemoExecutionSnapshot} from '@/lib/types/binance-demo';
import {Empty, Metric, Panel} from './components';

const stateNames: Record<string, string> = {UNKNOWN: '未知', ACK: '已确认', PARTIALLY_FILLED: '部分成交', FILLED: '全部成交', CANCELLED: '已撤单', EXPIRED: '已失效'};
const comparisons: Record<string, string> = {UNKNOWN: '尚无法比较', WAITING_TERMINAL_ORDERS: '等待订单终态', DECLARED_POSITION_MATCH: '与声明持仓一致', DECLARED_POSITION_MISMATCH: '与声明持仓不一致'};
const reasons: Record<string, string> = {NO_RECEIPTS: '尚未导入回执', DECLARED_STREAM_GAPS: '声明窗口存在数据缺口',
  COMMISSION_NOT_SUPPORTED: '费用缺失、币种不符或返佣尚不支持'};
const timestamp = (ms: number) => new Date(ms).toISOString().replace('T', ' ').replace('Z', ' UTC');

export function DemoExecutionEvidence({snapshot, refreshFailed}: {snapshot?: DemoExecutionSnapshot; refreshFailed: boolean}) {
  const report = snapshot?.report;
  return <Panel title="成交回执与恢复" note="离线技术回放 · 独立于公共连接与账户检查">
    {!report ? <Empty title={snapshot?.status === 'CORRUPT_OR_UNREADABLE' ? '回执日志无法验证' : snapshot?.status === 'DATA_BLOCKED' ? '回执时间晚于当前时间' : '尚无成交回执日志'}>
      导入冻结目标对应的订单回执后，显示逐笔成交、费用和恢复结果。这里不发起采集或下单。
    </Empty> : <div className="s-demo-replay">
      <div className="s-demo-replay-heading"><div><span className="s-eyebrow">{report.symbol} / RECEIPT REPLAY</span>
        <h3>{report.projection_status === 'UNRESOLVED' ? '回放未解决' : '已重建技术账本'}</h3></div>
        <span className="s-demo-source">{report.provenance === 'SYNTHETIC_TEST_FIXTURE' ? '合成测试数据' : '导入回执 · 来源未独立验证'}</span></div>
      {refreshFailed && <p role="status" className="s-demo-notice">刷新失败，以下是上次读取的历史回放，不能表示当前日志状态。</p>}
      <p className="s-demo-description">从固定订单与原始回执重新计算；初始空仓、抵押金和期末目标均来自声明。真实账户现金、资金费及外部流水尚未核对。</p>
      {report.projection_status === 'UNRESOLVED' && <div className="s-demo-notice" role="status"><strong>{reasons[report.projection_reason ?? ''] ?? '成交事实无法按当前模型完整回放'}</strong>
        <p>原始回执仍保留；账本数值保持未知。原因：{report.projection_reason}</p></div>}
      <div className="s-metrics s-demo-metrics">
        <Metric label="回执日志" value={`${report.journal_records} 条`} note={`${report.trades_count} 笔去重成交 · ${report.duplicate_messages} 条重复推送`}/>
        <Metric label="拒绝的导入批次" value={String(report.rejected_batches)} note="不覆盖已保存的成交事实"/>
        <Metric label="回放手续费" value={report.reconciliation?.fee ?? '—'} note={`${report.quote_asset} · 按回执原值计入一次`}/>
        <Metric label="场所现金对账" value="未知" note="技术回放不证明账户已对齐"/>
      </div>
      <div className="s-demo-comparison"><strong>{comparisons[report.position_comparison]}</strong>
        <span>回放持仓 {report.position_quantity ?? '未知'} BTC</span><span>声明期末持仓 {report.expected_end_position ?? '未知'} BTC</span>
        <span>仅在订单进入终态后比较声明目标</span></div>
      <h3 className="s-demo-subheading">固定订单</h3>
      <div className="s-table-wrap s-demo-table" role="region" aria-label="回执订单表" tabIndex={0}><table className="s-table"><thead><tr>
        <th>订单身份</th><th>方向</th><th>目标数量 / BTC</th><th>已成交 / BTC</th><th>最后状态</th>
      </tr></thead><tbody>{report.orders.map(o => <tr key={o.order_id}><td>{o.order_id}</td><td>{o.side === 'BUY' ? '买入' : '卖出'}</td>
        <td>{o.quantity}</td><td>{o.filled_quantity}</td><td>{stateNames[o.state] ?? o.state}</td></tr>)}</tbody></table></div>
      <h3 className="s-demo-subheading">逐笔成交 <small>最近 {report.recent_trades.length} / 共 {report.trades_count} 笔 · 时间为 UTC</small></h3>
      {report.recent_trades.length ? <div className="s-table-wrap s-demo-table" role="region" aria-label="逐笔成交表" tabIndex={0}><table className="s-table"><thead><tr>
        <th>成交时间 / 接收时间</th><th>场所订单 / 成交 ID</th><th>数量 / BTC</th><th>价格 / {report.quote_asset}</th><th>回执费用</th><th>角色</th>
      </tr></thead><tbody>{report.recent_trades.map(t => <tr key={`${t.venue_order_id}:${t.trade_id}`}><td>{timestamp(t.venue_trade_ms)}<small className="s-demo-cell-note">{timestamp(t.received_at_ms)}</small></td>
        <td>{t.venue_order_id}<small className="s-demo-cell-note">{t.trade_id}</small></td><td>{t.quantity}</td><td>{t.price}</td>
        <td>{t.reported_commission === null ? '未知' : `${t.reported_commission} ${t.commission_asset ?? '币种未知'}`}</td><td>{t.liquidity_role}</td></tr>)}</tbody></table></div>
        : <Empty title="尚无成交回执">已确认订单不等于已经成交。</Empty>}
      <details className="s-demo-identities"><summary>查看恢复身份与来源</summary><dl>
        {[['冻结规格', report.manifest_hash], ['日志末端', report.journal_tail], ['回放代码指纹', report.replay_source_sha256],
          ['声明源码版本', report.declared_source_commit], ['技术账本状态', report.ledger_state_hash ?? '未知']].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        <div><dt>最后接收时间</dt><dd>{report.observed_at_ms === null ? '未知' : timestamp(report.observed_at_ms)}</dd></div>
      </dl><p>身份哈希用于内容核对与复现，不是来源签名或交易授权。当前只覆盖固定 LIMIT / GTX、单向、全仓订单。</p></details>
    </div>}
  </Panel>;
}
