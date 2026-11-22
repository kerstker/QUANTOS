import { GoldBarResults } from './GoldBarResults';
import {newRequestIdentity} from '@/lib/api/request-identity';
import { useRef, useState } from 'react';
import { Link, useNavigate } from '@/lib/router';
import { fetchResearchRun, fetchTechnicalReplay, fetchForwardDashboard, fetchRegisteredReviewRequest, registeredReviewArchiveUrl } from '@/lib/api/research-console-api';
import { cancelRun, getOperator, getRunnerToken, setOperator, setRunnerToken, triggerRun, type RunnerHealth } from '@/lib/api/research-runner-api';
import type { ResearchRunSummary, RegisteredResearchReport, RegisteredReviewReport, BtcFactoryReport, BtcForwardReport, TechnicalReplayPayload } from '@/lib/types/research';
import { Badge, Empty, ErrorNote, Header, Icon, Loading, Metric, Panel, SparkChart } from './components';
import { download, fmt, KIND_LABELS, stamp, statusLabel, useResource } from './data';
export interface RunsState {
  rows: ResearchRunSummary[];
  loading: boolean;
  error: string | null;
  available: boolean;
  refresh: () => void;
}
export function RunTable({ rows, compact = false }: {
  rows: ResearchRunSummary[];
  compact?: boolean;
}) {
  if (!rows.length)
    return <Empty title="还没有研究运行" action={<Link className="s-button" to="/studio/new">创建第一个实验 <Icon name="arrow" />
    </Link>}>创建研究任务后，进度与结果会保存在这里。</Empty>;
  return <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
    <table className="s-table">
      <thead>
        <tr>
          <th>研究任务</th>
          <th>类型</th>
          <th>状态</th>{!compact && <th>实验编号</th>}<th>创建时间</th>
          <th>
            <span className="s-sr-only">查看</span>
          </th>
        </tr>
      </thead>
      <tbody>{rows.map(r => <tr key={r.run_id}>
        <td>
          <Link className="s-row-title" to={`/research/runs/${encodeURIComponent(r.run_id)}`}>{r.title || KIND_LABELS[r.kind] || r.kind}</Link>
          <small>{r.run_id.slice(0, 20)}</small>
        </td>
        <td>{KIND_LABELS[r.kind] ?? r.kind}</td>
        <td>
          <Badge value={r.status} />
        </td>{!compact && <td>{String(r.params?.attempt ?? '—')}</td>}<td className="s-nowrap">{stamp(r.created_ts)}</td>
        <td>
          <Link className="s-icon-button" aria-label={`查看 ${r.title}`} to={`/research/runs/${encodeURIComponent(r.run_id)}`}>
            <Icon name="arrow" />
          </Link>
        </td>
      </tr>)}</tbody>
    </table>
  </div>;
}
export function Overview({ runs, health }: {
  runs: RunsState;
  health: RunnerHealth | null;
}) {
  const latest = runs.rows.find(r => r.kind === 'btc_factory' && r.status === 'done');
  const result = useResource(`overview:${latest?.run_id}`, signal => latest ? fetchTechnicalReplay(latest.run_id, signal) : Promise.resolve(null));
  const factory = result.data?.technical_replay?.factory_report;
  const center = factory?.cases.find(c => c.policy === 'FACTORY' && c.slip_bps === 5 && c.stop && c.status === 'PASSED');
  return <>
    <Header eyebrow="RESEARCH / OVERVIEW" title="研究工作台" action={<Link className="s-button primary" to="/studio/new">
      <Icon name="plus" />新建研究</Link>}>从机制假设到可复核的实验，让每一次研究都有据可循。</Header>
    <ErrorNote error={runs.error} retry={runs.refresh} />
    <Link to="/studio/demo" className="s-demo-home-entry" aria-label="进入 DEMO 控制中心">
      <span className="s-demo-home-icon"><Icon name="forward"/></span><span><strong>DEMO 控制中心</strong><small>首次启动指引 · 交易进度 · 订单恢复 · 现金对账</small></span>
      <span className="s-demo-home-action">进入工作区 <Icon name="arrow"/></span>
    </Link>
    <div className="s-metrics">
      <Metric label="研究运行" value={runs.available ? runs.rows.length : '—'} note="当前登记库中的记录" />
      <Metric label="正在推进" value={runs.available ? runs.rows.filter(r => ['running', 'queued'].includes(r.status)).length : '—'} note="运行中与排队任务" />
      <Metric label="完成实验" value={runs.available ? runs.rows.filter(r => r.status === 'done').length : '—'} note="计算完成，不代表有效 Alpha" />
      <Metric label="可用研究模块" value={health?.kinds.length ?? '—'} note={health ? '研究引擎已连接' : '等待研究引擎连接'} />
    </div>
    <div className="s-overview-grid">
      <Panel title="BTC 工厂 · 技术权益" note="最近完成的工厂回放 / 5 bp 滑点 / 启用亏损停机" action={latest ? <Link className="s-text-link" to={`/research/runs/${encodeURIComponent(latest.run_id)}`}>查看完整结果 <Icon name="arrow" />
      </Link> : undefined}>
        {result.loading && latest ? <Loading /> : center?.equity ? <SparkChart points={center.equity.map(p => ({ time: p.time, value: Number(p.equity) }))} /> : <Empty title="等待首份工厂结果">完成一次工厂规则回放，即可查看连续权益和成本对比。</Empty>}
        {center?.metrics && <div className="s-chart-footer">
          <span>技术净收益 <b className={center.metrics.net_pnl < 0 ? 's-negative' : 's-positive'}>{fmt(center.metrics.net_pnl)} USDC</b>
          </span>
          <span>最大回撤 <b>{fmt(center.metrics.max_drawdown * 100)}%</b>
          </span>
          <span>成交 <b>{center.metrics.fills}</b>
          </span>
        </div>}
      </Panel>
      <section className="s-next">
        <p className="s-eyebrow">RESEARCH FLOW</p>
        <h2>下一步，从这里开始。</h2>
        <p>围绕 BTCUSDC，把机制、回放与前向观察连接起来。</p>{[['01', '机制信息实验', '检验价格、订单流和资金费的信息增量', 'btc_mechanism'], ['02', '工厂规则回放', '比较八种政策与48种成本、风险情景', 'btc_factory'], ['03', '真实时间观察', '冻结对照，积累标签并按月更新模型', 'btc_forward']].map(([n, title, desc, kind]) => <Link to={`/studio/new?kind=${kind}`} className="s-flow-item" key={n}>
          <span>{n}</span>
          <div>
            <strong>{title}</strong>
            <p>{desc}</p>
          </div>
          <Icon name="arrow" />
        </Link>)}</section>
    </div>
    <Panel title="最近运行" note="实时任务与保留的实验记录" action={<Link className="s-text-link" to="/research/runs">全部运行 <Icon name="arrow" />
    </Link>}>{runs.loading && !runs.rows.length ? <Loading /> : !runs.available ? <Empty title="运行登记库尚不可用">请检查系统连接，缺失的记录不会作为零次实验展示。</Empty> : <RunTable rows={runs.rows.slice(0, 6)} compact />}</Panel>
  </>;
}
export function Runs({ runs, initialQuery = '' }: {
  runs: RunsState;
  initialQuery?: string;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [status, setStatus] = useState('all');
  const [kind, setKind] = useState('all');
  const rows = runs.rows.filter(r => (status === 'all' || r.status === status) && (kind === 'all' || r.kind === kind) && `${r.title} ${r.run_id} ${r.kind} ${r.params?.attempt ?? ''}`.toLowerCase().includes(query.toLowerCase()));
  return <>
    <Header eyebrow="RESEARCH / EXPERIMENTS" title="实验运行" action={<div className="s-controls"><Link className="s-button" to="/studio/compare">运行对比</Link><Link className="s-button primary" to="/studio/new">
      <Icon name="plus" />新建研究</Link></div>}>每一次尝试，包括失败的实验，都保留在这里。</Header>
    <ErrorNote error={runs.error} retry={runs.refresh} />
    <Panel title="运行登记" note={`${rows.length} 条匹配记录`} action={<button className="s-button" onClick={runs.refresh}>
      <Icon name="refresh" />刷新</button>}>
      <div className="s-filterbar">
        <label className="s-search">
          <Icon name="search" />
          <input aria-label="搜索运行" placeholder="搜索名称、运行 ID 或实验编号" value={query} onChange={e => setQuery(e.target.value)} />
        </label>
        <select aria-label="按状态筛选" value={status} onChange={e => setStatus(e.target.value)}>{['all', 'running', 'queued', 'done', 'error', 'cancelled'].map(s => <option key={s} value={s}>{s === 'all' ? '全部状态' : statusLabel(s)}</option>)}</select>
        <select aria-label="按研究类型筛选" value={kind} onChange={e => setKind(e.target.value)}>
          <option value="all">全部类型</option>{[...new Set(runs.rows.map(r => r.kind))].map(k => <option key={k} value={k}>{KIND_LABELS[k] ?? k}</option>)}</select>
      </div>{runs.loading && !runs.rows.length ? <Loading /> : !runs.available ? <Empty title="运行登记库尚不可用" /> : rows.length ? <RunTable rows={rows} /> : query || status !== 'all' || kind !== 'all' ? <Empty title="没有匹配的运行" action={<button className="s-button" onClick={() => { setQuery(''); setStatus('all'); setKind('all'); }}>清除筛选</button>} /> : <RunTable rows={[]} />}</Panel>
  </>;
}
export function Launch({ health, initialKind, initialAttempt="1", initialAction="0", initialInvocation='', initialReviewRequest='' }: {
  health: RunnerHealth | null;
  initialKind: string;
  initialAttempt?: string;
  initialAction?: string;
  initialInvocation?: string;
  initialReviewRequest?: string;
}) {
  const nav = useNavigate();
  const [kind, setKind] = useState(initialKind || 'btc_factory');
  const [attempt, setAttempt] = useState(initialAttempt);
  const [nativeInvocation, setNativeInvocation] = useState(initialInvocation);
  const [reviewRequest, setReviewRequest] = useState(initialReviewRequest);
  const [action, setAction] = useState(['0','1','2','3'].includes(initialAction)?initialAction:'0');
  const [goldTemplate,setGoldTemplate]=useState('1'),[captureSeconds,setCaptureSeconds]=useState('30');
  const [resume, setResume] = useState(false);
  const [strategy, setStrategy] = useState('0');
  const [frequency, setFrequency] = useState('60000');
  const [intraday, setIntraday] = useState(false);
  const [operator, setOperatorValue] = useState(getOperator);
  const [token, setToken] = useState(getRunnerToken);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const invocation = useRef<{
    key: string;
    id: string;
  } | null>(null);
  const hasAttempt = ['gold_bar_recover', 'gold_bar_receipt', 'gold_bar_research', 'gold_factory_review', 'gold_collect_input', 'gold_prepare_input', 'gold_replay', 'btc_factory', 'btc_mechanism', 'btc_forward', 'perpetual_alpha', 'orderblock_research'].includes(kind);
  const hasNativeInvocation = ['registered_research', 'registered_research_recover', 'registered_review_request'].includes(kind);
  const hasReviewRequest = ['registered_review', 'registered_review_recover'].includes(kind);
  const valid = (!hasReviewRequest || /^[a-f0-9]{64}$/.test(reviewRequest)) && (!hasNativeInvocation || /^[a-f0-9]{64}$/.test(nativeInvocation)) && (kind!=='gold_collect_input'||(Number.isInteger(Number(goldTemplate))&&Number(goldTemplate)>=1&&Number(goldTemplate)<=1000&&goldTemplate!==attempt&&Number.isInteger(Number(captureSeconds))&&Number(captureSeconds)>=15&&Number(captureSeconds)<=60)) && (!hasAttempt || (Number.isInteger(Number(attempt)) && Number(attempt) >= 1 && Number(attempt) <= 1000)) && (kind !== 'perpetual_replay' || (Number.isInteger(Number(frequency)) && Number(frequency) >= 1 && Number(frequency) <= 3600000));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !valid || !health?.kinds.includes(kind))
      return;
    setBusy(true);
    setError(null);
    setOperator(operator);
    setRunnerToken(token);
    const params: Record<string, unknown> = hasAttempt ? { attempt: Number(attempt) } : {};
    if (hasNativeInvocation) params.native_invocation_id = nativeInvocation;
    if (hasReviewRequest) params.review_request_id = reviewRequest;
    if (kind === 'gold_collect_input') {params.template_id=Number(goldTemplate);params.seconds=Number(captureSeconds);}
    if (kind === 'btc_factory')
      params.resume = resume;
    if (kind === 'btc_forward')
      params.action = Number(action);
    if (kind === 'data_refresh')
      params.intraday = intraday;
    if (kind === 'perpetual_replay') {
      params.strategy_id = Number(strategy);
      params.frequency_ms = Number(frequency);
    }
    const key = JSON.stringify([kind, params, operator]);
    try {
      if (invocation.current?.key !== key)
        invocation.current = { key, id: newRequestIdentity() };
      const result = await triggerRun(kind, params, invocation.current.id);
      nav(`/research/runs/${encodeURIComponent(result.run_id)}`);
    }
    catch (e) {
      setError(e instanceof Error ? e.message : '提交失败');
    }
    finally {
      setBusy(false);
    }
  }
  return <>
    <Header eyebrow="RESEARCH / NEW EXPERIMENT" title="新建研究">选择研究任务，设置参数，然后交给研究引擎。</Header>
    <div className="s-form-layout">
      <form className="s-panel s-form" onSubmit={submit}>
        <div className="s-form-section">
          <span className="s-step">01</span>
          <div>
            <h2>研究任务</h2>
            <p>仅展示当前引擎支持的研究类型。</p>
          </div>
        </div>
        <label>研究类型<select aria-label="研究类型" value={kind} onChange={e => setKind(e.target.value)} disabled={busy}>
          <option value="" disabled>选择研究类型</option>{[...new Set([kind, ...(health?.kinds ?? [])])].filter(Boolean).map(k => <option key={k} value={k}>{KIND_LABELS[k] ?? k}</option>)}</select>
        </label>
        {hasNativeInvocation && <label>冻结运行编号<input aria-label="冻结运行编号" required maxLength={64} pattern="[a-f0-9]{64}" value={nativeInvocation} onChange={e => setNativeInvocation(e.target.value)} disabled={busy}/>
          <small>{kind==='registered_review_request' ? '导出完整原项目及预登记统计政策；材料准备不授予研究或交易资格。' : '使用已冻结输入和评估计划的运行编号；恢复沿用原编号，不重复计算未知的在途调用。'}</small>
        </label>}
        {hasReviewRequest && <label>评审请求编号<input aria-label="评审请求编号" required maxLength={64} pattern="[a-f0-9]{64}" value={reviewRequest} onChange={e=>setReviewRequest(e.target.value)} disabled={busy}/>
          <small>消费已交付的独立回复；缺回复、签名无效或原项目已变化时拒绝。恢复沿用原评审事务。</small>
        </label>}
        {hasAttempt && <label>实验编号<input aria-label="实验编号" type="number" min="1" max="1000" step="1" required value={attempt} onChange={e => setAttempt(e.target.value)} disabled={busy} />
          <small>前向观察使用来源工厂的编号；新的计算使用新的编号。</small>
        </label>}
        {kind === 'gold_collect_input'&&<><label>参数模板快照编号<input type="number" min={1} max={1000} required value={goldTemplate} onChange={e=>setGoldTemplate(e.target.value)}/></label><label>成交采集时长（秒）<input type="number" min={15} max={60} required value={captureSeconds} onChange={e=>setCaptureSeconds(e.target.value)}/></label><p>模板沿用已有快照的价格区域、退出规则、数量和模拟成本。实验编号必须使用未占用的新编号。报价请求可能比成交采集更晚结束。</p></>}
        {kind === 'btc_factory' && <label className="s-checkbox">
          <input type="checkbox" checked={resume} onChange={e => setResume(e.target.checked)} disabled={busy} />恢复同编号的中断实验</label>}
        {kind === 'btc_forward' && <label>前向操作<select aria-label="前向操作" value={action} onChange={e => setAction(e.target.value)} disabled={busy}>
          <option value="0">查看观察状态</option>
          <option value="1">冻结四模型对照</option>
          <option value="3">启用在线月度工厂</option>
          <option value="2">采集数据并推进观察</option>
        </select>
        </label>}
        {kind === 'data_refresh' && <label className="s-checkbox">
          <input type="checkbox" checked={intraday} onChange={e => setIntraday(e.target.checked)} />包含日内数据</label>}
        {kind === 'perpetual_replay' && <div className="s-form-row">
          <label>策略编号<select value={strategy} onChange={e => setStrategy(e.target.value)}>{[0, 1, 2].map(n => <option key={n}>{n}</option>)}</select>
          </label>
          <label>采样间隔（毫秒）<input type="number" min="1" max="3600000" step="1" value={frequency} onChange={e => setFrequency(e.target.value)} />
          </label>
        </div>}
        <div className="s-form-section">
          <span className="s-step">02</span>
          <div>
            <h2>引擎连接</h2>
            <p>令牌仅保存在当前浏览器标签页。</p>
          </div>
        </div>
        <div className="s-form-row">
          <label>操作者标签<input value={operator} onChange={e => setOperatorValue(e.target.value)} autoComplete="off" disabled={busy} />
          </label>
          <label>研究引擎令牌<input type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" disabled={busy} />
          </label>
        </div>
        {error && <div className="s-error" role="alert">{error}</div>}{!health && <p className="s-inline-note">研究引擎尚未连接，请在系统状态页检查连接。</p>}
        <div className="s-form-actions">
          <Link className="s-button" to="/research/runs">返回运行列表</Link>
          <button className="s-button primary" type="submit" disabled={busy || !valid || !operator.trim() || !token.trim() || !health?.kinds.includes(kind)}>{busy ? '正在提交…' : '提交研究任务'}<Icon name="arrow" />
          </button>
        </div>
      </form>
      <aside className="s-form-aside">
        <p className="s-eyebrow">EXPERIMENT NOTES</p>
        <h2>{KIND_LABELS[kind] ?? '研究任务'}</h2>
        <p>{kind === 'gold_bar_recover' ? '核验已归档的原三候选实验并恢复结果发布，保留原试验编号和失败任务。不能恢复未完成采集或失败实验。' : kind === 'gold_bar_receipt' ? '核验对应实验编号的外部签名回执，保留原始三候选试验。不重新回测，也不授予交易权限。' : kind === 'gold_bar_research' ? '读取对应编号的已保存收盘日志和同时段报价，比较三种固定候选并留存模拟实验。需先准备输入；不会自动采集或下单。' : kind === 'gold_factory_review' ? '重新检查已有黄金工厂试验的独立收据与公钥登记。不会重新运行模型或回测，签名核对不授予交易权限。' : kind === 'gold_collect_input' ? '同窗采集 XAUUSDT 公开成交与报价，沿用选定模板的参数，自动装配新快照。属于回溯技术研究，不是事前注册的策略验证。' : kind === 'gold_prepare_input' ? '将对应编号下已保存的公开报价、成交信号和显式成本模板装配为工厂数据快照。成功后可在持续研究计划中选择；本任务不执行联网采集。' : kind === 'gold_replay' ? '读取预先保存的黄金行情与规则，保留逐次信号。此任务不模拟成交、不计算盈利，也不联网采集。' : kind === 'btc_factory' ? '统一账本下比较八种政策、三档滑点和两种风险设置。全部48个情景都会保留。' : kind === 'btc_forward' ? '保留最初冻结的模型作对照。在线版本按月更新，只使用实际收到并已成熟的前向标签。' : '结果会保存至运行登记库，方便复核、比较和继续研究。'}</p>
        <dl>
          <dt>主要研究对象</dt>
          <dd>{kind.startsWith('ashare') ? 'A股 · PIT 数据' : kind.startsWith('gold_') ? 'XAUUSDT · 黄金永续合约' : hasAttempt ? 'BTCUSDC · 永续合约' : '任务固定的公开研究数据'}</dd>
          <dt>运行环境</dt>
          <dd>隔离研究 / L2</dd>
          <dt>结果用途</dt>
          <dd>技术研究与诊断</dd>
        </dl>{kind === 'btc_forward' && <div className="s-tip">新预测窗口为北京时间 08:00–08:05。错过窗口不会补写；训练至少需要180个真实成熟样本。</div>}</aside>
    </div>
  </>;
}
export function FactoryReport({ report }: {
  report: BtcFactoryReport;
}) {
  const [slip, setSlip] = useState(5);
  const [stop, setStop] = useState(true);
  const [policy, setPolicy] = useState('FACTORY');
  const rows = report.cases.filter(r => r.slip_bps === slip && r.stop === stop);
  const activePolicy = rows.some(r => r.policy === policy) ? policy : (rows[0]?.policy ?? policy);
  const selected = rows.find(r => r.policy === activePolicy);
  const baseline = rows.find(r => r.policy === 'FROZEN_PRICE')?.metrics?.net_pnl;
  return <>
    <Panel title="政策比较" note="同一账本规则下的成本与风险情景" action={<div className="s-controls">
      <label>滑点<select aria-label="单边滑点" value={slip} onChange={e => setSlip(Number(e.target.value))}>{[2, 5, 10].map(v => <option key={v} value={v}>{v} bp</option>)}</select>
      </label>
      <label className="s-checkbox">
        <input type="checkbox" checked={stop} onChange={e => setStop(e.target.checked)} />亏损150 USDC停机</label>
    </div>}>
      <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
        <table className="s-table">
          <thead>
            <tr>
              <th>政策</th>
              <th>净收益 USDC</th>
              <th>相对冻结基线</th>
              <th>手续费</th>
              <th>资金费</th>
              <th>回撤</th>
              <th>成交</th>
            </tr>
          </thead>
          <tbody>{rows.map(r => <tr key={r.trial_id} className={activePolicy === r.policy ? 'selected' : ''}>
            <td>
              <button className="s-policy" onClick={() => setPolicy(r.policy)} aria-pressed={activePolicy === r.policy}>{r.policy}<Icon name="arrow" />
              </button>
            </td>{r.metrics ? <>
              <td className={r.metrics.net_pnl < 0 ? 's-negative' : 's-positive'}>{fmt(r.metrics.net_pnl)}</td>
              <td>{baseline === undefined ? '—' : fmt(r.metrics.net_pnl - baseline)}</td>
              <td>{fmt(r.metrics.fees)}</td>
              <td>{fmt(r.metrics.funding)}</td>
              <td>{fmt(r.metrics.max_drawdown * 100)}%</td>
              <td>{r.metrics.fills}</td>
            </> : <td colSpan={6}>
              <Badge value="FAILED" /> {r.error ?? '结果不可用'}</td>}</tr>)}</tbody>
        </table>
      </div>
    </Panel>
    <Panel title={`${policy} · 连续技术权益`} note={`${slip} bp 单边滑点 · ${stop ? '启用' : '未启用'}亏损停机`}>{selected?.equity ? <SparkChart points={selected.equity.map(p => ({ time: p.time, value: Number(p.equity) }))} /> : <Empty title="这个情景没有可用曲线">失败情景不填充为零收益。</Empty>}{selected?.metrics && <div className="s-chart-footer">
      <span>对账残差 <b>{selected.metrics.reconciliation_residual}</b>
      </span>
      <span>停机时间 <b>{stamp(selected.metrics.halted_at, true)}</b>
      </span>{Object.entries(selected.metrics.annual_pnl ?? {}).map(([year, value]) => <span key={year}>{year} 净收益 <b>{fmt(value)}</b>
      </span>)}</div>}</Panel>
    <Panel title="月度选择记录" note="使用此前已经成熟的预测误差，保留现金选择与失败模型">
      <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
        <table className="s-table">
          <thead>
            <tr>
              <th>决策日期</th>
              <th>选择</th>
              <th>成熟样本</th>
              <th>最后标签时间</th>
              <th>零预测 MSE</th>
            </tr>
          </thead>
          <tbody>{report.selection_updates.map(r => <tr key={r.decision_ms}>
            <td>{stamp(r.decision_ms, true)}</td>
            <td>
              <strong>{r.selected}</strong>
            </td>
            <td>{r.n}</td>
            <td>{stamp(r.latest_label_ms, true)}</td>
            <td>{r.zero_mse?.toExponential(3) ?? '—'}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </Panel>
  </>;
}
export function ForwardReport({ report }: {
  report: BtcForwardReport;
}) {
  const online = report.online_factory;
  const completed = report.models.reduce((n, m) => n + m.completed, 0);
  return <>
    <div className="s-metrics">
      <Metric label="观察状态" value={<Badge value={report.status} />} note={stamp(report.observed_at_ms, true)} />
      <Metric label="已记录模型预测" value={report.models.reduce((n, m) => n + m.decisions, 0)} note="四模型各自记录" />
      <Metric label="已评价预测" value={completed} note="仅计算真实成熟标签" />
      <Metric label="在线训练进度" value={online ? `${online.training_available} / ${online.training_required}` : '未启用'} note="不足样本时工厂保持现金" />
    </div>
    <Panel title="冻结模型对照" note={report.reason}>{report.models.length ? <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
      <table className="s-table">
        <thead>
          <tr>
            <th>模型</th>
            <th>冻结时间</th>
            <th>预测</th>
            <th>待成熟</th>
            <th>已评价</th>
            <th>RMSE bp</th>
          </tr>
        </thead>
        <tbody>{report.models.map(m => <tr key={m.candidate_id}>
          <td>
            <strong>{m.model}</strong>
          </td>
          <td>{stamp(m.frozen_at_ms, true)}</td>
          <td>{m.decisions}</td>
          <td>{m.pending}</td>
          <td>{m.completed}</td>
          <td>{fmt(m.rmse_bps)}</td>
        </tr>)}</tbody>
      </table>
    </div> : <Empty title="尚未冻结对照模型" />}</Panel>
    {online && <Panel title="在线月度工厂" note="180个成熟训练样本；选择另需60个在线共同预测，最多使用最近90个。" action={<Badge value={online.status} />}>
      <div className="s-training">
        <div>
          <span>训练样本积累</span>
          <strong>{online.training_available} / {online.training_required}</strong>
        </div>
        <progress max={online.training_required} value={online.training_available} aria-label="在线训练进度" />
      </div>{online.versions.length ? <div className="s-version-list">{online.versions.map(v => <details key={v.version_hash}>
        <summary>
          <strong>{v.month}</strong>
          <Badge value={v.status} />
          <span>{v.selection.selected} · {v.training_n} 训练样本 · {v.selection.n} 选择样本</span>
          <span className="s-text-link">查看模型 ↓</span>
        </summary>
        <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
          <table className="s-table">
            <thead>
              <tr>
                <th>模型</th>
                <th>预测</th>
                <th>待成熟</th>
                <th>已评价</th>
                <th>RMSE bp</th>
                <th>选择 MSE</th>
              </tr>
            </thead>
            <tbody>{v.models.map(m => <tr key={m.candidate_id}>
              <td>{m.model}</td>
              <td>{m.decisions}</td>
              <td>{m.pending}</td>
              <td>{m.completed}</td>
              <td>{fmt(m.rmse_bps)}</td>
              <td>{v.selection.mse[m.model]?.toExponential(3) ?? '—'}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <p className="s-pad s-muted">版本 {v.version_hash} · 最后标签 {stamp(v.selection.latest_label_ms, true)} · 零预测 MSE {v.selection.zero_mse?.toExponential(3) ?? '—'}</p>
      </details>)}</div> : <p className="s-pad s-muted">已启用，等待下一个决策窗口创建月份版本。</p>}{online.latest_decision && <div className="s-chart-footer">
        <span>最近工厂观察 <b>{online.latest_decision.selected}</b>
        </span>
        <span>观察权重 <b>{fmt(online.latest_decision.target_weight * 100)}%</b>
        </span>
        <span>累计观察 <b>{online.factory_decisions} 次</b>
        </span>
      </div>}</Panel>}
    <p className="s-footnote">预测评价与工厂权重仅用于研究观察，没有成交或账户收益。每日新预测窗口为北京时间08:00–08:05，错过不会补写。</p>
  </>;
}
export function Detail({ id }: {
  id: string;
}) {
  const result = useResource(`run:${id}`, signal => Promise.all([fetchResearchRun(id, signal), fetchTechnicalReplay(id, signal)]), 8000);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const run = result.data?.[0].run, technical = result.data?.[1].technical_replay;
  if (result.loading && !result.data)
    return <Loading />;
  if (!run)
    return <>
      <ErrorNote error={result.error} retry={result.refresh} />
      <Empty title="未找到这次运行" action={<Link className="s-button" to="/research/runs">返回实验运行</Link>}>检查运行链接和当前连接的登记库。</Empty>
    </>;
  async function cancel() {
    setCancelling(true);
    setError(null);
    try {
      await cancelRun(id);
      result.refresh();
    }
    catch (e) {
      setError(e instanceof Error ? e.message : '取消失败');
    }
    finally {
      setCancelling(false);
    }
  }
  return <>
    <Link className="s-back" to="/research/runs">← 实验运行</Link>
    <Header eyebrow={`${KIND_LABELS[run.kind] ?? run.kind} / RESULT`} title={run.title} action={<div className="s-controls">
      <Badge value={run.status} />{run.kind==='btc_factory'&&run.status==='done'&&<Link className="s-button" to={`/studio/new?kind=btc_forward&attempt=${String(run.params.attempt??1)}&action=1`}>冻结模型并观察</Link>}{['queued', 'running'].includes(run.status) && <button className="s-button" disabled={cancelling} onClick={() => void cancel()}>{cancelling ? '正在取消…' : '取消任务'}</button>}<button className="s-button" onClick={() => download(`${id}.json`, result.data)}>导出记录</button>
    </div>}>实验 {String(run.params.attempt ?? '—')} · {stamp(run.created_ts)} · {run.code_version?.slice(0, 10) ?? '源码提交未声明'}</Header>
    <ErrorNote error={result.error ?? error} retry={result.refresh} />{run.error && <div className="s-error" role="alert">
      <div>
        <strong>这次运行失败了</strong>
        <p>{run.error}</p>
      </div>
      <Link className="s-button" to={`/studio/new?kind=${run.kind}`}>创建后续任务</Link>
    </div>}
    {technical?.factory_report && <Link className="s-button" to={`/studio/analysis?run=${encodeURIComponent(id)}`}>打开策略分析与图表 →</Link>}
    {technical?.registered_review_report ? <RegisteredReviewResults report={technical.registered_review_report}/> : technical?.registered_research_report ? <RegisteredResearchResults report={technical.registered_research_report}/> : technical?.gold_bar_report ? <GoldBarResults report={technical.gold_bar_report} /> : technical?.factory_report ? <FactoryReport report={technical.factory_report} /> : technical?.forward_report ? <ForwardReport report={technical.forward_report} /> : technical ? <TechnicalMetrics data={technical} /> : <Empty title={['queued', 'running'].includes(run.status) ? '任务正在推进' : '没有可展示的技术结果'}>{['queued', 'running'].includes(run.status) ? '运行结束后会自动显示结果。' : '此记录仅保留运行身份；不会复用已失效的历史收益。'}</Empty>}
    <details className="s-evidence">
      <summary>运行参数、日志与证据</summary>
      <div className="s-evidence-content">
        <dl>
          <dt>运行 ID</dt>
          <dd>{run.run_id}</dd>
          <dt>证据状态</dt>
          <dd>{run.evidence_status ?? 'UNKNOWN'}</dd>
          <dt>完成时间</dt>
          <dd>{stamp(run.finished_ts)}</dd>
        </dl>
        <h3>参数</h3>
        <pre>{JSON.stringify(technical?.params ?? run.params, null, 2)}</pre>
        <h3>运行日志</h3>
        <pre>{run.log?.join('\n') || '没有可读日志。'}</pre>{technical?.caveats?.map((c, i) => <p key={i}>{c}</p>)}</div>
    </details>
  </>;
}
function TechnicalMetrics({ data }: {
  data: NonNullable<TechnicalReplayPayload['technical_replay']>;
}) {
  return <Panel title="技术结果" note="保留原始指标与单位；未提供的值显示为缺失。">
    <dl className="s-facts">{Object.entries(data.metrics).filter(([, v]) => typeof v !== 'object').map(([key, value]) => <div key={key}>
      <dt>{key.replace(/_/g, ' ')}</dt>
      <dd>{value === null ? '—' : String(value)}</dd>
    </div>)}</dl>{Object.entries(data.series).filter(([, rows]) => rows.length > 0).map(([name, rows]) => <details className="s-evidence" key={name}>
      <summary>{name} · {rows.length} 项</summary>
      <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
        <table className="s-table">
          <tbody>{rows.map((row, i) => <tr key={i}>
            <td>{row.label}</td>
            <td>{fmt(row.value, 6)}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>)}</Panel>;
}
export function RegisteredResearchResults({report}: {report: RegisteredResearchReport}) {
  const statusText = {UNKNOWN: '状态未知', INSUFFICIENT: '证据不足', REJECTED: '未通过',
    NOT_REVIEWED: '尚未评审', INDEPENDENT_REVIEW_REQUIRED: '等待独立评审', NOT_APPROVED: '未准入'};
  const labels: Record<keyof RegisteredResearchReport['research_status'], string> = {
    oos: '锁定样本外验证', statistical_review: '统计评审', risk_replay: '风险复算',
    independent_review: '独立评审', candidate_admission: '候选准入',
  };
  const accountLabels: Record<string, string> = {initial_equity: '期初权益', ending_equity: '期末权益',
    price_pnl: '价格损益', fee: '手续费', spread: '价差成本', impact: '冲击成本',
    funding: '资金费', liquidation_fee: '强平费用', insurance_adjustment: '保险调整',
    adl_adjustment: '自动减仓调整', attributed_change: '归因权益变化', residual: '对账残差'};
  return <>
    <Panel title="研究准入状态" note="原生技术计算已完成；各项研究证据仍按各自核验结果展示。">
      <dl className="s-facts">{Object.entries(report.research_status).map(([key, value]) => <div key={key}>
        <dt>{labels[key as keyof typeof labels]}</dt><dd>{statusText[value]}</dd>
      </div>)}</dl>
    </Panel>
    <Panel title="原账本成本与对账" note="精确数值来自原运行；本报告保持技术验证状态。">
      <dl className="s-facts">{Object.entries(report.account).map(([key, value]) => <div key={key}>
        <dt>{accountLabels[key] ?? key}</dt><dd>{value}</dd>
      </div>)}</dl>
    </Panel>
    <details className="s-evidence"><summary>冻结输入与原试验身份</summary><dl>
      <dt>运行编号</dt><dd>{report.plan.invocation_id}</dd>
      <dt>数据版本</dt><dd>{report.plan.search.dataset_version}</dd>
      <dt>候选</dt><dd>{report.plan.trials[0]?.candidate_id ?? '—'}</dd>
      <dt>原试验编号</dt><dd>{report.native_trial_id}</dd>
      <dt>源码提交</dt><dd>{report.plan.source_identity.commit}</dd>
      <dt>冻结计划摘要</dt><dd>{report.plan_sha256}</dd>
      <dt>结果摘要</dt><dd>{report.result_hash}</dd>
    </dl></details>
    <Link className="s-button" to={`/studio/new?kind=registered_review_request&native_invocation_id=${encodeURIComponent(report.plan.invocation_id)}`}>准备独立评审材料</Link>
  </>;
}
export function RegisteredReviewResults({report}: {report: RegisteredReviewReport}) {
  const [busy,setBusy]=useState(false), [error,setError]=useState<string|null>(null);
  const unsigned=report.phase==='REQUEST_PREPARED';
  async function exportMaterial() {
    setBusy(true); setError(null);
    try {
      const payload=await fetchRegisteredReviewRequest(report.review_request_id);
      if (payload.material_status!=='VALID' || !payload.review_request ||
          payload.review_request.request.review_request_id!==report.review_request_id)
        throw new Error('评审材料缺失或无效，无法导出。');
      if (payload.review_request.request.schema === 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V2' ||
          payload.review_request.request.schema === 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V3') {
        const link = document.createElement('a');
        link.href = registeredReviewArchiveUrl(report.review_request_id);
        link.download = `project-review-${report.review_request_id}.tar`;
        link.click();
      } else {
        download(`project-review-${report.review_request_id}.json`,payload.review_request);
      }
    } catch (e) {setError(e instanceof Error ? e.message : '导出失败');}
    finally {setBusy(false);}
  }
  return <>
    <Panel title="原项目独立评审" note={unsigned ? '材料已准备，等待独立评审。' : '签名已核验；研究资格仍按原统计、风险和样本外证据判定。'}>
      <dl className="s-facts">
        <div><dt>回复身份</dt><dd>{unsigned?'等待独立评审':'签名已核验'}</dd></div>
        <div><dt>统计评审</dt><dd>{report.statistical_decision==='PASS'?'统计通过':report.statistical_decision==='FAIL'?'统计未通过':'统计证据不足'}</dd></div>
        <div><dt>锁定样本外验证</dt><dd>{report.oos_status==='UNKNOWN'?'状态未知':'证据不足'}</dd></div>
        <div><dt>风险复算</dt><dd>{report.risk_status==='REJECTED'?'未通过':report.risk_status==='INSUFFICIENT'?'证据不足':'状态未知'}</dd></div>
        <div><dt>评审结论</dt><dd>{report.qualification==='REJECTED'?'评审拒绝':report.qualification==='INSUFFICIENT'?'证据不足':'尚未评审'}</dd></div>
        <div><dt>候选准入</dt><dd>{report.promotion_approved?'候选已准入':'候选未准入'}</dd></div>
        <div><dt>试验集合</dt><dd>{report.attempt_count} 次原尝试</dd></div>
      </dl>
      <button className="s-button" disabled={busy} onClick={exportMaterial}>{busy?'正在导出…':'下载完整评审材料'}</button>
      <Link className="s-button" to={`/studio/new?kind=${unsigned?'registered_review':'registered_review_recover'}&review_request_id=${encodeURIComponent(report.review_request_id)}`}>{unsigned?'消费独立评审回复':'恢复评审结果'}</Link>
      <ErrorNote error={error} retry={()=>void exportMaterial()}/>
    </Panel>
    <details className="s-evidence"><summary>评审身份与缺失证据</summary><dl>
      <dt>评审请求编号</dt><dd>{report.review_request_id}</dd>
      <dt>原运行编号</dt><dd>{report.native_invocation_id}</dd>
      <dt>原评审记录</dt><dd>{report.review_id??'等待回复'}</dd>
      <dt>核查时间</dt><dd>{stamp(report.checked_at_ms,true)}</dd>
    </dl><ul>{report.reasons.map(reason=><li key={reason}>{reason}</li>)}</ul></details>
  </>;
}
export function Forward({ runs }: { runs: RunsState }) {
  const [attempt,setAttempt]=useState('');
  const latest=runs.rows.find(r=>r.kind==='btc_forward'&&r.status==='done');
  const current=useResource('forward-dashboard',fetchForwardDashboard,15000);
  const retained=useResource(`forward:${latest?.run_id}`,signal=>latest?fetchTechnicalReplay(latest.run_id,signal):Promise.resolve(null),15000);
  const observations=current.error?[]:current.data?.observations??[];
  const selected=observations.find(r=>String(r.attempt)===attempt)??observations[0];
  const report=selected??retained.data?.technical_replay?.forward_report;
  const schedule=current.error?null:current.data?.schedule;
  return <>
    <Header eyebrow="RESEARCH / FORWARD LAB" title="前向观察" action={<Link className="s-button primary" to={`/studio/new?kind=btc_forward&attempt=${String(report?.attempt??latest?.params.attempt??1)}&action=2`}><Icon name="plus"/>推进观察</Link>}>保留当时的预测，等待真实时间给出答案。</Header>
    <ErrorNote error={current.error??runs.error??retained.error} retry={()=>{current.refresh();runs.refresh();retained.refresh();}}/>
    <Panel title="持续观察调度" note="每2分钟检查已冻结实验；错过的预测不回填。" action={<Badge value={schedule?(schedule.fresh?schedule.status:'STALE'):'UNAVAILABLE'}/>}>
      <div className="s-analysis-controls"><span>{schedule?'最近检查 '+stamp(schedule.observed_at_ms,true):'尚未收到调度运行回执'}</span>{observations.length>0&&<label>观察实验<select aria-label="观察实验" value={selected?.attempt??''} onChange={e=>setAttempt(e.target.value)}>{observations.map(r=><option key={r.attempt} value={r.attempt}>实验 {r.attempt} · {statusLabel(r.status)}</option>)}</select></label>}</div>
      {schedule?.attempts.filter(r=>r.error).map(r=><p className="s-error" key={r.attempt}>实验 {r.attempt}：{r.error}</p>)}
      {current.data?.errors.map((e,i)=><p className="s-error" key={i}>观察材料无法验证：{e.attempt??'调度'} · {e.reason}</p>)}
    </Panel>
    {current.loading&&!current.data&&retained.loading?<Loading/>:report?<><p className="s-inline-note">{selected?'当前观察材料':'最近手动运行快照'} · {stamp(report.observed_at_ms,true)}</p><ForwardReport report={report}/>{latest&&<Link className="s-text-link" to={`/research/runs/${encodeURIComponent(latest.run_id)}`}>查看最近手动观察记录 <Icon name="arrow"/></Link>}</>:<Empty title="还没有前向观察记录" action={<Link className="s-button" to="/studio/new?kind=btc_forward">开始冻结模型</Link>}>先完成工厂回放，再使用相同实验编号冻结对照模型。</Empty>}
  </>;
}
