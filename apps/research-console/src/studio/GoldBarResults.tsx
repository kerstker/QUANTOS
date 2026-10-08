import type { GoldBarReport } from '@/lib/types/research';
import { Panel } from './components';
import {GoldStatisticsResults} from './GoldStatisticsResults';

const names = {channel: '通道突破', trend: '均线交叉', reversion: '均值回归'};
const outcomes: Record<string, string> = {
  NO_SIGNAL: '没有入场信号', NO_FILL: '有交易意图，未成交',
  OPEN_AT_END: '仍有仓位或挂单', FLAT_TERMINAL: '已成交，期末空仓',
  INPUT_UNAVAILABLE: '输入尚不可用',
};
const percentage = (value: string | undefined) => value !== undefined && Number.isFinite(Number(value))
  ? `${(Number(value) * 100).toLocaleString('zh-CN', {maximumSignificantDigits: 5})}%` : '未知';
const lossStates = {ACTIVE: '未触发5%停机', STOPPED_FLAT: '已触发5%停机，期末空仓',
  STOPPING_UNRESOLVED: '已触发5%停机，退出未完成'};
export function GoldBarResults({report}: {report: GoldBarReport}) {
  return <Panel title="黄金三策略比较">
    {report.native_registration && <section aria-label="原试验登记与评估状态">
      <h3>原试验登记与评估状态</h3>
      <p>计算前登记了 {report.native_registration.attempt_count} 次原试验；登记保留尝试历史。</p>
      <dl>
        <dt>原运行编号</dt><dd>{report.native_registration.native_invocation_id}</dd>
        <dt>原计划摘要</dt><dd>{report.native_registration.plan_sha256}</dd>
        <dt>原输入摘要</dt><dd>{report.native_registration.input_sha256}</dd>
        <dt>评估计划</dt><dd>{report.native_registration.schema !== 'QUANTOS_REGISTERED_GOLD_COLLECTION_V1'
          ? '观察窗口前已固定评估声明' : '评估计划未预登记'}</dd>
        {report.native_registration.schema !== 'QUANTOS_REGISTERED_GOLD_COLLECTION_V1' && <>
          <dt>声明摘要</dt><dd>{report.native_registration.forward_declaration.design_sha256}</dd>
          <dt>声明时间资格</dt><dd>声明时间尚未独立核验</dd>
        </>}
        <dt>统计评审</dt><dd>{report.native_statistics?.statistical_review === 'COMPLETED'
          ? '统计检验已执行，资格仍待独立核验' : report.native_statistics ? '统计评审尚未完成' : '前向统计适配尚未接入'}</dd>
        <dt>风险评审</dt><dd>风险尚未独立复算</dd>
        <dt>样本资格</dt><dd>锁定 OOS 未知</dd>
        <dt>候选资格</dt><dd>候选未准入</dd>
      </dl>
    </section>}
    {report.native_statistics && <GoldStatisticsResults value={report.native_statistics}/>}
    <p>固定候选的短窗口技术实验，尚未选出可交易策略。模拟订单不代表交易所成交。</p>
    <div className="s-table-wrap" tabIndex={0} role="region" aria-label="黄金候选实验结果">
      <table className="s-table">
        <thead><tr><th>候选策略</th><th>实验结果</th><th>输入</th><th>模拟订单</th><th>含成交的订单</th><th>期末持仓数</th><th>净权益变动（USDT，含浮动）</th><th>模拟手续费（USDT）</th><th>观测最大回撤</th></tr></thead>
        <tbody>{report.candidates.map(row => <tr key={row.candidate}>
          <td>{names[row.candidate]}</td><td>{outcomes[row.outcome] ?? row.outcome}<br/>
            <small>{row.loss_control ? lossStates[row.loss_control.status] : '旧记录：停机状态未知'}</small></td>
          <td>{row.input_ready ? '曾满足计算条件' : '未满足计算条件'}</td>
          <td>{row.order_count}</td><td>{row.filled_order_count}</td><td>{row.open_position_count}</td>
          <td>{row.account?.net_equity_change ?? '未知'}</td><td>{row.account?.fees ?? '未知'}</td>
          <td>{percentage(row.account?.max_observed_drawdown)}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {report.portfolio && <section aria-label="共享资金组合实验">
      <h3>共享资金组合实验</h3>
      <p>固定目标先抵消，再由一个账户执行。未计算分策略利润；本结果不是三个独立账户收益之和。</p>
      <dl>
        <dt>净权益变动（USDT，含浮动）</dt><dd>{report.portfolio.account.net_equity_change}</dd>
        <dt>手续费 / 资金费（USDT）</dt><dd>{report.portfolio.account.fees} / {report.portfolio.account.funding}</dd>
        <dt>观测最大回撤</dt><dd>{percentage(report.portfolio.account.max_observed_drawdown)}</dd>
        <dt>模拟订单 / 含成交订单 / 期末持仓数</dt><dd>{report.portfolio.order_count} / {report.portfolio.filled_order_count} / {report.portfolio.open_position_count}</dd>
        <dt>对账残差（USDT）</dt><dd>{report.portfolio.account.reconciliation_residual}</dd>
        <dt>共享账户停止状态</dt><dd>{lossStates[report.portfolio.loss_control.status]}</dd>
      </dl>
      {report.portfolio.target_diagnostics ? <div aria-label="组合目标诊断">
        <p>目标观测：{report.portfolio.target_diagnostics.observation_count} 次；以下计数不是交易次数或时间占比。</p>
        <dl>
          <dt>全部目标为零</dt><dd>{report.portfolio.target_diagnostics.all_zero_count}</dd>
          <dt>相反目标完全抵消</dt><dd>{report.portfolio.target_diagnostics.opposed_zero_count}</dd>
          <dt>相反目标部分抵消</dt><dd>{report.portfolio.target_diagnostics.opposed_nonzero_count}</dd>
          <dt>同向非零净目标</dt><dd>{report.portfolio.target_diagnostics.unopposed_nonzero_count}</dd>
        </dl>
        <p>净目标为零可能来自策略相互抵消；有目标也不代表已成交。</p>
      </div> : <p>旧记录：组合目标诊断未知</p>}
      <p>独立组合试验编号：{report.portfolio.trial_id}</p>
    </section>}
    <p>假设手续费率：{report.fee_rate}（小数比例，尚未按账户校准）。没有信号或成交，不等于策略盈利。</p>
    <p>净权益变动包含未平仓浮动损益；各候选使用独立模拟账户，不能相加作为组合收益。旧报告缺少的成本数据标为未知。</p>
    <p>5%停机以单个模拟窗口的期初权益为基准，触发后不会在本研究计划中自动开始下一窗口；退出费用和行情跳空仍可能扩大亏损。</p>
    <p>记录已进行同一实现的确定性复算，仍需独立验证和足够样本；未启用自动交易。</p>
  </Panel>;
}
