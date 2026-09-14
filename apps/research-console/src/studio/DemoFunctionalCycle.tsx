import type {DemoFunctionalSnapshot,DemoFunctionalPhase} from '@/lib/types/demo-functional-status';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';
import type {DemoAccountPosition} from './demo-account-position';

const titles:Record<DemoFunctionalPhase,string>={
  OPENING_CASH_CAPTURE:'正在留存交易前余额',OPEN_IOC:'正在执行有限开仓',RECEIPT_RECOVERY:'正在核对成交回执',
  REDUCE_ONLY_IOC:'正在减少已确认持仓',CASH_RECONCILIATION:'正在核对费用与现金',
  DEMO_CYCLE_COMPLETED:'成交、持仓与现金核对完成',RECOVERY_REQUIRED:'订单结果需要恢复核对',
  NO_TRADING_EVIDENCE:'本轮未形成完整往返成交',CASH_RECONCILIATION_REQUIRED:'持仓已结束，现金仍待核对',
  REDUCTION_INCOMPLETE:'仍有未完成减仓',CYCLE_NOT_COMPLETED:'本轮测试未完成',
  REDUCTION_AUTHORIZATION_REQUIRED:'已核对持仓，后续减仓需要有效测试授权',
  EVIDENCE_REPAIR_REQUIRED:'原始回执有缺口，本轮推进已停止',
  MANUAL_ACCOUNT_RECONCILED:'人工平仓账务已核对，自动往返仍待测试',
};
export function DemoFunctionalCycle({snapshot,refreshFailed=false,position}:{snapshot?:DemoFunctionalSnapshot;
  refreshFailed?:boolean;position?:DemoAccountPosition|null}) {
  const r=snapshot?.report;
  return <section aria-label="Demo 有限交易周期"><Panel title="Demo 有限交易周期" note="BTCUSDC · 交易前余额 → 有限开仓 → 回执 → 减仓 → 现金核对">
    {!r ? <Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'测试记录无法读取':snapshot?.status==='DATA_BLOCKED'?'测试记录时间异常':'尚无实际交易周期'}>
      首次测试配置登记后展示逐步结果。已有账户连接和历史成交不会计为本轮完成。
    </Empty> : <div className="s-demo-replay">
      {(refreshFailed || snapshot?.status==='STALE') && <p className="s-demo-notice" role="status">{refreshFailed?'刷新失败，保留上次测试记录。':'测试进度已过期，当前结果需要重新核查。'}</p>}
      <p className="s-demo-description" role="status"><strong>{titles[r.phase]}</strong> · {stamp(r.observed_at_ms,true)}</p>
      {r.phase==='MANUAL_ACCOUNT_RECONCILED' && <p className="s-demo-notice">系统开仓与人工平仓已分别入账，费用和钱包匹配。原始私有流缺口仍保留；这次人工恢复不计为自动闭环。</p>}
      {position && !refreshFailed && <div className="s-demo-notice" role="status">
        <strong>{position.flat?'账户观察：BTCUSDC 当前无持仓':`账户观察：BTCUSDC ${position.quantityLabel}`}</strong>
        <p>{stamp(position.observedAt,true)} · 场所账户读数与下方历史账本分别展示。{position.flat && !r.cash_reconciled?
          '当前空仓不代表历史成交、费用与现金已经核对完成。':'最新持仓不改写本轮历史结果。'}</p>
      </div>}
      <div className="s-metrics s-demo-metrics">
        <Metric label="订单尝试" value={r.orders_attempted==null?'—':`${r.orders_attempted} / 3`} note="包含开仓与减仓；结果不明时先恢复"/>
        <Metric label="已核对成交记录" value={r.fill_count??'—'} note="未成交或仅收到接单响应不算完成"/>
        <Metric label="账本持仓数量" value={r.final_quantity==null?'—':`${r.final_quantity} BTC`} note="本轮已核对成交的计算结果"/>
        <Metric label="现金核对" value={r.cash_reconciled?'已匹配':'待完成'} note="交易费用与资金流水各记一次"/>
      </div>
      <p className="s-demo-description">本轮只验证 Demo 执行与账务功能。策略盈利和长期稳定性仍由前向研究分别验证。</p>
      <details className="s-demo-identities"><summary>查看本轮来源</summary><p>周期：<code>{r.cycle_sha256}</code></p><p>运行版本：<code>{r.source_commit}</code></p></details>
    </div>}
  </Panel></section>;
}
