import React, { useEffect, useState } from 'react';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchPhase3AReadinessSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockPhase3AReadiness } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { Phase3ABlocker, Phase3AReadinessPayload } from '@/lib/types/research';

const authorizationLabel = (authorized: boolean) => authorized ? "AUTHORIZED" : "NOT_AUTHORIZED";
const criterionZh: Record<string, string> = {
  E01: 'Phase 2H 治理批准',
  E02: '券商测试环境账户确认',
  E03: '法务与合规批准',
  E04: '测试凭证进入 Vault',
  E05: '代码中无凭证',
  E06: '网络访问已审查',
  E07: '券商准备清单',
  E08: '熔断开关已测试',
  E09: '回滚已测试',
  E10: '灾备手册已批准',
  E11: 'UAT 计划已批准',
  E12: '应急响应已批准',
  E13: '无生产凭证',
  E14: '无生产交易',
  E15: 'Phase 2G.1 测试通过',
};
const categoryZh: Record<string, string> = {
  Governance: '治理',
  Broker: '券商',
  Compliance: '合规',
  Credentials: '凭证',
  Security: '安全',
  Network: '网络',
  Operations: '运营',
  Risk: '风险',
  DR: '灾备',
  UAT: '用户验收',
  IR: '事件响应',
  Technical: '技术',
};
const ownerZh: Record<string, string> = {
  CTO: 'CTO',
  'Broker Relationship Owner': '券商关系负责人',
  'Compliance Officer': '合规官',
  'Security Officer': '安全负责人',
  'Operations Lead': '运营负责人',
  'Trading System Owner': '交易系统负责人',
  'Risk Officer': '风险负责人',
};
const evidenceZh: Record<string, string> = {
  'Governance signoff register': '治理签署登记',
  'Broker response intake': '券商回复收集表',
  'Legal/compliance checklist': '法务与合规清单',
  'Vault path and access policy': 'Vault 路径与访问策略',
  'Governance scanner report': '治理扫描报告',
  'Network/IP whitelist review': '网络与 IP 白名单审查',
  'Broker readiness checklist': '券商准备清单',
  'Kill switch drill report': '熔断演练报告',
  'Rollback drill report': '回滚演练报告',
  'DR dry-run report': '灾备干跑报告',
  'UAT dry-run report': 'UAT 干跑报告',
  'Incident response plan': '事件响应计划',
  'Dry-run adapter gate evidence': '干跑适配器门禁证据',
};
const blockerZh: Record<string, string> = {
  E01: '治理签名未完成',
  E02: '券商测试环境未确认',
  E03: '法务与合规批准未完成',
  E04: '测试凭证未在 Vault 中配置',
  E06: '网络访问未审查',
};
const nextActionZh: Record<string, string> = {
  'Collect required role signatures': '收集所需角色签名',
  'Send broker request package': '发送券商请求材料',
  'Review broker agreement and test scope': '审查券商协议与测试范围',
  'Prepare Vault-only intake workflow': '准备仅通过 Vault 接收凭证的流程',
  'Collect broker endpoint and whitelist details': '收集券商端点与白名单信息',
};
const dependencyZh: Record<string, string> = {
  None: '无',
  'Broker response': '券商回复',
  E02: 'E02',
};

function BlockerDependencyMap({blockers}: {blockers: Phase3ABlocker[]}) {
  const criticalBlocker = blockers.find(blocker => blocker.id === 'E02');
  const independentBlockers = blockers.filter(blocker => blocker.dependency !== 'E02' && blocker.id !== 'E02');
  const e02Dependents = blockers.filter(blocker => blocker.dependency === 'E02');

  const renderNode = (blocker: Phase3ABlocker, tone: 'critical' | 'independent' | 'dependent') => {
    const toneClass = {
      critical: 'border-red-800 bg-red-950/30',
      independent: 'border-amber-800 bg-amber-950/20',
      dependent: 'border-slate-700 bg-slate-950/60',
    }[tone];

    return (
      <div key={blocker.id} className={`min-w-0 rounded border p-3 text-xs ${toneClass}`}>
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="font-bold text-cyan-300">{blocker.id}</span>
          <DecisionBadge decision="BLOCKED" />
        </div>
        <div className="font-bold text-slate-100">{bi(blockerZh[blocker.id] ?? blocker.blocker, blocker.blocker)}</div>
        <div className="mt-2 text-slate-400">{bi('负责人', 'Owner')}: {bi(ownerZh[blocker.owner] ?? blocker.owner, blocker.owner)}</div>
        <div className="mt-1 text-slate-500">{bi('依赖', 'Depends on')}: {bi(dependencyZh[blocker.dependency] ?? blocker.dependency, blocker.dependency)}</div>
      </div>
    );
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-4 flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-sm uppercase tracking-wider text-slate-400">{bi('阻塞依赖图', 'Blocker Dependency Map')}</h2>
          <div className="mt-1 text-xs text-slate-500">
            {bi('E02 是外部关键路径；它未关闭前，E03/E04/E06 保持阻塞。', 'E02 is the external critical path; E03/E04/E06 stay blocked until it closes.')}
          </div>
        </div>
        <DecisionBadge decision="NO_GO" />
      </div>

      <div className="grid gap-3 lg:grid-cols-[0.8fr_1fr_1.2fr] lg:items-stretch">
        <div className="space-y-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">{bi('可独立推进', 'Independent')}</div>
          {independentBlockers.map(blocker => renderNode(blocker, 'independent'))}
        </div>
        <div className="space-y-3">
          <div className="text-[10px] uppercase tracking-wider text-red-300">{bi('外部关键路径', 'External Critical Path')}</div>
          {criticalBlocker ? renderNode(criticalBlocker, 'critical') : null}
        </div>
        <div className="space-y-3">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">{bi('被 E02 阻塞', 'Blocked by E02')}</div>
          <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-1">
            {e02Dependents.map(blocker => renderNode(blocker, 'dependent'))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Phase3AReadiness() {
  const [payload, setPayload] = useState<Phase3AReadinessPayload | null>(null);
  const [apiState, setApiState] = useState<ConsoleApiState>('disabled');
  const [apiError, setApiError] = useState<string | null>(null);
  const apiEnabled = isResearchConsoleApiEnabled();

  useEffect(() => {
    if (!apiEnabled) {
      setPayload(null);
      setApiState('disabled');
      setApiError(null);
      return;
    }

    const controller = new AbortController();
    setApiState('loading');
    setApiError(null);
    fetchPhase3AReadinessSnapshot(controller.signal)
      .then(nextPayload => {
        setPayload(nextPayload);
        setApiState('connected');
      })
      .catch(error => {
        if (controller.signal.aborted) {
          return;
        }
        setPayload(null);
        setApiState('error');
        setApiError(error instanceof Error ? error.message : String(error));
      });

    return () => controller.abort();
  }, [apiEnabled]);

  const usingBackend = apiState === 'connected' && payload !== null;
  const readiness = usingBackend && payload ? payload.readiness : mockPhase3AReadiness;
  const approvedPct = Math.round((readiness.approved_count / readiness.total_count) * 100);
  const pending = readiness.total_count - readiness.approved_count;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-100">{bi('Phase 3A 准入状态', 'Phase 3A Readiness')}</h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <DecisionBadge decision={readiness.current_level} />
            <DecisionBadge decision={readiness.phase_status} />
            <DecisionBadge decision={readiness.decision} />
            <DecisionBadge decision={authorizationLabel(readiness.implementation_authorized)} />
          </div>
        </div>
        <div className="text-xs text-slate-500 md:text-right">
          <div>{bi('目标', 'Target')}: {readiness.target_level}</div>
          <div>{bi('门禁', 'Gate')}: {bi('15 项标准必须全部通过', 'all 15 criteria must be APPROVED')}</div>
        </div>
      </div>
      <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricCard label={bi('已通过', 'Approved')} value={readiness.approved_count} />
        <MetricCard label={bi('待处理', 'Pending')} value={pending} />
        <MetricCard label={bi('就绪度', 'Readiness')} value={approvedPct} unit="%" />
        <MetricCard label={bi('阻塞项', 'Blockers')} value={readiness.blockers.length} />
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="uppercase tracking-wider text-slate-400">{bi('准入标准进度', 'Entry Criteria Progress')}</span>
          <span className="font-bold text-slate-200">{readiness.approved_count}/{readiness.total_count}</span>
        </div>
        <div className="h-3 overflow-hidden rounded bg-slate-800">
          <div className="h-full bg-cyan-700" style={{width:`${approvedPct}%`}} />
        </div>
      </div>

      <BlockerDependencyMap blockers={readiness.blockers} />

      <div className="grid gap-2 md:hidden">
        {readiness.criteria.map(c => (
          <div key={c.id} className="bg-slate-900 border border-slate-800 rounded-lg p-3 text-xs">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div>
                <div className="font-bold text-cyan-400">{c.id}</div>
                <div className="mt-1 font-bold text-slate-100">{bi(criterionZh[c.id] ?? c.criterion, c.criterion)}</div>
              </div>
              <DecisionBadge decision={c.status} />
            </div>
            <div className="grid grid-cols-2 gap-2 text-slate-500">
              <div>
                <div className="uppercase text-slate-600">{bi('类别', 'Category')}</div>
                <div className="text-slate-400">{bi(categoryZh[c.category] ?? c.category, c.category)}</div>
              </div>
              <div>
                <div className="uppercase text-slate-600">{bi('负责人', 'Owner')}</div>
                <div className="text-slate-400">{bi(ownerZh[c.owner] ?? c.owner, c.owner)}</div>
              </div>
              <div className="col-span-2">
                <div className="uppercase text-slate-600">{bi('证据', 'Evidence')}</div>
                <div className="text-slate-400">{bi(evidenceZh[c.evidence] ?? c.evidence, c.evidence)}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="hidden bg-slate-900 border border-slate-800 rounded-lg overflow-auto md:block">
        <table className="w-full min-w-[780px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">ID</th>
              <th className="p-2 text-left">{bi('标准', 'Criterion')}</th>
              <th className="p-2 text-left">{bi('类别', 'Category')}</th>
              <th className="p-2 text-left">{bi('负责人', 'Owner')}</th>
              <th className="p-2 text-left">{bi('状态', 'Status')}</th>
              <th className="p-2 text-left">{bi('证据', 'Evidence')}</th>
            </tr>
          </thead>
          <tbody>
            {readiness.criteria.map(c => (
              <tr key={c.id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-400">{c.id}</td>
                <td className="p-2 text-slate-200">{bi(criterionZh[c.id] ?? c.criterion, c.criterion)}</td>
                <td className="p-2 text-slate-400">{bi(categoryZh[c.category] ?? c.category, c.category)}</td>
                <td className="p-2 text-slate-400">{bi(ownerZh[c.owner] ?? c.owner, c.owner)}</td>
                <td className="p-2"><DecisionBadge decision={c.status} /></td>
                <td className="p-2 text-slate-500">{bi(evidenceZh[c.evidence] ?? c.evidence, c.evidence)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.3fr_0.7fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase tracking-wider text-slate-400">{bi('剩余阻塞项', 'Remaining Blockers')}</h2>
          <div className="space-y-2">
            {readiness.blockers.map(b => (
              <div key={b.id} className="grid gap-2 border-b border-slate-800 pb-2 text-xs md:grid-cols-[3rem_1fr_11rem_8rem]">
                <span className="font-bold text-amber-300">{b.id}</span>
                <span className="text-slate-200">{bi(blockerZh[b.id] ?? b.blocker, b.blocker)}</span>
                <span className="text-slate-400">{bi(ownerZh[b.owner] ?? b.owner, b.owner)}</span>
                <span className="text-slate-500">{bi(dependencyZh[b.dependency] ?? b.dependency, b.dependency)}</span>
                <span className="text-slate-500 md:col-start-2 md:col-span-3">{bi(nextActionZh[b.next_action] ?? b.next_action, b.next_action)}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase tracking-wider text-slate-400">{bi('依赖链', 'Dependency Chain')}</h2>
            <div className="space-y-2 text-xs text-slate-300">
              <div>{bi('E01 可独立推进。', 'E01 can proceed independently.')}</div>
              <div>{bi('E02 是外部关键路径。', 'E02 is the external critical path.')}</div>
              <div>{bi('E02 阻塞 E03、E04 和 E06。', 'E02 blocks E03, E04, and E06.')}</div>
            </div>
          </div>
          <div className="bg-red-950/30 border border-red-900/50 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase tracking-wider text-red-300">{bi('安全边界', 'Safety Boundary')}</h2>
            <div className="space-y-1 text-xs text-red-200">
              <div>{bi('无真实 CTP/FIX 实现', 'No real CTP/FIX implementation')}</div>
              <div>{bi('无券商 SDK 导入', 'No broker SDK imports')}</div>
              <div>{bi('无 socket 连接', 'No socket connections')}</div>
              <div>{bi('无凭证文件', 'No credential files')}</div>
              <div>{bi('无实盘下单 API', 'No live order APIs')}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
