import type {DemoCashChainSnapshot} from '@/lib/types/demo-cash-chain';
import {Empty, Metric} from './components';

const titles={MATCHED_REPORTED_INPUTS:'跨批次账本输入相符',DIFFERENCE_FOUND:'跨批次核对存在差额',UNRESOLVED:'跨批次连续性待补齐'};
const issues:Record<string,string>={
  RECEIPT_CHAIN_GAP_OR_ORDER:'批次窗口存在断档或顺序不符，尚不能连续核对。',
  CONTINUATION_OBSERVATION_REQUIRED:'后续批次缺少配套账户观察，持仓成本不会估填。',
  CONTINUATION_WALLET_MISMATCH:'后续账户钱包与原账本累计现金不符。',
  CONTINUATION_EXACT_BASIS_MISMATCH:'后续持仓数量或精确成本与原账本不符。',
  CONTINUATION_POSITION_MISMATCH:'后续持仓范围与原账本不符。',
  CONTINUATION_BASIS_SOURCE_MISMATCH:'后续成本来源尚未绑定原账本状态。',
  CONTINUATION_EVENT_OVERLAPS_OBSERVATION:'账户观察期间可能发生资金或成交变化，连续性尚不明确。',
  CHAIN_REPLAY_REPORTEDFILLREPLAYERROR:'报告成交超出当前账本支持条件，请核查原始规则、容量和成交。',
  CAPACITY_CHANGED_IN_CHAIN:'同一计量窗口的容量规则发生变化，不能重置预算后继续。',
  CHAIN_DOES_NOT_COVER_CASH:'连续回执范围未覆盖整个现金窗口。',
  CANONICAL_WALLET_DIFFERENCE:'期末钱包与累计账本存在差额。',
  REPORTED_REALIZED_PNL_DIFFERENCE:'报告已实现收益与原持仓成本计算不符。',
};
const stamp=(at:number)=>new Date(at).toISOString().replace('T',' ').replace('Z',' UTC');

export function DemoCashChain({snapshot,refreshFailed}:{snapshot?:DemoCashChainSnapshot;refreshFailed:boolean}){
  const r=snapshot?.report;
  return <section className="s-demo-cash-chain" aria-label="跨批次现金与持仓">
    <h3>跨批次现金与持仓</h3>
    {!r?<Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'连续账本记录无法验证':snapshot?.status==='DATA_BLOCKED'?'连续账本时间待核查':'等待连续批次与现金窗口'}>
      后续批次沿原账本累计持仓、精确成本和费用；账户观察与现金窗口齐备后展示核对结果。
    </Empty>:<div className="s-demo-replay">
      <div className="s-demo-replay-heading"><h4>{titles[r.phase]}</h4><span className="s-demo-source">{r.symbol??'币种待核查'} · {r.batch_count} 个批次</span></div>
      <p className="s-demo-description">固定输入截至 {stamp(r.observed_at_ms)}。后续批次没有重置原始本金或累计费用；结果不是盈利或实际交易准入证明。</p>
      {refreshFailed&&<p role="status">刷新失败，保留上次读取的固定窗口结果，不能视为当前账户持仓。</p>}
      {!!r.issues.length&&<div className="s-demo-notice" role="status"><ul>{r.issues.map(i=><li key={i}>{issues[i]??`连续核对未完成（${i}）`}</li>)}</ul></div>}
      <div className="s-metrics s-demo-origin-metrics">
        <Metric label="原始账本净值" value={r.initial_equity??'待核对'} note={r.quote_asset??'币种待核查'}/>
        <Metric label="累计期末钱包" value={r.canonical_wallet??'待核对'} note="保持同一个账本起点"/>
        <Metric label="钱包差额" value={r.wallet_residual??'待核对'} note="场所报告 − 累计账本"/>
      </div>
      <div className="s-table-wrap s-demo-table s-demo-cash-wallet s-demo-canonical-costs" role="region" aria-label="跨批次持仓和成本" tabIndex={0}>
        <table className="s-table"><thead><tr><th>项目</th><th>数值</th><th>口径</th></tr></thead><tbody>
          {[
            ['期末持仓数量',r.quantity,'BTC；固定输入的期末状态'],
            ['剩余精确持仓成本',r.entry_notional,`${r.quote_asset??'计价币种'}；原成交成本按比例保留`],
            ['累计手续费',r.fee,'各批成交回执只计一次'],['累计资金费',r.funding,'按流水身份记账'],
            ['累计外部资金',r.external_cash_flow,'不计入交易收益'],
          ].map(([label,value,note])=><tr key={label}><td data-label="项目">{label}</td><td data-label="数值">{value??'待核对'}</td><td data-label="口径">{note}</td></tr>)}
        </tbody></table>
      </div>
      <details className="s-demo-identities"><summary>查看批次衔接与来源</summary>
        <p>{r.continuations_matched} / {Math.max(0,r.batch_count-1)} 个衔接点与原账本前缀相符。输入来源、窗口完整性和非原子账户观察仍各自待证明。</p>
        <ol className="s-demo-chain-batches">{r.batches.map((b,i)=><li key={`${i}-${b.manifest_hash}`}>
          <strong>批次 {i+1} · {b.orders_count} 个订单 · {b.trades_count} 条成交</strong>
          <p>{stamp(b.start_ms)}<br/>至 {stamp(b.end_ms)}</p>
          <dl><div><dt>冻结输入</dt><dd>{b.manifest_hash}</dd></div><div><dt>回执记录</dt><dd>{b.journal_tail}</dd></div></dl>
        </li>)}</ol>
        <dl>{[['完整报告',r.report_sha256],['现金窗口',r.cash_capture_sha256],['累计状态',r.ledger_state_hash??'未形成']].map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </details>
    </div>}
  </section>;
}
