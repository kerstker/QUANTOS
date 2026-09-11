import type {DemoOpeningAssessment} from '@/lib/types/binance-demo';
import {stamp} from './data';

const issues: Record<string, string> = {EXACT_ENTRY_BASIS_MISSING: '需要精确历史成本，不能用显示均价代替。',
  OPENING_CONTRACT_RULES_MISSING: '部分合约缺少明确的数量、结算和保证金规则。',
  ACCOUNT_CHANGED_DURING_CAPTURE: '采集期间账户发生变化。', HEDGE_MODE_UNSUPPORTED: '当前回放尚不支持双向持仓。',
  MULTI_ASSET_COLLATERAL_UNSUPPORTED: '当前回放尚不支持多资产抵押。', OPENING_MARGIN_MODE_MISSING: '缺少保证金模式。',
  TARGET_CROSS_MARGIN_REQUIRED: '本版目标订单只支持全仓模式。', PREEXISTING_ORDERS_UNRESOLVED: '已有挂单的归属及影响尚未解决。',
  POSITION_MARGIN_ASSET_MISSING: '持仓保证金币种未出现在资产清单。', POSITION_CONTRACT_REQUIRES_REVIEW: '存在尚未支持的持仓合约。'};

export function DemoOpeningEvidence({opening}: {opening: DemoOpeningAssessment}) {
  return <div className="s-demo-opening-evidence">
    <h3 className="s-demo-subheading">回放起点与缺项</h3>
    <p className="s-demo-description">{stamp(opening.opening_at_ms, true)} · {opening.quote_asset} 独立账本。
      {opening.status === 'DECLARED_INPUTS_READY' ? '声明输入足以执行技术回放，来源与账户现金仍未独立核验。' : '起点输入不完整，保留回执并等待补齐。'}</p>
    <div className="s-demo-comparison"><span>起点仓位 {opening.initial_position} BTC</span><span>起点钱包 {opening.initial_collateral} {opening.quote_asset}</span>
      <span>同币种已有仓位 {opening.positions_count}</span><span>其他币种仓位 {opening.other_quote_positions_count}（分别处理）</span></div>
    <p className="s-demo-description">精确成本尚缺 {opening.missing_basis_symbols.length} 个合约；合约规则尚缺 {opening.missing_contract_symbols.length} 个。
      已有浮盈亏不计为本次收益；未导入后续标记价格时保留起点估值，不代表实时净值。</p>
    {!!opening.issues.length && <ul className="s-demo-baseline-issues">{opening.issues.map(i => <li key={i}>{issues[i] ?? `模型尚不支持此起点：${i}`}</li>)}</ul>}
    <details className="s-demo-identities"><summary>逐仓查看起点输入 · {opening.positions_count} 个仓位</summary>
      <div className="s-table-wrap s-demo-table" role="region" aria-label="回放起点输入表" tabIndex={0}><table className="s-table">
        <thead><tr><th>合约</th><th>起点数量</th><th>保证金模式</th><th>声明精确成本</th><th>合约规则</th></tr></thead>
        <tbody>{opening.positions.map(p => <tr key={`${p.symbol}:${p.position_side}`}><td>{p.symbol}<small className="s-demo-cell-note">{p.position_side}</small></td><td>{p.quantity}</td>
          <td>{p.margin_mode === 'CROSSED' ? '全仓' : p.margin_mode === 'ISOLATED' ? '逐仓' : '未知'}</td><td>{p.entry_notional ?? '尚缺'}
            {p.basis_source_sha256 && <small className="s-demo-cell-note">成本来源已绑定 · 未独立验证</small>}</td><td>{p.contract_available ? '声明输入已绑定' : '尚缺'}</td></tr>)}</tbody>
      </table></div>
      {!opening.positions.some(p => p.symbol === `BTC${opening.quote_asset}`) && opening.missing_contract_symbols.includes(`BTC${opening.quote_asset}`) &&
        <p>目标 BTC 合约虽无起点仓位，仍需补齐规则后才能回放其成交。</p>}
      <dl><div><dt>起点快照</dt><dd>{opening.capture_hash}</dd></div><div><dt>声明账户引用</dt><dd>{opening.account_ref_sha256}</dd></div></dl>
      <p>输入声明与内容摘要不证明原子快照、账户身份或完整资金流水。这里只重建已导入的目标订单，不能替代全账户现金对账。</p>
    </details>
  </div>;
}
