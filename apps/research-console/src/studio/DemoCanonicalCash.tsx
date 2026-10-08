import {DemoCashChain} from './DemoCashChain';
import type {DemoCashChainSnapshot} from '@/lib/types/demo-cash-chain';
import type {DemoCanonicalCashSnapshot} from '@/lib/types/demo-canonical-cash';
import {Empty, Metric, Panel} from './components';
import {stamp} from './data';
import {DemoClockEvidence} from './DemoClockEvidence';

const issues: Record<string,string> = {
  ACCOUNT_NAMESPACE_MISMATCH:'现金窗口和成交回执来自不同账户命名空间。',
  RECEIPT_PROJECTION_UNRESOLVED:'成交回执尚不满足唯一账本回放条件。',
  FLAT_SINGLE_QUOTE_START_REQUIRED:'本次核对需要该计价币种的已声明空仓起点；旧仓位成本不会估填。',
  ORDERS_NOT_TERMINAL:'仍有订单未取得终态，先完成订单恢复。',
  QUOTE_WALLET_MISSING:'缺少同币种期初或期末钱包。',
  OPENING_WALLET_MISMATCH:'窗口期初钱包与冻结账本起点不一致。',
  RECEIPT_WINDOW_DOES_NOT_COVER_CASH:'回执窗口未覆盖这次现金查询，需要同一窗口的配套记录。',
  ORDER_OUTSIDE_CASH_INTERIOR:'订单位于钱包采样边界之外，不能归入本次核对。',
  TRADE_OUTSIDE_CASH_INTERIOR:'成交时间接近或超出钱包采样边界，现金归属尚不明确。',
  UNSUPPORTED_INCOME_TYPE:'存在当前账本尚未支持的资金流水类型。',
  UNBOUND_SAME_QUOTE_SYMBOL:'发现同币种其他合约流水，不能忽略后宣称账户闭合。',
  COMMISSION_TRADE_SCOPE_MISMATCH:'佣金流水尚未逐笔对应成交，需补齐或核查交易编号。',
  REPORTED_COMMISSION_DIFFERENCE:'回执手续费与对应佣金流水不一致。',
  REALIZED_PNL_TRADE_SCOPE_MISMATCH:'已实现盈亏流水未绑定本批成交。',
  REPORTED_REALIZED_PNL_DIFFERENCE:'已实现盈亏流水与账本计算不一致。',
  CANONICAL_WALLET_DIFFERENCE:'期末钱包与唯一账本存在差额。',
  CASH_ENDPOINT_EVENT_AMBIGUITY:'采样边界附近存在流水，归属期初或期末尚不明确。',
  CASH_INCOME_SWEEPS_DIFFER:'两轮现金查询内容不同，继续保留未知。',
  CASH_CAPTURE_INCOMPLETE:'现金采集未完整结束，请查看下方读取与失败记录。',
  DECLARED_POSITION_MISMATCH:'账本期末数量与声明目标不符。',
  RECEIPT_SNAPSHOT_CHANGED:'读取期间回执已推进，请刷新以比较同一份记录。',
  CASH_BEFORE_BOUND_OPENING:'存在账本起点之前的资金流水，不能重复计入。',
  CASH_CLOCK_INTERIOR_UNRESOLVED:'资金流水的可能时间区间触及钱包采样边界，暂不能归入本次现金核对。',
};
const titles = {MATCHED_REPORTED_INPUTS:'报告输入与唯一账本相符',DIFFERENCE_FOUND:'唯一账本核对存在差额',UNRESOLVED:'唯一账本核对待补齐'};

export function DemoCanonicalCash({snapshot, chain, refreshFailed}: {snapshot?: DemoCanonicalCashSnapshot; chain?: DemoCashChainSnapshot; refreshFailed: boolean}) {
  const r=snapshot?.report;
  return <Panel title="唯一账本现金核对" note="逐笔佣金匹配 · 资金费一次记账 · 同币种钱包比较">
    {!r ? <Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE' ? '配套账本输入无法验证' : snapshot?.status==='DATA_BLOCKED' ? '配套记录时间待核查' : '等待配套成交与现金窗口'}>
      同一账户、同一窗口的冻结订单回执和现金观察齐备后，展示唯一账本计算与场所报告的差额。
    </Empty> : <div className="s-demo-replay">
      <div className="s-demo-replay-heading"><h3>{titles[r.status]}</h3><span className="s-demo-source">{r.symbol} · {r.quote_asset}</span></div>
      <p className="s-demo-description">固定输入截至 {stamp(r.observed_at_ms,true)}。数值相符仅说明这些报告输入可重放，不代表策略盈利或完整场所验收。</p>
      {refreshFailed && <p role="status">刷新失败，以下保留上次成功读取的核对记录。</p>}
      <DemoClockEvidence clock={r.clock}/>
      {!!r.issues.length && <div className="s-demo-notice" role="status"><ul>{r.issues.map(i=><li key={i}>{issues[i] ?? `核对未完成（${i}）`}</li>)}</ul></div>}
      <div className="s-metrics s-demo-origin-metrics">
        <Metric label="账本期末钱包" value={r.canonical_wallet ?? '待核对'} note={r.quote_asset}/>
        <Metric label="场所报告期末钱包" value={r.reported_wallet ?? '待核对'} note="固定窗口观察"/>
        <Metric label="钱包差额" value={r.wallet_residual ?? '待核对'} note="场所报告 − 唯一账本"/>
      </div>
      <div className="s-demo-comparison">
        <span>佣金逐笔比较：{r.fee_comparison==='MATCHED_REPORTED_TRADES' ? '报告相符' : r.fee_comparison==='NO_REPORTED_TRADES' ? '窗口内未报告成交' : '待核对'}</span>
        <span>已实现盈亏比较：{r.realized_pnl_comparison==='MATCHED_REPORTED_TOTAL' ? '报告总额相符' : r.realized_pnl_comparison==='DIFFERENCE_FOUND' ? '存在差额' : '待核对'}</span>
      </div>
      {r.reconciliation && <>
        <div className="s-table-wrap s-demo-table s-demo-cash-wallet s-demo-canonical-costs" role="region" aria-label="唯一账本成本与外部资金" tabIndex={0}>
          <table className="s-table"><thead><tr><th>项目</th><th>金额（{r.quote_asset}）</th><th>计入口径</th></tr></thead><tbody>
            {[
              ['手续费',r.reconciliation.fee,'按成交回执计入；佣金流水只做比较'],
              ['资金费',r.reconciliation.funding,'按报告流水编号一次计入'],
              ['外部资金',r.reconciliation.external_cash_flow,'转入转出单独列示，不计为交易收益'],
              ['价格与资金费净变化',r.reconciliation.trading_and_funding_change,'保留输入估值口径，不是可支取利润'],
            ].map(([label,value,note])=><tr key={label}><td data-label="项目">{label}</td><td data-label="金额">{value}</td><td data-label="计入口径">{note}</td></tr>)}
          </tbody></table>
        </div>
        <p className="s-demo-description">金额保留十进制原值，佣金与已实现盈亏不会从资金流水再扣一次。资金变化与完整盈利结论分别核验。</p>
      </>}
      <details className="s-demo-identities"><summary>查看账本与输入绑定</summary>
        <p>保留 {r.cash_input_duplicates} 条重复现金输入；每个有效流水身份只计一次。</p>
        <dl>{[
          ['冻结订单',r.manifest_hash],['回执记录',r.journal_tail],['现金窗口',r.cash_capture_sha256],
          ['账本状态',r.ledger_state_hash ?? '尚未形成'],['账户命名空间',r.account_ref_sha256],
        ].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <p>输入来源、私有流完整覆盖与钱包原子快照尚未经独立证明；已有匹配不会改变交易准入。</p>
      </details>
    </div>}
    <DemoCashChain snapshot={chain} refreshFailed={refreshFailed}/>
  </Panel>;
}
