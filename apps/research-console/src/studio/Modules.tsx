import { useState } from 'react';
import { Link } from '@/lib/router';
import { useMftReadModel } from '@/workbench/useMftReadModel';
import { fetchConsoleModuleSnapshot, fetchHourlyAlphaSnapshot } from '@/lib/api/research-console-api';
import { CONSOLE_MODULES, classifyConsoleModule } from '@/workbench/module-registry';
import { getOperator, getRunnerToken, setOperator, setRunnerToken, type RunnerHealth } from '@/lib/api/research-runner-api';
import { Badge, Empty, ErrorNote, Header, Icon, Loading, Metric, Panel } from './components';
import { download, useResource } from './data';
const GROUPS: Record<string, string> = { DATA: '市场与数据', ALPHA: 'Alpha 研究', VALIDATION: '验证与证据', PORTFOLIO_RISK: '组合与风险', EXECUTION: '执行观察', GOVERNANCE_SYSTEM: '系统与审计' };
const LABELS: Record<string, string> = { status: '状态', name: '名称', symbol: '标的', timestamp: '时间', rows: '记录', count: '数量', source: '数据来源', read_only: '只读', data_provenance: '数据谱系', snapshot_status: '快照状态', snapshot_id: '快照编号', updated_at: '更新时间', reason: '原因', errors: '错误', warnings: '提示', components: '组件', services: '服务', datasets: '数据集', items: '项目', evidence: '证据', summary: '摘要', metrics: '指标', healthy: '健康', mode: '模式', records: '记录', assets: '资产', orders: '订单', positions: '持仓', balance: '余额', available: '可用', price: '价格', volume: '成交量' };
const label = (key: string) => LABELS[key] ?? key.replace(/_/g, ' ');
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const scalar = (v: unknown) => v === null || v === undefined ? '—' : typeof v === 'boolean' ? (v ? '是' : '否') : String(v);
function Records({ rows, depth = 0 }: {
  rows: unknown[];
  depth?: number;
}) {
  const [page, setPage] = useState(0);
  const size = 15;
  const visible = rows.slice(page * size, (page + 1) * size);
  const keys = [...new Set(rows.slice(0, 30).flatMap(row => isObject(row) ? Object.keys(row) : []))].slice(0, 7);
  if (!rows.length)
    return <p className="s-pad s-muted">没有记录。</p>;
  return <>
    <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
      <table className="s-table">
        <thead>
          <tr>{keys.length ? keys.map(k => <th key={k}>{label(k)}</th>) : <th>值</th>}</tr>
        </thead>
        <tbody>{visible.map((row, i) => <tr key={i}>{keys.length && isObject(row) ? keys.map(k => <td key={k}>{isObject(row[k]) || Array.isArray(row[k]) ? <details>
          <summary>查看内容</summary>
          <Tree value={row[k]} depth={depth + 1} />
        </details> : scalar(row[k])}</td>) : <td>{scalar(row)}</td>}</tr>)}</tbody>
      </table>
    </div>{rows.length > size && <div className="s-pagination">
      <span>{page * size + 1}–{Math.min((page + 1) * size, rows.length)} / {rows.length}</span>
      <button className="s-button" disabled={!page} onClick={() => setPage(n => n - 1)}>上一页</button>
      <button className="s-button" disabled={(page + 1) * size >= rows.length} onClick={() => setPage(n => n + 1)}>下一页</button>
    </div>}</>;
}
function Tree({ value, depth = 0 }: {
  value: unknown;
  depth?: number;
}) {
  if (depth > 5)
    return <span className="s-muted">嵌套内容请导出完整快照查看。</span>;
  if (Array.isArray(value))
    return <Records rows={value} depth={depth} />;
  if (!isObject(value))
    return <span>{scalar(value)}</span>;
  const entries = Object.entries(value);
  const flat = entries.filter(([, v]) => !isObject(v) && !Array.isArray(v));
  const nested = entries.filter(([, v]) => isObject(v) || Array.isArray(v));
  return <>
    <dl className="s-facts">{flat.map(([key, v]) => <div key={key}>
      <dt>{label(key)}</dt>
      <dd>{scalar(v)}</dd>
    </div>)}</dl>{nested.map(([key, v]) => <details className="s-tree" key={key} open={depth === 0 && nested.length <= 3}>
      <summary>{label(key)} <span>{Array.isArray(v) ? `${v.length} 项` : `${Object.keys(v as object).length} 个字段`}</span>
      </summary>
      <Tree value={v} depth={depth + 1} />
    </details>)}</>;
}
function SnapshotModule({ selectedId = 'data-catalog' }: {
  selectedId?: string;
}) {
  const [query, setQuery] = useState('');
  const selected = CONSOLE_MODULES.find(m => m.id === selectedId) ?? CONSOLE_MODULES[0];
  const result = useResource(`module:${selected.id}`, signal => selected.id === 'hourly-alpha' ? fetchHourlyAlphaSnapshot(signal).then(value => value as unknown as Record<string, unknown>) : fetchConsoleModuleSnapshot<Record<string, unknown>>(selected.endpoint, CONSOLE_MODULES, signal), 15000);
  const state = result.data && !result.error ? classifyConsoleModule(result.data) : 'UNAVAILABLE';
  const visible = CONSOLE_MODULES.filter(m => `${m.label} ${m.id} ${GROUPS[m.group]}`.toLowerCase().includes(query.toLowerCase()));
  const meta = ['source', 'read_only', 'api_version', 'data_provenance', 'snapshot_id', 'snapshot_ids', 'snapshot_status', 'authoritative', 'evidence_status', 'invalidation_code'];
  return <>
    <Header eyebrow="RESEARCH / CONNECTED MODULES" title="数据与模块">浏览 {CONSOLE_MODULES.length} 个后端只读模块，按来源区分真实数据与技术诊断。</Header>
    <div className="s-module-layout">
      <aside className="s-module-list">
        <label className="s-search">
          <Icon name="search" />
          <input aria-label="筛选模块" placeholder="查找模块或数据源" value={query} onChange={e => setQuery(e.target.value)} />
        </label>{Object.entries(GROUPS).map(([group, title]) => <section key={group}>
          <h2>{title} <span>{visible.filter(m => m.group === group).length}</span>
          </h2>{visible.filter(m => m.group === group).map(m => <Link key={m.id} className={m.id === selected.id ? 'selected' : ''} to={`/studio/modules/${m.id}`}>{m.label.split(' / ')[0]}<Icon name="arrow" />
          </Link>)}</section>)}{!visible.length && <p className="s-pad">没有匹配的模块。</p>}</aside>
      <div className="s-module-content">
        <Panel title={selected.label} note={GROUPS[selected.group]} action={<div className="s-controls">
          <Badge value={result.loading && !result.data ? 'UNKNOWN' : state} />
          <button className="s-icon-button" aria-label="刷新模块" onClick={result.refresh}>
            <Icon name="refresh" />
          </button>{result.data && <button className="s-button" onClick={() => download(`${selected.id}.json`, result.data)}>导出快照</button>}</div>}>
          <ErrorNote error={result.error} retry={result.refresh} />{result.loading && !result.data ? <Loading /> : result.data && !result.error ? (result.data.snapshot_status === 'NO_SNAPSHOT' ? <Empty title="当前没有快照">尚无真实运行结果；不会把缺失样本显示为零值。</Empty> : state === 'SYNTHETIC' ? <Empty title="当前接口只有占位数据">占位内容不会作为真实研究结果展示。</Empty> : <Tree value={Object.fromEntries(Object.entries(result.data).filter(([k]) => !meta.includes(k)))} />) : <Empty title="这个模块暂时没有可读数据">其他模块仍可独立使用。</Empty>}</Panel>{result.data && <details className="s-evidence">
            <summary>数据来源与快照标记</summary>
            <Tree value={Object.fromEntries(Object.entries(result.data).filter(([k]) => meta.includes(k)))} />
          </details>}</div>
    </div>
  </>;
}
export function System({ health, error, refresh }: {
  health: RunnerHealth | null;
  error: string | null;
  refresh: () => void;
}) {
  const [operator, changeOperator] = useState(getOperator);
  const [token, changeToken] = useState(getRunnerToken);
  const [saved, setSaved] = useState(false);
  return <>
    <Header eyebrow="WORKSPACE / SYSTEM" title="系统状态" action={<button className="s-button" onClick={refresh}>
      <Icon name="refresh" />重新检查</button>}>连接、数据与运行边界集中在这里查看。</Header>
    <div className="s-metrics">
      <Metric label="研究模式" value="L2" note="L2_DRY_RUN_ONLY" />
      <Metric label="研究引擎" value={health ? '已连接' : '未连接'} note={health?.mode ?? '等待引擎响应'} />
      <Metric label="研究入口" value={health?.kinds.length ?? '—'} note="由后端白名单提供" />
      <Metric label="交易执行" value="关闭" note="当前界面只提供研究任务" />
    </div>
    <ErrorNote error={error} retry={refresh} />
    <Panel title="研究引擎访问" note="设置一次后，新任务与取消操作共用当前标签页的令牌。">
      <form className="s-connection-form" onSubmit={event => { event.preventDefault(); setOperator(operator); setRunnerToken(token); setSaved(true); }}>
        <label>操作者标签<input value={operator} onChange={e => { changeOperator(e.target.value); setSaved(false); }} autoComplete="off" />
        </label>
        <label>研究引擎令牌<input type="password" value={token} onChange={e => { changeToken(e.target.value); setSaved(false); }} autoComplete="off" />
        </label>
        <button className="s-button" disabled={!operator.trim() || !token.trim()}>保存到当前会话</button>{saved && <span role="status">已保存；提交任务时由引擎验证。</span>}</form>
    </Panel>
    <div className="s-two-col">
      <Panel title="模块连接" note="只读查看各领域的现有数据">{[['system-health', '服务健康', '检查组件、任务和数据状态'], ['runtime-contract', '运行契约', '查看部署身份与实际运行边界'], ['data-catalog', '数据目录', '检查输入数据与时间可用性'], ['mft-execution', '执行读模型', '查看现有执行记录与连接状态'], ['safety-primitives', '风险控制', '检查风险和停机状态'], ['events', '审计事件', '追踪保留的系统事件']].filter(([id]) => CONSOLE_MODULES.some(m => m.id === id)).map(([id, title, desc]) => <Link className="s-system-link" to={`/studio/modules/${id}`} key={id}>
        <span>
          <strong>{title}</strong>
          <small>{desc}</small>
        </span>
        <Icon name="arrow" />
      </Link>)}</Panel>
      <Panel title="研究能力" note="当前可执行的任务类型">
        <div className="s-capabilities">{health?.kinds.map(kind => <Link to={`/studio/new?kind=${kind}`} key={kind}>
          <span>{kind}</span>
          <Icon name="arrow" />
        </Link>) ?? <Empty title="等待引擎连接" />}</div>
        <details className="s-evidence">
          <summary>查看隔离的历史入口</summary>
          <ul className="s-pad">{Object.entries(health?.quarantined_kinds ?? {}).map(([kind, reason]) => <li key={kind}>{kind} · {reason}</li>)}</ul>
        </details>
      </Panel>
    </div>
    <p className="s-footnote">研究计算完成与经济权威是不同状态。这里不提供交易、晋级、凭证或风险控制修改入口。</p>
  </>;
}
export function Modules({ selectedId = 'data-catalog' }: {
  selectedId?: string;
}) {
  if(!CONSOLE_MODULES.some(module=>module.id===selectedId)) return <><Header eyebrow="404 / MODULE" title="模块不存在"/><Empty title="请从模块目录重新选择" action={<Link className="s-button" to="/studio/modules">全部模块</Link>}/></>;
  return selectedId === 'mft-execution' ? <ExecutionModule /> : <SnapshotModule selectedId={selectedId} />;
}
function ExecutionModule() {
  const [revision, setRevision] = useState(0);
  return <ExecutionStream key={revision} refresh={() => setRevision(n => n + 1)} />;
}
function ExecutionStream({ refresh }: {
  refresh: () => void;
}) {
  const model = useMftReadModel();
  const snapshot = model.phase === 'LIVE' ? model.snapshot : null;
  return <>
    <Header eyebrow="DATA / EXECUTION READ MODEL" title="执行读模型" action={<button className="s-button" onClick={refresh}>
      <Icon name="refresh" />重新连接</button>}>增量按哈希链校验；断流或过期时隐藏执行值。</Header>
    <Link className="s-back" to="/studio/modules">← 全部数据与模块</Link>
    <div className="s-metrics">
      <Metric label="连接状态" value={model.phase === 'LIVE' ? '已同步' : model.phase === 'LOADING' ? '正在连接' : '等待同步'} note={model.transport === 'NONE' ? '尚未建立数据连接' : model.transport} />
      <Metric label="当前事件序号" value={snapshot?.watermark?.sequence ?? '—'} note={snapshot ? '已验证快照与增量' : '数据不可用于当前判断'} />
      <Metric label="会话事件" value={snapshot ? model.events.length : '—'} note="同一事件不会重复应用" />
      <Metric label="视图类型" value="只读" note="L2 研究环境" />
    </div>
    {model.phase === 'LOADING' ? <Loading /> : !snapshot ? <Panel title="执行数据暂不可用">
      <Empty title={model.phase === 'RECONNECTING' ? '连接已中断，正在恢复' : '当前没有可用的执行快照'}>
        <span>{model.reason ?? model.phase}</span>
      </Empty>
    </Panel> : <>
      <Panel title="执行状态" note={`current authority sequence ${snapshot.watermark?.sequence ?? 0}`}>
        <Tree value={{ orders: snapshot.oms, positions: snapshot.positions, risk: snapshot.risk }} />
      </Panel>
      <Panel title="市场与对账">
        <Tree value={{ market_gap: snapshot.market_gap, reconciliation: snapshot.reconciliation, tca: snapshot.tca }} />
      </Panel>
      <Panel title="增量事件" note={model.eventHistory}>{model.events.length ? <div className="s-table-wrap" tabIndex={0} role="region" aria-label="可横向滚动的数据表格">
        <table className="s-table">
          <tbody>{model.events.map(event => <tr key={event.sequence} data-testid="authority-event">
            <td>{event.sequence}</td>
            <td>{String(event.payload.event_type ?? event.section)}</td>
            <td>{event.event_id}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="s-pad s-muted">NO_SESSION_DELTA_EVENTS · {model.eventHistory}</p>}</Panel>
    </>}
    <details className="s-evidence">
      <summary>同步诊断</summary>
      <div className="s-pad">{model.phase} · {model.reason ?? 'NONE'} · {model.transport} · DIAGNOSTIC_ONLY</div>
    </details>
  </>;
}
