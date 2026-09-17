import type {DemoPortfolioSnapshot,PortfolioStatus} from '@/lib/types/demo-portfolio-status';
import type {DemoCashChainSnapshot} from '@/lib/types/demo-cash-chain';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';
import {DemoCashChain} from './DemoCashChain';

const titles:Record<PortfolioStatus,string>={
  NO_CHANGE:'本次决策无需调仓',QUERY_ONLY:'订单已领取，等待原编号恢复',TASK_READY:'调仓任务已准备，尚未证明成交',
  EXECUTION_ATTEMPT_RECORDED:'执行尝试已留存，等待成交与现金核对',EXECUTION_NOT_COMPLETED:'执行未完成',
  CASH_RECONCILIATION_REQUIRED:'现金仍待核对',CASH_RECONCILED:'组合往返与现金核对完成',
  INPUTS_UNRESOLVED:'运行输入不齐，本轮未完成',CONTROLLER_RESULT_NOT_RETAINED:'运行结果未完整保存',
  ALREADY_FLAT:'已确认空仓，现金核对尚未完成',CLOSE_RECOVERY_REQUIRED:'减仓结果需要恢复核对',
  CLOSING_REQUIRED:'已进入减仓阶段',DECISION_BUDGET_RESERVED:'本轮调仓预算已使用，余量留给退出',
  EXPIRED_QUERY_ONLY:'任务已过期，只允许查询恢复',CYCLE_DEADLINE_REACHED:'本轮等待期限已到，仍需核查',
};
export function DemoPortfolioCycle({snapshot,cash,refreshFailed=false}:{snapshot?:DemoPortfolioSnapshot;
  cash?:DemoCashChainSnapshot;refreshFailed?:boolean}) {
  const r=snapshot?.report;
  const linked=!!r?.cash_report_sha256 && cash?.report?.report_sha256===r.cash_report_sha256;
  return <section aria-label="组合执行闭环"><Panel title="组合执行闭环" note="组合目标 → 调仓 → 有限减仓 → 原账户现金核对">
    {!r ? <Empty title={snapshot?.status==='DATA_BLOCKED'?'组合记录时间异常':
      snapshot?.status==='CORRUPT_OR_UNREADABLE'?'组合记录无法读取':'尚无组合运行记录'}>
      组合配置登记并运行后展示结果。已有固定买单测试不计为组合闭环。
    </Empty> : <div className="s-demo-replay">
      {(refreshFailed || snapshot?.status==='STALE') && <p className="s-demo-notice" role="status">记录待刷新：下方是历史结果，不能代表当前账户状态。</p>}
      <p className="s-demo-description" role="status"><strong>{titles[r.status]}</strong> · {stamp(r.observed_at_ms,true)}</p>
      <div className="s-metrics s-demo-metrics">
        <Metric label="本轮配置资金" value={r.capital===null?'—':`${r.capital} USDC`} note="组合配置额度，不代表整个账户余额"/>
        <Metric label="减仓序号" value={r.close_index===null?'—':`${r.close_index} / 2`} note="前一单终态确认后才可处理余仓"/>
        <Metric label="已核对成交" value={r.fill_count??'—'} note="准备任务或当前空仓不算完整往返"/>
        <Metric label="现金核对" value={r.cash_reconciled?'本轮已匹配':'待完成'} note={r.wallet_residual===null?'费用与资金流水待核对':`钱包差额 ${r.wallet_residual} USDC`}/>
      </div>
      <p className="s-demo-description">授权截止：{r.policy_valid_until_ms===null?'未知':stamp(r.policy_valid_until_ms,true)}。到期或 UTC 换日前提前减仓，不自动续期。功能核对不证明策略盈利。</p>
      {r.cash_reconciled && !linked && <p className="s-demo-notice">本轮现金明细尚未同步，暂不展示其他周期的现金报告。</p>}
      {linked && <DemoCashChain snapshot={cash} refreshFailed={refreshFailed || snapshot?.status==='STALE'}/>}
      <details className="s-demo-identities"><summary>查看组合与运行来源</summary>
        <dl><div><dt>模型</dt><dd><code>{r.model_hash??'未知'}</code></dd></div>
          <div><dt>配置</dt><dd><code>{r.allocation_sha256??'未知'}</code></dd></div>
          <div><dt>任务</dt><dd><code>{r.task_sha256??'本次没有任务记录'}</code></dd></div>
          <div><dt>版本</dt><dd><code>{r.source_commit}</code></dd></div></dl>
      </details>
    </div>}
  </Panel></section>;
}
