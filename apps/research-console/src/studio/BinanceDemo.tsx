import {fetchBinanceDemo} from '@/lib/api/binance-demo-api';
import type {BinanceDemoPayload} from '@/lib/types/binance-demo';
import {Empty, ErrorNote, Header, Loading, Metric, Panel} from './components';
import {stamp, useResource} from './data';
import './binance-demo.css';
import {DemoExecutionEvidence} from './DemoExecutionEvidence';
import {DemoOrderRecovery} from './DemoOrderRecovery';

const reasons: Record<string, string> = {
  RECEIPT_MISSING: '尚未生成检查快照', RECEIPT_FROM_FUTURE: '采集时间晚于当前时间，请检查时钟',
  RECEIPT_INVALID_OR_UNREADABLE: '快照无法读取或不符合当前契约', PROBE_FAILED: '本次检查有未通过项',
  CREDENTIALS_MISSING: '操作环境未配置测试网凭证', ACCOUNT_NOT_REQUESTED: '本次只检查公共接口',
  PUBLIC_CHECK_FAILED: '等待公共接口检查通过', REQUEST_FAILED: '网络请求失败，请在采集环境检查连接',
  RESPONSE_INVALID: '场所返回的数据不完整', CLOCK_UNRELIABLE: '时钟偏差或请求耗时超出范围',
  SYMBOL_RULES_INVALID: '目标合约或交易规则不符合预期', RATE_LIMITED: '场所限流，请稍后重新采集',
  AUTHENTICATION_REJECTED: '场所拒绝账户查询，请核对测试网凭证与访问权限',
  REDIRECT_REFUSED: '场所地址发生重定向，检查已停止', RESPONSE_TOO_LARGE: '场所响应超出大小限制',
  REQUEST_TIMEOUT: '整次检查超时，已停止采集；请稍后重试',
};
const explain = (reason: string | undefined) => reason ? reasons[reason] ?? reason : '等待检查';

export function BinanceDemo() {
  const resource = useResource('studio:binance-demo', fetchBinanceDemo, 15000);
  return <DemoReadinessView {...resource}/>;
}

export function DemoReadinessView({data, loading, error, refresh}: {
  data: BinanceDemoPayload | null; loading: boolean; error: string | null; refresh: () => void;
}) {
  const current = !!data && !error && data.snapshot_status !== 'STALE' && data.age_ms !== null && data.age_ms <= data.max_age_ms;
  const publicReady = current && data?.public?.status === 'READY';
  const accountReady = current && data?.account?.status === 'READY';
  const historical = !!data?.public && !current;
  const publicTitle = publicReady ? '公共接口可达' : historical ? '等待重新检查' : data?.public?.status === 'FAILED' ? '公共接口检查失败' : '公共接口待检查';
  const accountTitle = accountReady ? '账户查询成功' : historical ? '账户状态待更新' : data?.account?.status === 'FAILED' ? '账户查询未通过' : '账户尚未查询';
  return <>
    <Header eyebrow="EXECUTION / BINANCE DEMO" title="模拟盘接入" action={
      <button className="s-button" onClick={refresh} disabled={loading}>{loading ? '正在刷新…' : '刷新快照'}</button>
    }>从公共连接、账户读取到订单回执与对账，逐步核实执行链路。</Header>
    <ErrorNote error={error} retry={refresh}/>
    {loading && !data && <Loading/>}
    <div className="s-demo-environment"><span>BINANCE USDⓈ-M · DEMO</span><span>虚拟资金 · 接入诊断</span></div>
    <ol className="s-demo-steps" aria-label="模拟盘接入步骤">
      {[
        [publicTitle, publicReady ? 'BTCUSDT / BTCUSDC 合约规则已读取' : explain(data?.public?.reason), publicReady],
        [accountTitle, accountReady ? '按资产与合约展示场所返回值' : explain(data?.account?.reason), accountReady],
        ['订单回执待联调', '连接恢复、成交与撤单轨迹尚未验收', false],
        ['账本对账待验证', '等待逐单回执与唯一账本比较', false],
      ].map(([title, description, ready], index) => <li key={String(title)} className={ready ? 'verified' : ''}>
        <span className="s-demo-step-number">{String(index + 1).padStart(2, '0')}</span>
        <h2>{title}</h2><p>{description}</p>
      </li>)}
    </ol>
    {data?.execution.report && <div className="s-demo-receipt-jump"><span>已导入 {data.execution.report.journal_records} 条回执</span>
      <a className="s-button" href="#demo-execution-evidence">查看成交回执与恢复 ↓</a>
      {data.order_recovery.report && <a className="s-button" href="#demo-order-recovery">查看订单恢复核对 ↓</a>}</div>}
    {data?.snapshot_status === 'STALE' && <div className="s-demo-notice" role="status"><strong>快照已过期</strong><p>以下数值来自上次采集，不能表示当前连接或账户状态。刷新按钮只读取已有快照；需要采集端重新检查。</p></div>}
    {data && ['CORRUPT_OR_UNREADABLE', 'DATA_BLOCKED'].includes(data.snapshot_status) && <div className="s-demo-notice" role="status">{explain(data.data_quality.reason)}</div>}
    {(!data || data.snapshot_status === 'NO_SNAPSHOT') && !loading && <Empty title="尚无接入检查快照">运行公共接入检查后，这里会显示场所时钟和合约规则。账户信息保持未知。</Empty>}
    <div className="s-metrics s-demo-metrics">
      <Metric label="公共时间接口往返" value={data?.public?.round_trip_ms == null ? '—' : `${data.public.round_trip_ms} ms`} note="单次 REST 诊断，不代表下单延迟"/>
      <Metric label="场所时钟偏差" value={data?.public?.clock_offset_ms == null ? '—' : `${data.public.clock_offset_ms} ms`} note="相对请求中点的估计值"/>
      <Metric label="快照年龄" value={data?.age_ms == null ? '—' : `${Math.floor(data.age_ms / 1000)} 秒`} note="有效期 120 秒"/>
      <Metric label="交易执行" value="未启用" note="公共检查不授予下单权限"/>
    </div>
    <Panel title="合约与计价" note={`${historical ? '历史观察 · ' : ''}BTCUSDT 与 BTCUSDC 分别保留数量、价格与保证金币种`}>
      {data?.public?.symbols.length ? <div className="s-table-wrap s-demo-table" role="region" aria-label="合约规则表" tabIndex={0}><table className="s-table"><thead><tr>
        <th>合约</th><th>报价 / 保证金</th><th>价格步长</th><th>数量步长</th><th>最小数量</th><th>最小名义金额</th>
      </tr></thead><tbody>{data.public.symbols.map(s => <tr key={s.symbol}><td><strong>{s.symbol}</strong><small className="s-demo-cell-note">永续 · 场所可交易</small></td>
        <td>{s.quote_asset} / {s.margin_asset}</td><td>{s.tick_size}</td><td>{s.step_size}</td><td>{s.min_qty}</td><td>{s.min_notional} {s.quote_asset}</td>
      </tr>)}</tbody></table></div> : <Empty title="尚无有效合约规则">等待公共接口检查成功。</Empty>}
    </Panel>
    <div className="s-analytics-grid">
      <Panel title="模拟账户资产" note={`${historical ? '历史观察 · ' : ''}按原币种展示；不合并为美元收益`}>
        {data?.account?.status === 'READY' ? data.account.assets.length ? <div className="s-table-wrap s-demo-table" role="region" aria-label="模拟资产表" tabIndex={0}><table className="s-table"><thead><tr>
          <th>资产</th><th>钱包余额</th><th>可用余额</th><th>未实现盈亏</th>
        </tr></thead><tbody>{data.account.assets.map(a => <tr key={a.asset}><td>{a.asset}</td><td>{a.wallet_balance}</td><td>{a.available_balance}</td><td>{a.unrealized_profit}</td></tr>)}</tbody></table></div>
          : <Empty title="场所未返回资产条目">本次账户查询成功，未返回资产明细。</Empty>
          : <Empty title="账户数据未知">需由操作员在独立采集环境发起账户只读检查。本页面不接收密钥。</Empty>}
      </Panel>
      <Panel title="模拟账户持仓" note="场所快照；尚未与 QUANTOS 账本对账">
        {data?.account?.status === 'READY' ? data.account.positions.length ? <div className="s-table-wrap s-demo-table" role="region" aria-label="模拟持仓表" tabIndex={0}><table className="s-table"><thead><tr>
          <th>合约</th><th>持仓侧</th><th>数量</th><th>未实现盈亏</th>
        </tr></thead><tbody>{data.account.positions.map(p => <tr key={`${p.symbol}:${p.position_side}`}><td>{p.symbol}</td><td>{p.position_side}</td><td>{p.quantity}</td><td>{p.unrealized_profit}</td></tr>)}</tbody></table></div>
          : <Empty title="场所未返回持仓条目">这是本次查询结果，未进行订单或账本对账。</Empty>
          : <Empty title="持仓数据未知">账户查询成功后显示原始数量与持仓侧。</Empty>}
      </Panel>
    </div>
    <div id="demo-execution-evidence" tabIndex={-1}><DemoExecutionEvidence snapshot={data?.execution} refreshFailed={!!error}/></div>
    <div id="demo-order-recovery" tabIndex={-1}><DemoOrderRecovery snapshot={data?.order_recovery} refreshFailed={!!error}/></div>
    <Panel title="下一步：核实执行链路" note="执行条件未满足">
      <div className="s-demo-next"><p>公共接口可用、账户可读，不代表测试网订单已接通。当前提交入口仍缺少独立执行权威集成；下一阶段需完成订单回执、恢复与账本对账的联调。</p>
        <p>Demo 用于验证连接和执行工程。真实费用、真实场所成交校准和成本后盈利仍需各自的证据。</p>
        <a className="s-button" href="/studio/modules/mft-execution">查看订单与对账工作区</a>
      </div>
    </Panel>
    <footer className="s-demo-footer"><span>采集于 {stamp(data?.observed_at_ms, true)} · {data?.rest_base ?? 'https://demo-fapi.binance.com'}</span>
      <span>刷新每 15 秒读取快照，不向交易所发起检查。</span></footer>
  </>;
}
