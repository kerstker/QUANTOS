import { useState } from 'react';
import { Link } from '@/lib/router';
import { fetchTechnicalReplay } from '@/lib/api/research-console-api';
import type { TechnicalReplayPayload } from '@/lib/types/research';
import type { RunsState } from './Research';
import { Empty, ErrorNote, Header, Loading, Panel } from './components';
import { fmt, KIND_LABELS, stamp, useResource } from './data';
type Result = NonNullable<TechnicalReplayPayload['technical_replay']>;
function values(data: Result, policy: string, slip: number, stop: boolean): Record<string, number> {
  const metrics = data.factory_report?.cases.find(c => c.policy === policy && c.slip_bps === slip && c.stop === stop)?.metrics;
  if (data.factory_report)
    return metrics ? { '净收益 USDC': metrics.net_pnl, '手续费 USDC': Number(metrics.fees), '资金费 USDC': Number(metrics.funding), '最大回撤 %': metrics.max_drawdown * 100, '成交数': metrics.fills } : {};
  if (data.forward_report)
    return { '已记录模型预测': data.forward_report.models.reduce((n, m) => n + m.decisions, 0), '已评价模型预测': data.forward_report.models.reduce((n, m) => n + m.completed, 0), '待成熟模型预测': data.forward_report.models.reduce((n, m) => n + m.pending, 0) };
  return Object.fromEntries(Object.entries(data.metrics).filter(([key, value]) => !key.includes('hash') && (typeof value === 'number' || (typeof value === 'string' && /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value)))).map(([key, value]) => [key, Number(value)]));
}
export function Compare({ runs }: {
  runs: RunsState;
}) {
  const [kind, setKind] = useState('btc_factory');
  const [first, setFirst] = useState('');
  const [second, setSecond] = useState('');
  const [policy, setPolicy] = useState('FACTORY');
  const [slip, setSlip] = useState(5);
  const [stop, setStop] = useState(true);
  const choices = runs.rows.filter(r => r.kind === kind && r.status === 'done');
  const a = choices.find(r => r.run_id === first) ?? choices[0];
  const b = choices.find(r => r.run_id === second) ?? choices.find(r => r.run_id !== a?.run_id);
  const result = useResource(`compare:${a?.run_id}:${b?.run_id}`, signal => a && b && a.run_id !== b.run_id ? Promise.all([fetchTechnicalReplay(a.run_id, signal), fetchTechnicalReplay(b.run_id, signal)]) : Promise.resolve(null));
  const left = result.data?.[0].technical_replay, right = result.data?.[1].technical_replay;
  const policies = [...new Set([...(left?.factory_report?.cases.map(c => c.policy) ?? []), ...(right?.factory_report?.cases.map(c => c.policy) ?? [])])];
  const activePolicy = policies.includes(policy) ? policy : (policies[0] ?? policy);
  const va = left ? values(left, activePolicy, slip, stop) : {}, vb = right ? values(right, activePolicy, slip, stop) : {};
  const keys = [...new Set([...Object.keys(va), ...Object.keys(vb)])];
  return <>
    <Header eyebrow="RESEARCH / COMPARE" title="运行对比" action={<Link className="s-button" to="/research/runs">返回实验运行</Link>}>并列查看同类研究，检查差异和输入来源。</Header>
    <ErrorNote error={runs.error ?? result.error} retry={() => { runs.refresh(); result.refresh(); }} />
    <Panel title="选择两次运行">
      <div className="s-compare-controls">
        <label>研究类型<select aria-label="对比研究类型" value={kind} onChange={e => { setKind(e.target.value); setFirst(''); setSecond(''); }}>{['btc_factory', 'btc_forward', 'btc_mechanism', 'perpetual_alpha', 'perpetual_replay', 'orderblock_research'].map(k => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}</select>
        </label>
        <label>运行 A<select aria-label="运行 A" value={a?.run_id ?? ''} onChange={e => setFirst(e.target.value)}>
          <option value="" disabled>选择运行</option>{choices.map(r => <option key={r.run_id} value={r.run_id} disabled={r.run_id === b?.run_id}>实验 {String(r.params.attempt ?? '—')} · {stamp(r.created_ts)} · {r.run_id.slice(-8)}</option>)}</select>
        </label>
        <label>运行 B<select aria-label="运行 B" value={b?.run_id ?? ''} onChange={e => setSecond(e.target.value)}>
          <option value="" disabled>选择运行</option>{choices.map(r => <option key={r.run_id} value={r.run_id} disabled={r.run_id === a?.run_id}>实验 {String(r.params.attempt ?? '—')} · {stamp(r.created_ts)} · {r.run_id.slice(-8)}</option>)}</select>
        </label>
      </div>
    </Panel>
    {result.loading ? <Loading /> : !a || !b ? <Empty title="至少需要两次同类的完成运行">失败记录保留在运行列表中，不填充为成功结果。</Empty> : !left || !right ? <Empty title="缺少可比较的技术结果">不会复用已失效的历史指标。</Empty> : <Panel title="指标差异" note="差值为 B − A；数值变化不代表研究质量提升。" action={kind === 'btc_factory' ? <div className="s-controls">
      <select aria-label="对比政策" value={activePolicy} onChange={e => setPolicy(e.target.value)}>{policies.map(p => <option key={p}>{p}</option>)}</select>
      <select aria-label="对比滑点" value={slip} onChange={e => setSlip(Number(e.target.value))}>{[2, 5, 10].map(n => <option key={n} value={n}>{n} bp</option>)}</select>
      <label className="s-checkbox">
        <input type="checkbox" checked={stop} onChange={e => setStop(e.target.checked)} />启用亏损停机</label>
    </div> : undefined}>
      <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
        <table className="s-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>
                <Link to={`/research/runs/${encodeURIComponent(a.run_id)}`}>运行 A ↗</Link>
              </th>
              <th>
                <Link to={`/research/runs/${encodeURIComponent(b.run_id)}`}>运行 B ↗</Link>
              </th>
              <th>差值</th>
            </tr>
          </thead>
          <tbody>{keys.map(key => <tr key={key}>
            <td>{key}</td>
            <td>{fmt(va[key], 6)}</td>
            <td>{fmt(vb[key], 6)}</td>
            <td>{va[key] === undefined || vb[key] === undefined ? '—' : fmt(vb[key] - va[key], 6)}</td>
          </tr>)}</tbody>
        </table>
      </div>{!keys.length && <Empty title="选定情景没有可比较的指标" />}<details className="s-evidence">
        <summary>核对输入与参数</summary>
        <dl className="s-facts">
          <div>
            <dt>运行 A 输入摘要</dt>
            <dd>{String(left.metrics.input_hash ?? '未声明')}</dd>
          </div>
          <div>
            <dt>运行 B 输入摘要</dt>
            <dd>{String(right.metrics.input_hash ?? '未声明')}</dd>
          </div>
          <div>
            <dt>运行 A 参数</dt>
            <dd>{JSON.stringify(left.params)}</dd>
          </div>
          <div>
            <dt>运行 B 参数</dt>
            <dd>{JSON.stringify(right.params)}</dd>
          </div>
        </dl>
      </details>
    </Panel>}</>;
}
