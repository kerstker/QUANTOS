import type {DemoPrivateCheckSnapshot} from '@/lib/types/binance-demo';
import {Empty, Panel} from './components';
import {stamp} from './data';

const names: Record<string, string> = {credentials: '本地 Demo 凭证', public: '公共连接与时钟',
  account: '账户签名读取', BTCUSDT_orders: 'BTCUSDT 当前挂单', BTCUSDT_trades: 'BTCUSDT 近期成交查询',
  BTCUSDC_orders: 'BTCUSDC 当前挂单', BTCUSDC_trades: 'BTCUSDC 近期成交查询',
  listen_key: '私有订阅会话', websocket: '私有连接与主动重连', worker: '检查进程'};

export function DemoPrivateChecks({snapshot, refreshFailed}: {snapshot?: DemoPrivateCheckSnapshot; refreshFailed: boolean}) {
  const report = snapshot?.report;
  const historical = snapshot?.status === 'STALE' || refreshFailed;
  return <Panel title="自动联调记录" note="账户与私有连接 · 单次有界检查">
    {!report ? <Empty title={snapshot?.status === 'CORRUPT_OR_UNREADABLE' ? '联调记录无法验证' : '尚无私有连接检查记录'}>
      等待采集端执行检查；页面只读取结果。</Empty> : <div className="s-demo-replay">
      <h3>{historical ? '历史检查结果' : report.connectivity_status === 'CHECKS_PASSED' ? '账户与私有连接检查通过' :
        report.connectivity_status === 'FAILED' ? '自动检查未通过' : '尚未请求私有检查'}</h3>
      <p className="s-demo-description">检查于 {stamp(report.observed_at_ms, true)}。{historical && '记录已过期或刷新失败，不能代表当前连接状态。'}</p>
      <div className="s-demo-comparison"><span>连接 {report.websocket_connections} / 2</span><span>心跳响应 {report.websocket_pongs} / 2</span>
        <span>观察事件 {report.websocket_events}</span><span>订单提交：未接通</span><span>现金对账：未知</span></div>
      <p className="s-demo-description">{report.event_delivery_status === 'UNKNOWN' ? '未观察到私有事件，成交回执投递仍待验证。' : '已观察到私有事件，逐笔成交与账本绑定仍需独立核实。'}
        连接和重连通过不代表下单、成交补拉或账本对账通过。近期成交查询窗口为检查前 15 分钟。</p>
      <div className="s-table-wrap s-demo-table s-demo-private-table" role="region" aria-label="自动联调步骤" tabIndex={0}><table className="s-table">
        <thead><tr><th>检查项目</th><th>本次结果</th><th>返回条数</th></tr></thead>
        <tbody>{report.steps.map(s => <tr key={s.name}><td>{names[s.name] ?? s.name}</td><td>{s.status === 'PASSED' ? '通过' : '失败'}
          {s.status === 'FAILED' && <small className="s-demo-cell-note">{s.reason}</small>}</td><td>{s.rows ?? '—'}</td></tr>)}</tbody>
      </table></div>
      <details className="s-demo-identities"><summary>检查来源</summary><dl><div><dt>程序摘要</dt><dd>{report.source_sha256}</dd></div></dl></details>
    </div>}
  </Panel>;
}
