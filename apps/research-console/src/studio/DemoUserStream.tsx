import {useState} from 'react';
import type {DemoUserStreamSnapshot} from '@/lib/types/demo-user-stream';
import {Empty, Metric, Panel} from './components';
import {stamp} from './data';

const labels: Record<string,string> = {
  CONNECTING:'正在建立连接',CONNECTED:'持续连接中',DISCONNECTED:'连接有缺口',STOPPED:'采集已停止',STALE:'心跳已过期',
  SESSION_START:'会话开始',HEARTBEAT:'心跳确认',LEASE_RENEWED:'租约续期',MESSAGE:'场所事件',GAP:'数据缺口',
  ACCOUNT_UPDATE:'账户变化',ORDER_TRADE_UPDATE:'订单与成交',ACCOUNT_CONFIG_UPDATE:'账户配置变化',
  MARGIN_CALL:'保证金事件',TRADE_LITE:'成交摘要',listenKeyExpired:'租约已失效',
  UNCLEAN_RESTART:'上次进程未正常结束',RESTART_WINDOW:'进程重启窗口',CONNECT_FAILED:'连接建立失败',
  STREAM_DISCONNECTED:'连接中断',LEASE_EXPIRED:'租约已失效',KEEPALIVE_FAILED:'续期失败',MESSAGE_REJECTED:'消息未通过解析',
  OUT_OF_ORDER_EVENT:'同类事件时钟倒序',CLOCK_REGRESSION:'本地时钟回退',RECONNECT_BUDGET_EXHAUSTED:'本轮重连次数已用完',
  BUDGET_EXHAUSTED:'本轮采集预算结束',SIGNAL:'收到停止信号',AUTHENTICATION_REJECTED:'账户认证被拒绝',RATE_LIMITED:'场所限流',FAILED:'本轮采集失败',
};
const explain = (v: string) => labels[v] ?? v;

export function DemoUserStream({snapshot, refreshFailed}: {snapshot?: DemoUserStreamSnapshot; refreshFailed: boolean}) {
  const [filter,setFilter] = useState('ALL');
  const r = snapshot?.report;
  const rows = r?.recent.filter(row => filter === 'ALL' || row.kind === filter).slice().reverse() ?? [];
  return <Panel title="持续账户与订单事件" note="DEMO 私有事件 · 按实际接收顺序保存">
    {!r ? <Empty title={snapshot?.status === 'CORRUPT_OR_UNREADABLE' ? '事件日志校验失败' : snapshot?.status === 'DATA_BLOCKED' ? '事件时间需要检查' : '等待持续事件采集'}>
      采集端启动后，这里显示连接、账户变化、订单回执与断线记录。
    </Empty> : <>
      <div className="s-metrics s-demo-metrics">
        <Metric label="连接状态" value={refreshFailed ? '当前状态待更新' : explain(r.state)} note={`最近记录 ${stamp(r.observed_at_ms,true)}`}/>
        <Metric label="订单事件" value={String(r.event_counts.ORDER_TRADE_UPDATE ?? 0)} note="包含状态通知，不等于成交笔数"/>
        <Metric label="账户事件" value={String(r.event_counts.ACCOUNT_UPDATE ?? 0)} note="只含变化条目，不是完整账户快照"/>
        <Metric label="保留的数据缺口" value={String(r.gaps)} note={`${r.sessions} 次会话 · ${r.connections} 次连接`}/>
      </div>
      {!r.messages && <p>尚未收到任何账户或订单事件。心跳只证明连接回应，事件交付仍待观察。</p>}
      <p className="s-demo-description">累计 {r.messages} 条消息，{r.unique_messages} 份不同内容，重复接收 {r.messages-r.unique_messages} 次。重复消息保留在输入记录中，不在这里记账或扣费。</p>
      {!!r.gaps && <div className="s-demo-notice" role="status"><strong>历史缺口仍保留</strong><p>最近：{r.last_gap ? `${stamp(r.last_gap.at_ms,true)} · ${explain(r.last_gap.reason)}` : '未知'}。重连后的心跳不会消除断线期间的缺口。</p></div>}
      <p className="s-demo-description">事件流没有完整历史序号；账户变化只包含更新的资产或仓位。当前历史完整性和账户现金对账均未验收。</p>
      <div className="s-demo-stream-filter"><label htmlFor="demo-stream-filter">事件筛选</label><select id="demo-stream-filter" className="s-input" value={filter} onChange={e => setFilter(e.target.value)}>
        <option value="ALL">全部记录</option><option value="MESSAGE">账户与订单事件</option><option value="GAP">断线与数据缺口</option>
      </select><span>最近 {r.recent.length} / {r.records} 条记录</span></div>
      {rows.length ? <div className="s-table-wrap s-demo-table" role="region" aria-label="账户与订单事件时间线" tabIndex={0}><table className="s-table"><thead><tr>
        <th>接收时间</th><th>类型</th><th>事件内容</th><th>场所时间</th>
      </tr></thead><tbody>{rows.map(row => <tr key={row.sequence}>
        <td>{stamp(row.at_ms,true)}<small className="s-demo-cell-note">序号 {row.sequence}</small></td>
        <td>{row.kind === 'CONNECTED' ? '连接已建立' : row.kind === 'STOPPED' ? '会话结束' : explain(row.event_kind ?? row.kind)}</td>
        <td>{row.reason ? explain(row.reason) : row.order_id ? <>
          <strong>{row.symbol} · {row.order_status}</strong><small className="s-demo-cell-note">订单 {row.order_id}</small>
          <span>本次成交量 {row.last_quantity ?? '未知'} · 报告手续费 {row.commission === null ? '未知' : `${row.commission} ${row.commission_asset ?? '币种未知'}`}</span>
        </> : row.account_reason ? `${row.account_reason}${row.symbol ? ` · ${row.symbol}` : ''}` : '—'}</td>
        <td>{stamp(row.event_ms,true)}{row.transaction_ms !== null && <small className="s-demo-cell-note">事务 {stamp(row.transaction_ms,true)}</small>}</td>
      </tr>)}</tbody></table></div> : <Empty title="当前筛选下没有记录">可切换筛选查看连接状态或其他事件。</Empty>}
      <details className="s-demo-identities"><summary>记录来源与校验范围</summary>
        <p>本地最近 100 条记录与日志清单已校验；完整导出另行检查全链。哈希用于本地完整性检查，不构成场所签名认证。</p>
        <p>采集代码：<code>{r.source_sha256}</code></p><p>日志末尾：<code>{r.head_sha256}</code></p>
        <p>账户引用：<code>{r.account_ref_sha256}</code>（当前 DEMO 凭证的不可逆引用，换密钥需另建日志）</p>
      </details>
    </>}
  </Panel>;
}
