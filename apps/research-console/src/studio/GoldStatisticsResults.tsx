import type {GoldNativeStatistics} from '@/lib/types/research';
import {Chart, type ChartRow} from './Charts';

const names = {channel:'通道突破', trend:'均线交叉', reversion:'均值回归', net_portfolio:'共享资金组合'};
const decisions = {PASS:'通过',FAIL:'未通过',INSUFFICIENT:'证据不足'};
const checks = {dsr:'多次尝试后的显著性',cpcv_positive:'交叉验证正收益比例',cpcv_tail:'交叉验证尾部表现',
  pbo:'过拟合概率',net_return:'样本净收益',benchmark_excess:'超过现金基准',drawdown:'样本回撤'};
const reasons: Record<string,string> = {SAMPLE_TOO_SHORT:'样本未达到预先固定的30天要求',
  FULL_RUN_TERMINAL_NOT_COVERED:'样本未覆盖完整账户终态',NO_PREDECLARED_SELECTION:'观察前未选择待评审候选',
  CPCV_INCOMPLETE_OR_NONFINITE:'信息隔离后交叉验证样本不足',ZERO_VARIANCE_CANDIDATE:'部分候选收益没有足够变化',
  NATIVE_ATTEMPT_NOT_COMPLETE:'存在失败或尚未完成的原试验',
  GLOBAL_DSR_VARIANCE_NOT_COMPARABLE:'跨窗口搜索尚缺可比较的全局显著性证据',
  DECLARATION_ATTEMPT_BUDGET_MISMATCH:'原试验数量或所属窗口与事前声明不一致',
  STATISTICAL_POLICY_NOT_PREREGISTERED_FOR_THIS_STUDY:'部分尝试没有本研究的窗口前声明',
  COMPLETE_COMPARABLE_CANDIDATE_MATRIX_REQUIRED:'完整尝试集尚不能形成同口径比较'};

export function GoldStatisticsResults({value}:{value:GoldNativeStatistics}) {
  const review = value.statistical_review === 'COMPLETED' ? value.review : null;
  const curves = value.attempts.filter(a=>a.grid !== null);
  const points = curves[0]?.grid?.points ?? value.benchmark?.grid?.points ?? [];
  const rows: ChartRow[] = points.map((point,index)=>({label:new Date(point.timestamp).toISOString(),time:point.timestamp,
    ...Object.fromEntries(curves.map(a=>[a.candidate_id,Number(a.grid!.points[index].equity)])),
    ...(value.benchmark?.grid ? {baseline:Number(value.benchmark.grid.points[index].equity)} : {})}));
  return <section aria-label="原账本统计样本与基准">
    <h3>原账本统计样本与基准</h3>
    <p>统计点按原收据可知时间取样，资金费尚未收到时不提前显示现金变化。曲线保留原账户权益，包含浮动损益。</p>
    <p>{review ? `统计检验已执行，结论：${decisions[review.statistical_decision]}。` : '样本不足，统计评审尚未完成；'}独立风险、成本校准和锁定 OOS 仍待核验。</p>
    {review && <section aria-label="黄金统计检验结果">
      <h4>窗口前规则下的统计检验</h4>
      <p>检验保留全部 {review.metrics.n_trials} 次尝试；无订单现金账户不计为额外试验。统计结果不授予候选或交易权限。</p>
      {review.schema === 'QUANTOS_GOLD_PROJECT_STATISTICAL_REPORT_V2' && <p>
        本次观察前固定的比较集含 {review.comparison.included_attempts.length} 次原尝试；
        其余 {review.comparison.excluded_attempts.length} 次保留于完整历史，并计入全局搜索次数。
      </p>}
      <dl><dt>DSR</dt><dd>{review.metrics.dsr ?? '未知'}</dd><dt>过拟合概率</dt><dd>{review.metrics.pbo ?? '未知'}</dd>
        <dt>样本净收益率</dt><dd>{review.metrics.sample_net_return ?? '未知'}</dd>
        <dt>超过现金基准的收益率</dt><dd>{review.metrics.benchmark_excess ?? '未知'}</dd></dl>
      <table className="s-table"><thead><tr><th>检查</th><th>结论</th></tr></thead><tbody>
        {Object.entries(checks).map(([key,label])=><tr key={key}><td>{label}</td>
          <td>{decisions[review.checks[key as keyof typeof checks]]}</td></tr>)}
      </tbody></table>
      {review.reasons.length > 0 && <ul>{review.reasons.map(reason=><li key={reason}>{reasons[reason] ?? reason}</li>)}</ul>}
    </section>}
    <p>本报告绑定完整试验快照中的 {value.population.attempt_count} 次原尝试，含失败和未完成记录。新准入必须重新核对最新完整历史。</p>
    <p>固定频率：{value.sample.frequency_ms/1000} 秒；样本区间：
      {new Date(value.sample.start_ms).toLocaleString('zh-CN')} 至 {new Date(value.sample.end_ms).toLocaleString('zh-CN')}。</p>
    <Chart title="黄金定频权益与现金基准" rows={rows} kind="step" xAxis="time" unit="USDT"
      series={[...curves.map(a=>({key:a.candidate_id,label:names[a.candidate_id]})),
        ...(value.benchmark?.grid ? [{key:'baseline',label:'无订单现金基准'}] : [])]}
      note="UTC 时间；短窗口原账本诊断，曲线不代表独立 OOS 或统计准入"/>
    <div className="s-table-wrap" tabIndex={0} role="region" aria-label="定频样本与完整现金结果">
      <table className="s-table"><thead><tr><th>账户</th><th>定频收益点数</th><th>样本期末权益（USDT）</th>
        <th>完整运行期末权益（USDT）</th><th>终态覆盖</th></tr></thead><tbody>
        {value.attempts.map(a=><tr key={a.native_trial_id}><td>{names[a.candidate_id]}</td>
          <td>{a.metrics?.sample_count ?? '未知'}</td><td>{a.grid?.points.slice(-1)[0]?.equity ?? '未知'}</td>
          <td>{a.metrics?.ending_equity ?? '未知'}</td><td>{a.full_run_covered ? '已覆盖' : '未覆盖'}</td></tr>)}
        {value.benchmark && <tr><td>无订单现金基准</td><td>{value.benchmark.metrics?.sample_count ?? '未知'}</td>
          <td>{value.benchmark.grid?.points.slice(-1)[0]?.equity ?? '未知'}</td>
          <td>{value.benchmark.metrics?.ending_equity ?? '未知'}</td><td>{value.benchmark.full_run_covered ? '已覆盖' : '未覆盖'}</td></tr>}
      </tbody></table>
    </div>
    {value.attempts.some(a=>!a.full_run_covered) && <p>本次网格未覆盖完整运行终态，样本期末权益不能替代完整运行现金结果。</p>}
    {!value.benchmark && <p>原现金基准尚不可用，不能用零值替代。</p>}
    <details><summary>查看绑定的完整试验历史</summary>
      <table className="s-table"><thead><tr><th>原试验编号</th><th>原运行编号</th><th>状态</th></tr></thead><tbody>
        {value.population.all_attempts.map(a=><tr key={a.native_trial_id}><td>{a.native_trial_id}</td>
          <td>{a.invocation_id}</td><td>{a.status}</td></tr>)}
      </tbody></table>
    </details>
  </section>;
}
