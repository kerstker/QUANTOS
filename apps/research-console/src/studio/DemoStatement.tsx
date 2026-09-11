import type {DemoStatementSnapshot} from '@/lib/types/demo-statement';
import {Empty, Panel} from './components';
import {stamp} from './data';

const flowTypes: Record<string, string> = {TRANSFER:'转入转出',COMMISSION:'手续费',REALIZED_PNL:'已实现盈亏',FUNDING_FEE:'资金费',COMMISSION_REBATE:'手续费返还',INSURANCE_CLEAR:'保险清算',WELCOME_BONUS:'奖励'};

export function DemoStatement({snapshot, refreshFailed}: {snapshot?: DemoStatementSnapshot; refreshFailed: boolean}) {
  const r = snapshot?.report;
  return <Panel title="场所费用与资金流水" note="实际账户查询 · 按币种交叉核对">
    {!r ? <Empty title={snapshot?.status === 'CORRUPT_OR_UNREADABLE' ? '流水记录校验失败' : '等待账户流水采集'}>取得手续费、保证金档位、成交历史与资金流水后在此展示。</Empty> : <>
      <p className="s-demo-description">窗口：{stamp(r.start_ms, true)} → {stamp(r.end_ms, true)}。{r.read_count} 次读取，{r.trade_count} 笔历史成交记录，{r.income_count} 笔资金流水。</p>
      {r.failed_attempt_count > 0 && <p>保留了 {r.failed_attempt_count} 次失败请求；单项临时网络错误最多重试两次，未重复已完成的查询。</p>}
      {(snapshot.status === 'STALE' || refreshFailed) && <p role="status">历史采集记录，不能表示当前账户或费率。</p>}
      <p className="s-demo-description">{r.status === 'CAPTURED' ? '本次查询已完成。' : '本次查询尚未完整完成。'}历史窗口不证明完整持仓成本；账户现金对账及策略收益仍待验证。</p>
      <div className="s-table-wrap s-demo-table" role="region" aria-label="实际费用与资金流水表" tabIndex={0}><table className="s-table">
        <thead><tr><th>资产</th><th>成交报告手续费</th><th>流水手续费</th><th>费用核对差额</th><th>成交已实现盈亏</th><th>流水净额</th></tr></thead>
        <tbody>{r.assets.map(a => <tr key={a.asset}><td>{a.asset}</td><td>{a.reported_commission}</td><td>{a.income_commission}</td><td>{a.fee_crosscheck_residual}</td><td>{a.reported_realized_pnl}</td><td>{a.income_total}</td></tr>)}</tbody>
      </table></div>
      {!r.assets.length && <p>窗口内未取得资金记录，不能据此认定账户没有交易或收益为零。</p>}
      <p className="s-demo-description">费用差额 = 成交报告手续费 + 流水中的有符号手续费。两者用于核对，不重复扣费。流水净额包含转入转出等账户活动，不等于策略利润。</p>
      {!!r.unqueried_income_symbols.length && <p role="status">资金流水还涉及未查询成交历史的合约：{r.unqueried_income_symbols.join('、')}。费用比较范围尚未齐全。</p>}
      {!!r.income_breakdown.length && <div className="s-table-wrap s-demo-table" role="region" aria-label="实际资金流水归因表" tabIndex={0}><table className="s-table"><thead><tr><th>资产</th><th>流水类型</th><th>窗口净额</th></tr></thead><tbody>
        {r.income_breakdown.map(b => <tr key={`${b.asset}:${b.type}`}><td>{b.asset}</td><td>{flowTypes[b.type] ?? b.type}</td><td>{b.amount}</td></tr>)}
      </tbody></table></div>}
      <details className="s-demo-identities"><summary>查看 {r.terms.length} 个合约的规则与账户费率</summary>
        <div className="s-table-wrap s-demo-table" role="region" aria-label="实际场所规则表" tabIndex={0}><table className="s-table"><thead><tr><th>合约</th><th>交易过滤器</th><th>保证金档位</th><th>Maker 费率</th><th>Taker 费率</th></tr></thead><tbody>
          {r.terms.map(t => <tr key={t.symbol}><td>{t.symbol}</td><td>{t.exchange_rules ? '已采集' : '缺失'}</td><td>{t.margin_brackets ? '已采集' : '缺失'}</td><td>{t.commission?.makerCommissionRate ?? '未知'}</td><td>{t.commission?.takerCommissionRate ?? '未知'}</td></tr>)}
        </tbody></table></div>
      </details>
      {!!r.gaps.length && <div className="s-demo-notice"><strong>采集缺口 · {r.gaps.length}</strong><ul>{r.gaps.map((g,i) => <li key={i}>{g.symbol || '全账户'} · {g.kind} · {g.reason === 'PAGE_LIMIT_REACHED' ? '返回数量达到上限，历史可能截断' : g.reason}</li>)}</ul></div>}
    </>}
  </Panel>;
}
