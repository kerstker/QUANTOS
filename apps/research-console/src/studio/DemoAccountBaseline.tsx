import {useState} from 'react';
import type {DemoAccountBaselineSnapshot} from '@/lib/types/binance-demo';
import {Empty, Panel} from './components';
import {stamp} from './data';

const comparisons: Record<string, string> = {wallet: '钱包余额', positions: '持仓数量', account_config: '账户模式', symbol_config: '保证金与杠杆配置'};
const issues: Record<string, string> = {
  ACCOUNT_CHANGED_DURING_CAPTURE: '采集期间账户发生变化，需要新的起点观察。',
  HEDGE_MODE_UNSUPPORTED_BY_SINGLE_POSITION_LEDGER: '账户为双向持仓模式，当前单仓位账本尚不支持。',
  MULTI_ASSET_MARGIN_REQUIRES_COLLATERAL_MODEL: '账户使用多资产保证金，需要相应的抵押品模型。',
  VENUE_ACCOUNT_TRADING_DISABLED: '场所返回账户不可交易。', POSITION_CONFIGURATION_MISSING: '部分仓位缺少保证金或杠杆配置。',
  POSITION_MARGIN_ASSET_MISSING: '部分仓位的保证金币种未出现在资产清单。', POSITION_CONTRACT_REQUIRES_REVIEW: '部分已有合约需要单独核对合约规则。',
  PREEXISTING_ORDERS_REQUIRE_OWNERSHIP_ISOLATION: '存在已有普通挂单，后续必须区分订单归属。',
  ENTRY_BASIS_UNVERIFIED: '场所开仓均价用于成本估算；精确历史成本基础尚未验证。',
  REST_OBSERVATIONS_NOT_ATOMIC: '分次查询即使数值一致，也不能证明账户在同一瞬间完整冻结。',
};
const reads: Record<string, string> = {config_before: '起始账户模式', account_before: '起始余额与仓位', symbols_before: '起始合约配置',
  positions: '持仓价格与保证金', orders: '当前普通挂单', symbols_after: '结束合约配置', account_after: '结束余额与仓位', config_after: '结束账户模式'};

export function DemoAccountBaseline({snapshot, refreshFailed}: {snapshot?: DemoAccountBaselineSnapshot; refreshFailed: boolean}) {
  const [query, setQuery] = useState('');
  const r = snapshot?.report;
  const historical = r?.inventory_status === 'STALE' || refreshFailed;
  const complete = r?.capture_reason === 'CAPTURE_COMPLETE';
  const positions = r?.positions.filter(p => p.symbol.toLocaleUpperCase().includes(query.trim().toLocaleUpperCase())) ?? [];
  return <Panel title="账户起点" note="已有资产与持仓 · 收益计算的前置核对">
    {!r ? <Empty title={snapshot?.status === 'CORRUPT_OR_UNREADABLE' ? '账户起点记录无法验证' : snapshot?.status === 'DATA_BLOCKED' ? '账户起点时间无效' : '尚无账户起点记录'}>
      完整采集后展示余额、开仓均价和保证金模式。</Empty> : <div className="s-demo-replay">
      <div className="s-demo-replay-heading"><h3>{historical ? '历史账户起点' : r.inventory_status === 'STABLE_OBSERVATIONS' ? '采集前后数值一致' :
        r.inventory_status === 'CHANGED_DURING_CAPTURE' ? '采集期间账户发生变化' : '账户起点采集未完成'}</h3>
        <span className="s-demo-source">{complete ? '账户观察' : `${r.read_intervals.length} / 8 项已读取`}</span></div>
      <p className="s-demo-description">采集于 {stamp(r.observed_at_ms, true)}，历时 {((r.completed_at_ms - r.observed_at_ms) / 1000).toFixed(2)} 秒。
        {historical && '记录已过期或刷新失败，只能查看历史状态。'}已有仓位的浮盈亏不计入本次策略收益，现金账本对账仍为未知。挂单统计仅覆盖普通订单接口。</p>
      {!complete && <p className="s-demo-description">采集原因：{r.capture_reason}。已完成的读取保留在采集记录中，不补成空账户。</p>}
      <div className="s-demo-comparison"><span>已有持仓 {r.nonzero_positions ?? '未知'}</span><span>已有普通挂单 {complete ? r.open_orders.length : '未知'}</span>
        <span>持仓模式 {r.account_config ? r.account_config.dualSidePosition ? '双向' : '单向' : '未知'}</span>
        <span>保证金资产模式 {r.account_config ? r.account_config.multiAssetsMargin ? '多资产' : '单资产' : '未知'}</span></div>
      {!!r.comparisons.length && <ul className="s-demo-baseline-checks" aria-label="采集前后核对">{r.comparisons.map(c => <li key={c.kind}>
        <span>{comparisons[c.kind]}</span><strong>{c.status === 'MATCHED_OBSERVATIONS' ? '观察一致' : '存在差异'}</strong></li>)}</ul>}
      {!!r.issues.length && <ul className="s-demo-baseline-issues">{r.issues.map(i => <li key={i}>{issues[i] ?? i}</li>)}</ul>}
      {complete && <>
        <div className="s-demo-baseline-heading"><h4 className="s-demo-subheading">已有持仓 <small>显示 {positions.length} / {r.positions.length}</small></h4>
          <label className="s-demo-filter">筛选合约<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="BTCUSDT" maxLength={32}/></label></div>
        {positions.length ? <div className="s-table-wrap s-demo-table" role="region" aria-label="账户起点持仓表" tabIndex={0}><table className="s-table">
          <thead><tr><th>合约 / 持仓侧</th><th>数量</th><th>开仓均价</th><th>标记价格</th><th>场所浮盈亏</th><th>浮盈亏差额 *</th><th>保证金模式</th><th>逐仓钱包</th></tr></thead>
          <tbody>{positions.map(p => <tr key={`${p.symbol}:${p.position_side}`}><td><strong>{p.symbol}</strong><small className="s-demo-cell-note">{p.position_side} · 已有仓位</small></td>
            <td>{p.quantity}</td><td>{p.observed_entry_price}</td><td>{p.observed_mark_price}</td><td>{p.observed_unrealized}<small className="s-demo-cell-note">{p.margin_asset}</small></td>
            <td>{p.basis_pnl_residual}</td><td>{p.margin_mode === 'CROSSED' ? '全仓' : p.margin_mode === 'ISOLATED' ? '逐仓' : '未知'}
              <small className="s-demo-cell-note">{p.leverage === null ? '杠杆未知' : `配置杠杆 ${p.leverage}×`}</small></td><td>{p.isolated_wallet}</td></tr>)}</tbody>
        </table></div> : <Empty title={r.positions.length ? '没有匹配的合约' : '本次未观察到非零仓位'}>{r.positions.length ? '调整合约筛选条件。' : '请结合采集前后核对状态判断完整性。'}</Empty>}
        <p className="s-demo-description">* 浮盈亏差额 = 数量 ×（标记价格 − 开仓均价）− 场所浮盈亏，单位为该合约保证金币种。保留均价精度和采样时间造成的差额，不强制归零。</p>
        <details className="s-demo-identities"><summary>按币种核对余额 · {r.assets.length} 种资产</summary>
          <div className="s-table-wrap s-demo-table" role="region" aria-label="账户起点资产表" tabIndex={0}><table className="s-table">
            <thead><tr><th>资产</th><th>钱包总额</th><th>全仓钱包</th><th>未实现盈亏</th><th>保证金余额</th><th>余额恒等式差额</th></tr></thead>
            <tbody>{r.assets.map(a => <tr key={a.asset}><td>{a.asset}</td><td>{a.observed_wallet}</td><td>{a.observed_cross_wallet}</td>
              <td>{a.observed_unrealized}</td><td>{a.observed_margin_balance}</td><td>{a.account_identity_residual}</td></tr>)}</tbody>
          </table></div><p>余额恒等式差额 = 钱包总额 + 未实现盈亏 − 保证金余额。各币种分别展示，不汇总为美元或策略收益。</p>
        </details>
        {!!r.open_orders.length && <details className="s-demo-identities"><summary>已有普通挂单 · {r.open_orders.length} 笔</summary>
          <div className="s-table-wrap s-demo-table" role="region" aria-label="账户起点挂单表" tabIndex={0}><table className="s-table"><thead><tr>
            <th>合约</th><th>订单编号</th><th>方向</th><th>委托数量</th><th>已成交</th><th>状态</th></tr></thead><tbody>{r.open_orders.map(o =>
            <tr key={`${o.symbol}:${o.orderId}`}><td>{o.symbol}</td><td>{o.orderId}</td><td>{o.side}</td><td>{o.origQty}</td><td>{o.executedQty}</td><td>{o.status}</td></tr>)}</tbody></table></div>
        </details>}
      </>}
      <details className="s-demo-identities"><summary>采集时间与来源</summary><dl><div><dt>记录摘要</dt><dd>{r.capture_hash}</dd></div>
        <div><dt>程序摘要</dt><dd>{r.source_sha256}</dd></div>{r.read_intervals.map(i => <div key={i.kind}><dt>{reads[i.kind]}</dt>
          <dd>{stamp(i.started_at_ms, true)} → {stamp(i.received_at_ms, true)}<small className="s-demo-cell-note">{i.response_sha256}</small></dd></div>)}</dl></details>
    </div>}
  </Panel>;
}
