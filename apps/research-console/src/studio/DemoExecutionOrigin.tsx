import type {DemoExecutionOriginSnapshot} from '@/lib/types/demo-execution-origin';
import {Empty, Metric, Panel} from './components';
import {stamp} from './data';

const issues: Record<string,string> = {
  CAPTURE_INCOMPLETE:'采集未完成，缺失项目不能视为空仓或零挂单。', ORIGIN_STALE:'观察已过期，执行前需要重新采集。',
  ACCOUNT_NOT_STABLE:'采样期间账户状态不一致。', ACCOUNT_TRADING_UNAVAILABLE:'账户当前不具备交易权限。',
  HEDGE_MODE_UNSUPPORTED:'当前接线要求单向持仓模式。', MULTI_ASSET_MARGIN_UNSUPPORTED:'跨币种抵押尚未支持。',
  TARGET_CROSS_MARGIN_REQUIRED:'目标合约需要全仓模式，空仓时也须核验。',
  PREEXISTING_ORDINARY_ORDERS:'账户存在普通挂单，需要先核对其归属与状态。',
  PREEXISTING_ALGO_ORDERS:'账户存在条件挂单，不能据普通挂单为空启动。',
  QUOTE_BOOK_NOT_FLAT:'目标计价币种仍有已有持仓。', QUOTE_WALLET_NOT_FREE_AND_FLAT:'钱包余额、可用资金或浮盈亏尚未满足起点条件。',
  TARGET_RULES_UNAVAILABLE:'目标合约规则不完整或状态不适用。', MARKET_TIME_UNAVAILABLE:'行情缺失或时间不满足要求。',
  BOOK_NOT_EXECUTABLE:'买卖盘口不可用于本次执行。', MARK_UNAVAILABLE:'标记价格或下一资金费时间待核查。',
  TARGET_CONFIG_MISSING:'目标合约账户额度尚未完整读取。',TARGET_CONFIG_CHANGED:'采集期间合约账户配置发生变化，需要重新核对。',
  TARGET_AUTO_MARGIN_UNSUPPORTED:'自动追加保证金设置不适用于当前固定全仓流程。',TARGET_NOTIONAL_CAP_UNAVAILABLE:'账户没有可用的目标合约名义额度。',
};
export function DemoExecutionOrigin({snapshot,refreshFailed}: {snapshot?: DemoExecutionOriginSnapshot; refreshFailed: boolean}) {
  const r=snapshot?.report;
  const cfg=r?.target_configuration;
  const wallet=r?.wallet?.includes('.') ? r.wallet.replace(/0+$/,'').replace(/\.$/,'') : r?.wallet;
  return <Panel title="执行起点检查" note="按计价币种核对账户、两类挂单与合约条件">
    {!r ? <Empty title={snapshot?.status === 'CORRUPT_OR_UNREADABLE' ? '起点记录无法验证' : snapshot?.status === 'DATA_BLOCKED' ? '起点时间待核查' : '等待执行起点观察'}>
      同一次采集会核对账户身份、普通挂单、条件挂单、目标合约规则和行情。
    </Empty> : <div className="s-demo-origin-content">
      <h3>{r.status === 'OBSERVED_FLAT_QUOTE' && !refreshFailed ? '观察到目标币种空仓' : '执行起点仍需核对'}</h3>
      <p>{r.symbol} · {stamp(r.observed_at_ms,true)} → {stamp(r.completed_at_ms,true)} · 已完成 {r.read_count} / 16 次读取。</p>
      {refreshFailed && <p role="status">刷新失败，以下为历史观察，不能据此确认当前执行条件。</p>}
      <div className="s-metrics s-demo-origin-metrics">
        <Metric label={`${r.quote_asset} 观察钱包`} value={wallet ?? '未知'}/>
        <Metric label="同币种持仓数" value={r.quote_positions ?? '未知'}/>
        <Metric label="普通挂单" value={r.ordinary_orders ?? '未知'}/>
        <Metric label="条件挂单" value={r.algo_orders ?? '未知'}/>
      </div>
      <p>其他计价币种的已有持仓：{r.other_quote_positions ?? '未知'}。初始资金不计作收益；历史持仓继续保留。</p>
      {cfg ? <div className="s-demo-notice">
        <strong>{cfg.status === 'MATCHED_OBSERVATIONS' ? '合约账户配置前后相符' : cfg.status === 'DIFFERENT_OBSERVATIONS' ? '合约账户配置前后不同' : '合约账户配置未完整取得'}</strong>
        <p>后次观察名义额度：{cfg.after?.maxNotionalValue ?? '未知'} {r.quote_asset} · 杠杆设置：{cfg.after?.leverage ?? '未知'} · 保证金模式：{cfg.after ? cfg.after.marginType === 'CROSSED' ? '全仓' : '逐仓' : '未知'}。</p>
        <p>执行检查同时受账户额度、杠杆档位和本批风险预算约束；上述额度不代表可用的交易预算。</p>
      </div> : <p className="s-demo-description">旧版采集未保留合约账户额度，需要新的起点观察。</p>}
      {!!r.issues.length && <div className="s-demo-notice" role="status"><ul>{r.issues.map(i=><li key={i}>{issues[i] ?? i}</li>)}</ul></div>}
      <p className="s-demo-description">结算舍入规则尚未核验。采样一致不等于原子快照或执行授权，系统尚未据此提交订单。</p>
      <details className="s-demo-identities"><summary>查看采集与身份记录</summary>
        <dl><div><dt>账户命名空间</dt><dd>{r.account_ref_sha256 ?? '未读取'}</dd></div>
          <div><dt>钱包原始精度</dt><dd>{r.wallet ?? '未知'}</dd></div>
          <div><dt>采集源码身份</dt><dd>{r.source_sha256}</dd></div></dl>
        {cfg && <dl>{(['before','after'] as const).map(phase=><div key={phase}>
          <dt>{phase === 'before' ? '前次' : '后次'}账户配置</dt><dd>{cfg[phase] ? `${cfg[phase].maxNotionalValue} ${r.quote_asset} · ${cfg[phase].leverage} 倍 · 自动追加保证金${cfg[phase].isAutoAddMargin ? '开启' : '关闭'}` : '未知'}</dd>
        </div>)}</dl>}
        {r.failures.map((f,i)=><p key={i}>{f.kind} · {f.reason} · {stamp(f.received_at_ms,true)}
          {f.server_time_ms !== null && ` · 服务端与请求中点相差 ${f.server_time_ms-Math.floor((f.requested_at_ms+f.received_at_ms)/2)} 毫秒（未通过检查）`}</p>)}
        <p>身份摘要绑定本次输入，不替代独立来源认证。</p>
      </details>
    </div>}
  </Panel>;
}
