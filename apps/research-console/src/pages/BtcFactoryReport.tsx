import { useState } from 'react';
import type { BtcFactoryReport as Report, BtcForwardReport } from '@/lib/types/research';
import { Panel } from '@/ui/primitives';
import { AreaChart } from '@/ui/charts';

const number = (value: number | undefined | null) => value == null ? '—' : value.toFixed(2);
const date = (value: number) => new Date(value).toISOString().slice(0, 10);

export function BtcFactoryResults({ report }: { report: Report }) {
  const [slip, setSlip] = useState(5);
  const [stop, setStop] = useState(true);
  const [selected, setSelected] = useState('FACTORY');
  const rows = report.cases.filter(row => row.slip_bps === slip && row.stop === stop);
  const current = rows.find(row => row.policy === selected);
  const frozen = rows.find(row => row.policy === 'FROZEN_PRICE')?.metrics?.net_pnl;
  return <>
    <Panel eyebrow="FACTORY REPLAY" title="完整工厂与固定基线">
      <p className="mb-4 text-xs text-muted">每月只用已成熟预测选择模型；八个政策分别连续记账。手续费单边 {report.fee_bps}bp，预测门槛 {number(report.threshold_bps)}bp。48 情景全部保留，不按结果筛选显示。</p>
      <div className="mb-4 flex flex-wrap gap-5 text-xs">
        <label>单边滑点 <select className="ml-2 rounded border border-line bg-inset p-2" value={slip} onChange={event => setSlip(Number(event.target.value))}>
          {[2, 5, 10].map(value => <option key={value} value={value}>{value} bp</option>)}
        </select></label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={stop} onChange={event => setStop(event.target.checked)} />累计亏损 150 USDC 停机</label>
      </div>
      <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left text-xs">
        <thead><tr><th>政策</th><th>状态</th><th>净收益 USDC</th><th>相对冻结基线</th><th>手续费</th><th>资金费</th><th>最大回撤</th><th>成交数</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.trial_id} className="border-t border-line">
          <td className="py-3"><button className="text-accent underline" onClick={() => setSelected(row.policy)}>{row.policy}</button></td>
          <td>{row.status === 'PASSED' ? '计算完成' : '计算失败'}</td>
          <td>{number(row.metrics?.net_pnl)}</td><td>{row.metrics && frozen != null ? number(row.metrics.net_pnl - frozen) : '—'}</td>
          <td>{row.metrics ? number(Number(row.metrics.fees)) : '—'}</td><td>{row.metrics ? number(Number(row.metrics.funding)) : '—'}</td>
          <td>{row.metrics ? `${number(row.metrics.max_drawdown * 100)}%` : '—'}</td><td>{row.metrics?.fills ?? '—'}</td>
        </tr>)}</tbody>
      </table></div>
      <p className="mt-3 text-xs text-faint">FROZEN_PRICE 冻结首期参数；PRICE 每月重训；FACTORY 每月选模型或现金。资金费为账本有符号现金流。历史已查看，结果仅供技术研究。</p>
    </Panel>
    <Panel eyebrow="POLICY DETAIL" title={`${selected} · ${slip}bp · ${stop ? '带停机' : '不带停机'}`}>
      {current?.metrics ? <>
        <p className="mb-3 text-xs text-muted">2025：{number(current.metrics.annual_pnl['2025'])} USDC · 2026：{number(current.metrics.annual_pnl['2026'])} USDC · 对账残差 {current.metrics.reconciliation_residual}</p>
        <p className="mb-2 text-xs text-muted">净权益（USDC）</p>
        <AreaChart data={(current.equity ?? []).map(row => Number(row.equity))} yFormat={value => value.toFixed(0)} />
        {!!current.equity?.length && <p className="text-xs text-faint">{date(current.equity[0].time)} — {date(current.equity[current.equity.length - 1].time)}；横轴为每日观察序号</p>}
        <div className="mt-4 flex flex-wrap gap-3 text-xs">{Object.entries(current.metrics.order_reasons).map(([reason, count]) => <span key={reason}>{reason}: {count}</span>)}</div>
        <p className="mt-2 text-xs">停机时间：{current.metrics.halted_at == null ? '未触发' : new Date(current.metrics.halted_at).toISOString()}</p>
      </> : <p role="status" className="text-xs text-warn">{current?.error ?? '此情景尚无完整结果'}</p>}
    </Panel>
    <Panel eyebrow="MONTHLY SELECTION" title="当时为何选择这个模型">
      <div className="max-h-96 overflow-auto"><table className="w-full text-left text-xs">
        <thead><tr><th>决策日 UTC</th><th>选择</th><th>已成熟样本</th><th>最后标签可用</th><th>候选 MSE</th></tr></thead>
        <tbody>{report.selection_updates.map(row => <tr key={row.decision_ms} className="border-t border-line">
          <td className="py-3">{date(row.decision_ms)}</td><td>{row.selected}</td><td>{row.n}</td>
          <td>{row.latest_label_ms == null ? '—' : new Date(row.latest_label_ms).toISOString()}</td>
          <td>{Object.entries(row.mse).map(([model, value]) => <div key={model}>{model}: {value.toExponential(3)}</div>)}零预测: {row.zero_mse?.toExponential(3) ?? '—'}</td>
        </tr>)}</tbody>
      </table></div>
    </Panel>
  </>;
}

export function BtcForwardResults({ report }: { report: BtcForwardReport }) {
  return <Panel eyebrow="FORWARD OBSERVATION" title="BTC 模型的真实时间观察">
    <p className="mb-4 text-xs text-muted">{report.reason}</p>
    <p className="mb-3 text-xs">状态 {report.status} · 截至 {new Date(report.observed_at_ms).toISOString()}</p>
    <div className="overflow-x-auto"><table className="min-w-[38rem] w-full text-left text-xs">
      <thead><tr><th>模型</th><th>冻结日期</th><th>已记录预测</th><th>待成熟</th><th>已评价</th><th>RMSE bp</th></tr></thead>
      <tbody>{report.models.map(row => <tr key={row.candidate_id} className="border-t border-line">
        <td className="py-3">{row.model}</td><td>{date(row.frozen_at_ms)}</td><td>{row.decisions}</td>
        <td>{row.pending}</td><td>{row.completed}</td><td>{number(row.rmse_bps)}</td>
      </tr>)}</tbody>
    </table></div>
    {!report.models.length && <p className="mt-3 text-xs">尚未冻结模型，没有前向样本。</p>}
    {report.online_factory && <section className="mt-6 border-t border-line pt-4" aria-label="在线月度工厂">
      <h3 className="text-sm font-semibold">在线月度工厂</h3>
      <p className="mt-2 text-xs">状态 {report.online_factory.status} · 已成熟训练样本 <strong>{report.online_factory.training_available} / {report.online_factory.training_required}</strong> · 工厂观察 {report.online_factory.factory_decisions} 次</p>
      <p className="mt-2 text-xs text-muted">每月首个实际决策窗口冻结版本；训练样本不足时选择现金，下一月重新评估。选择器另需至少60个在线四模型共同成熟预测，最多使用最近90个；不会用训练误差代替。</p>
      <div className="mt-3 overflow-x-auto"><table className="min-w-[38rem] w-full text-left text-xs">
        <thead><tr><th>月份 / 版本</th><th>状态</th><th>训练样本</th><th>月度选择</th><th>选择样本</th><th>最后标签接收</th></tr></thead>
        <tbody>{report.online_factory.versions.map(version => <tr key={version.version_hash} className="border-t border-line">
          <td className="py-3"><span>{version.month}</span><code className="block text-faint" title={version.version_hash}>{version.version_hash.slice(0, 12)}</code></td>
          <td>{version.status}</td><td>{version.training_n}</td><td>{version.selection.selected}</td>
          <td>{version.selection.n}</td><td>{version.selection.latest_label_ms === null ? '—' : date(version.selection.latest_label_ms)}</td>
        </tr>)}</tbody>
      </table></div>
      {report.online_factory.versions.filter(version => version.models.length > 0).map(version => <details className="mt-3 text-xs" key={version.version_hash}>
        <summary className="cursor-pointer">{version.month} 模型评价与选择误差</summary>
        <div className="overflow-x-auto"><table className="mt-2 min-w-[38rem] w-full text-left">
          <thead><tr><th>模型</th><th>预测</th><th>待成熟</th><th>已评价</th><th>RMSE bp</th><th>选择 MSE</th></tr></thead>
          <tbody>{version.models.map(model => <tr key={model.candidate_id} className="border-t border-line">
            <td className="py-2">{model.model}</td><td>{model.decisions}</td><td>{model.pending}</td><td>{model.completed}</td>
            <td>{number(model.rmse_bps)}</td><td>{version.selection.mse[model.model]?.toExponential(4) ?? '—'}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="mt-2">零预测 MSE {version.selection.zero_mse?.toExponential(4) ?? '—'}</p>
      </details>)}
      {!report.online_factory.versions.length && <p className="mt-3 text-xs">已启用，等待下一个决策窗口创建月份版本。</p>}
      {report.online_factory.latest_decision && <p className="mt-3 text-xs">
        最近观察 {date(report.online_factory.latest_decision.decision_time_ms)} · {report.online_factory.latest_decision.selected} ·
        目标权重 {number(report.online_factory.latest_decision.target_weight * 100)}% ·
        预测 bp {number(report.online_factory.latest_decision.prediction === null ? null : report.online_factory.latest_decision.prediction * 10000)}
      </p>}
      <p className="mt-3 text-xs text-faint">上述目标仅记录工厂选择，未形成成交或账户收益。最初冻结的四模型继续作为独立对照，旧在线版本的待成熟标签也继续保留。</p>
    </section>}
    <p className="mt-3 text-xs text-faint">只评价预测误差，不模拟成交或计算账户收益。错过每日 UTC 00:00–00:05 窗口不会补写预测；后续需再次运行观察任务追加成熟标签。</p>
  </Panel>;
}
