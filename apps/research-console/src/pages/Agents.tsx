import { fetchAgentsSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type {
  AgentEngineRecord, AgentRuntimeRecord, AgentsPayload,
} from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  Chip, DataTable, GateDot, KeyValue, KpiStrip, LoadingVeil, PageHeader,
  PageShell, Panel, ProvenanceRibbon, StatTile, StatusChip, type Column,
} from '@/ui/primitives';
import { num } from '@/ui/format';

const fallback: AgentsPayload = {
  source: 'FRONTEND_AGENT_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  data_provenance: 'UNAVAILABLE',
  summary: {
    active: 0, total_runs: 0, completed: 0, succeeded: 0,
    failed: 0, cancelled: 0, agent_auto_approved: 0, registry_status: 'UNAVAILABLE',
  },
  agents: [],
  engines: [],
  observed_run_kinds: [],
  realtime_inputs: {
    status: 'UNAVAILABLE',
    fresh: false,
    age_ms: null,
    source: 'BINANCE_PUBLIC_USDM_WEBSOCKET',
    feature_version: null,
    feature_count: 0,
    instrument_count: 0,
    retained_value_count: 0,
    drift_status: null,
    drift_status_counts: {},
    batch_chain_valid: false,
    consumption_mode: 'READ_ONLY_DIAGNOSTIC_CONTEXT',
    automatic_action: false,
    promotion_authority: false,
  },
  containment: {
    authorized_level: 'L2_DRY_RUN_ONLY',
    research_only: true,
    human_approval_required: true,
    agent_can_reach_approved: false,
    credentials_access: false,
    broker_connectivity: false,
    order_submission: false,
    live_trading: false,
  },
};

export default function Agents() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<AgentsPayload>(
    signal => fetchAgentsSnapshot(signal));
  const model = usingBackend ? payload! : fallback;
  const containment = model.containment;

  const agentColumns: Column<AgentRuntimeRecord>[] = [
    {
      key: 'agent', header: 'Agent',
      render: agent => (
        <div>
          <div className="font-medium text-ink">{agent.name}</div>
          <div className="mono text-2xs text-faint">{agent.agent_id}</div>
        </div>
      ),
    },
    { key: 'state', header: bi('状态', 'State'), render: agent => <StatusChip value={agent.state} /> },
    { key: 'active', header: bi('活动运行', 'Active'), align: 'right', render: agent => num(agent.runs_active) },
    { key: 'done', header: bi('成功', 'Succeeded'), align: 'right', render: agent => num(agent.runs_succeeded) },
    { key: 'failed', header: bi('失败', 'Failed'), align: 'right', render: agent => num(agent.runs_failed) },
    {
      key: 'last', header: bi('最近运行', 'Latest Run'),
      render: agent => (
        <div>
          <div className="mono text-xs text-muted">{agent.last_run_id ?? '—'}</div>
          <div className="text-2xs text-faint">{agent.last_run_kind ?? 'no persisted run'}</div>
        </div>
      ),
    },
    { key: 'approve', header: bi('自动批准', 'Auto Approve'), align: 'center', render: agent => <GateDot ok={agent.can_reach_approved} /> },
    { key: 'orders', header: bi('下单', 'Orders'), align: 'center', render: agent => <GateDot ok={agent.order_submission} /> },
  ];

  const engineColumns: Column<AgentEngineRecord>[] = [
    { key: 'engine', header: bi('搜索引擎', 'Search Engine'), mono: true, render: engine => engine.engine_id },
    { key: 'readiness', header: bi('就绪度', 'Readiness'), render: engine => <StatusChip value={engine.readiness} /> },
    { key: 'implementation', header: bi('真实实现', 'Implementation'), mono: true, render: engine => engine.implementation },
  ];

  return (
    <PageShell>
      <PageHeader title="AI Agent Control Plane" titleZh="AI Agent 控制面"
        subtitle={bi(
          '真实研究运行注册表、搜索能力与不可越权边界；不是虚构人格或演示预算',
          'Real research-run activity, search capabilities, and hard containment; no fictional personas or demo budgets',
        )}
        actions={<Chip label={containment.authorized_level} tone="info" />} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && model.data_provenance === 'RESEARCH_RUN_REGISTRY_AND_GUARDRAIL_CONTRACT'}
        source={model.source} error={error} />

      <LoadingVeil active={apiState === 'loading'}>
        <KpiStrip cols={8}>
          <StatTile label={bi('活动运行', 'Active Runs')} value={num(model.summary.active)}
            tone={model.summary.active > 0 ? 'pos' : 'muted'} />
          <StatTile label={bi('全部运行', 'Total Runs')} value={num(model.summary.total_runs)} accent />
          <StatTile label={bi('已完成', 'Terminal')} value={num(model.summary.completed)} />
          <StatTile label={bi('成功', 'Succeeded')} value={num(model.summary.succeeded)} tone="pos" />
          <StatTile label={bi('失败', 'Failed')} value={num(model.summary.failed)}
            tone={model.summary.failed > 0 ? 'warn' : 'pos'} />
          <StatTile label={bi('搜索引擎', 'Search Engines')} value={num(model.engines.length)} tone="info" />
          <StatTile label={bi('Agent 自动批准', 'Agent Approvals')} value={num(model.summary.agent_auto_approved)} tone="pos" />
          <StatTile label={bi('注册表', 'Registry')} value={model.summary.registry_status}
            tone={model.summary.registry_status === 'AVAILABLE' ? 'pos' : 'warn'} />
        </KpiStrip>

        <Panel eyebrow="RUNTIME" title={bi('研究 Agent 实况', 'Research Agent Runtime')}
          right={<Chip label={`${model.agents.length} LOOP`} tone="accent" />}>
          <DataTable columns={agentColumns} rows={model.agents} getKey={agent => agent.agent_id} dense />
          {model.agents.length === 0 ? (
            <div className="py-8 text-center text-sm text-faint">
              {bi('运行注册表不可用，未伪造 Agent 状态。', 'Run registry unavailable; no agent state was fabricated.')}
            </div>
          ) : null}
        </Panel>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <Panel eyebrow="CAPABILITIES" title={bi('已测试搜索引擎', 'Tested Search Engines')}
            className="xl:col-span-2">
            <DataTable columns={engineColumns} rows={model.engines}
              getKey={engine => engine.engine_id} dense />
          </Panel>
          <Panel eyebrow="CONTAINMENT" title={bi('不可越权边界', 'Hard Safety Boundary')} tone="pos">
            <KeyValue cols={1} items={[
              { k: bi('仅研究', 'Research only'), v: containment.research_only ? 'YES' : 'NO' },
              { k: bi('人工批准必需', 'Human approval required'), v: containment.human_approval_required ? 'YES' : 'NO' },
              { k: bi('可到达 APPROVED', 'Can reach APPROVED'), v: containment.agent_can_reach_approved ? 'YES' : 'NO' },
              { k: bi('凭证访问', 'Credential access'), v: containment.credentials_access ? 'YES' : 'NO' },
              { k: bi('券商连接', 'Broker connectivity'), v: containment.broker_connectivity ? 'YES' : 'NO' },
              { k: bi('订单提交', 'Order submission'), v: containment.order_submission ? 'YES' : 'NO' },
              { k: bi('实盘交易', 'Live trading'), v: containment.live_trading ? 'YES' : 'NO' },
            ]} />
          </Panel>
        </div>

        <Panel eyebrow="REALTIME INPUT" title={bi('实时特征上下文', 'Realtime Feature Context')}
          right={<StatusChip value={model.realtime_inputs.status} />}>
          <KpiStrip cols={6}>
            <StatTile label={bi('特征', 'Features')} value={num(model.realtime_inputs.feature_count)} />
            <StatTile label={bi('标的', 'Instruments')} value={num(model.realtime_inputs.instrument_count)} />
            <StatTile label={bi('保留值', 'Retained Values')} value={num(model.realtime_inputs.retained_value_count)} />
            <StatTile label={bi('漂移状态', 'Drift')} value={model.realtime_inputs.drift_status ?? '—'}
              tone={model.realtime_inputs.drift_status === 'BLOCK' ? 'warn' : 'info'} />
            <StatTile label={bi('批次链', 'Batch Chain')}
              value={model.realtime_inputs.batch_chain_valid ? 'VALID' : 'UNKNOWN'}
              tone={model.realtime_inputs.batch_chain_valid ? 'pos' : 'warn'} />
            <StatTile label={bi('自动动作', 'Automatic Action')}
              value={model.realtime_inputs.automatic_action ? 'YES' : 'NO'} tone="pos" />
          </KpiStrip>
          <div className="mt-3 text-2xs text-muted">
            {bi(
              'Binance 公共实时特征仅作为只读诊断上下文；当前漂移阈值未校准，不驱动 Agent 自动晋级、仓位或订单。',
              'Binance public realtime features are read-only diagnostic context. Uncalibrated drift thresholds never drive agent promotion, positions, or orders.',
            )}
          </div>
        </Panel>

        <Panel eyebrow="OBSERVED" title={bi('持久化运行类型', 'Observed Persisted Run Kinds')}>
          <div className="flex flex-wrap gap-2">
            {model.observed_run_kinds.length
              ? model.observed_run_kinds.map(kind => <Chip key={kind} label={kind} tone="muted" />)
              : <span className="text-sm text-faint">{bi('尚无持久化运行。', 'No persisted runs yet.')}</span>}
          </div>
        </Panel>
      </LoadingVeil>
    </PageShell>
  );
}
