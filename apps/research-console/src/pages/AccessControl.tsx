import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchAccessControlSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { AccessControlPayload, AccessControlRole } from '@/lib/types/research';

const fallbackAccess: AccessControlPayload = {
  source: 'FRONTEND_ACCESS_CONTROL_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  access_mode: 'READ_ONLY_RBAC_AUDIT_EXPORT',
  summary: {
    roles: 2,
    users: 4,
    permission_rows: 8,
    allowed_read_permissions: 4,
    denied_dangerous_permissions: 4,
    export_packages: 1,
    access_reviews: 1,
    live_authorization_allowed: false,
  },
  roles: [
    {role_id: 'role-research-viewer', name: 'Research Viewer', name_zh: '研究只读', scope: 'READ_ONLY_DASHBOARDS', users: 3, permissions: ['read:overview', 'read:alpha'], denied_capabilities: ['trade:any', 'credential:any']},
    {role_id: 'role-compliance', name: 'Compliance Reviewer', name_zh: '合规复核', scope: 'READ_ONLY_GOVERNANCE', users: 1, permissions: ['read:evidence-ledger'], denied_capabilities: ['approve:broker', 'trade:any']},
  ],
  permission_matrix: [
    {role_id: 'role-research-viewer', permission: 'read:overview', allowed: true, reason: 'Role scoped read-only permission'},
    {role_id: 'role-research-viewer', permission: 'trade:any', allowed: false, reason: 'Forbidden by L2 dry-run safety boundary'},
  ],
  export_packages: [
    {package_id: 'EXP-AUDIT-FALLBACK', name: 'Model risk evidence pack', name_zh: '模型风险证据包', status: 'READY_FOR_REVIEW', records: 3, source_root_hash: 'fallback-root', requested_by_role: 'role-compliance', export_mode: 'METADATA_ONLY_READ_ONLY', content_hash: 'fallback-export', contains_credentials: false, contains_live_orders: false, download_enabled: false},
  ],
  access_reviews: [
    {review_id: 'AR-001', role_id: 'role-compliance', status: 'PENDING_ATTESTATION', owner: 'Compliance', age_hours: 18, finding: 'Review membership'},
  ],
  forbidden_capabilities: [
    {capability: 'trade:any', status: 'DENIED', reason: 'L2_DRY_RUN_ONLY boundary'},
    {capability: 'credential:read', status: 'DENIED', reason: 'No credentials in research console'},
  ],
  access_policy: {
    read_only_console: true,
    least_privilege: true,
    segregation_of_duties_required: true,
    signed_export_required: true,
    mutable_permissions: false,
    downloads_enabled: false,
    authorizes_live_trading: false,
  },
};

function RoleCard({role}: {role: AccessControlRole}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-bold text-slate-100">{role.name_zh} / {role.name}</div>
          <div className="mt-1 break-words text-slate-500">{role.role_id}</div>
        </div>
        <span className="rounded bg-cyan-950/40 px-2 py-0.5 text-cyan-300">{role.users}</span>
      </div>
      <div className="mt-3 text-slate-400">{role.scope}</div>
      <div className="mt-2 flex flex-wrap gap-1">
        {role.permissions.slice(0, 4).map(permission => <span key={permission} className="rounded bg-emerald-950/40 px-2 py-0.5 text-emerald-300">{permission}</span>)}
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {role.denied_capabilities.slice(0, 3).map(capability => <span key={capability} className="rounded bg-red-950/40 px-2 py-0.5 text-red-300">{capability}</span>)}
      </div>
    </div>
  );
}

function PolicyBoard({access}: {access: AccessControlPayload}) {
  const policies: Array<[string, boolean]> = [
    ['READ_ONLY_CONSOLE', access.access_policy.read_only_console],
    ['LEAST_PRIVILEGE', access.access_policy.least_privilege],
    ['SEGREGATION_OF_DUTIES', access.access_policy.segregation_of_duties_required],
    ['SIGNED_EXPORT_REQUIRED', access.access_policy.signed_export_required],
    ['IMMUTABLE_PERMISSIONS', !access.access_policy.mutable_permissions],
    ['DOWNLOADS_DISABLED', !access.access_policy.downloads_enabled],
    ['NO_LIVE_AUTHORIZATION', !access.access_policy.authorizes_live_trading],
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('访问策略', 'Access Policy')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {policies.map(([label, passed]) => (
          <div key={label} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-300">{label}</span>
            <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ForbiddenBoard({access}: {access: AccessControlPayload}) {
  return (
    <div className="bg-red-950/30 border border-red-900/50 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-red-300">{bi('禁止能力', 'Forbidden Capabilities')}</h2>
      <div className="space-y-2 text-xs">
        {access.forbidden_capabilities.map(item => (
          <div key={item.capability} className="rounded border border-red-900/50 bg-slate-950/50 p-3">
            <div className="flex justify-between gap-2">
              <span className="font-bold text-red-200">{item.capability}</span>
              <span className="text-red-300">{item.status}</span>
            </div>
            <div className="mt-1 text-red-100">{item.reason}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AccessControl() {
  const [payload, setPayload] = useState<AccessControlPayload | null>(null);
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
    fetchAccessControlSnapshot(controller.signal)
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
  const access = usingBackend && payload ? payload : fallbackAccess;
  const dangerousDenied = useMemo(
    () => access.permission_matrix.filter(item => !item.allowed && ['approve:live', 'trade:any', 'credential:any'].includes(item.permission)).length,
    [access.permission_matrix],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('访问控制 / 审计导出', 'Access Control / Audit Export')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={access.access_mode} />
              <DecisionBadge decision={access.summary.live_authorization_allowed ? 'APPROVED' : 'NO_GO'} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('导出模式', 'Export')}: METADATA_ONLY_READ_ONLY</div>
            <div>{bi('下载', 'Downloads')}: {access.access_policy.downloads_enabled ? 'enabled' : 'disabled'}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={access.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('角色', 'Roles')} value={access.summary.roles} />
        <MetricCard label={bi('用户', 'Users')} value={access.summary.users} />
        <MetricCard label={bi('权限行', 'Permission Rows')} value={access.summary.permission_rows} />
        <MetricCard label={bi('只读允许', 'Read Allows')} value={access.summary.allowed_read_permissions} />
        <MetricCard label={bi('危险拒绝', 'Danger Denied')} value={dangerousDenied} />
        <MetricCard label={bi('导出包', 'Exports')} value={access.summary.export_packages} />
        <MetricCard label={bi('访问复核', 'Access Reviews')} value={access.summary.access_reviews} />
        <MetricCard label={bi('实盘授权', 'Live Auth')} value={access.summary.live_authorization_allowed ? 'YES' : 'NO'} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('角色边界', 'Role Boundaries')}</h2>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {access.roles.map(role => <RoleCard key={role.role_id} role={role} />)}
          </div>
        </div>
        <div className="space-y-4">
          <ForbiddenBoard access={access} />
          <PolicyBoard access={access} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase">
                <th className="p-2 text-left">{bi('导出包', 'Export Package')}</th>
                <th className="p-2 text-left">{bi('状态', 'Status')}</th>
                <th className="p-2 text-right">{bi('记录', 'Records')}</th>
                <th className="p-2 text-left">{bi('模式', 'Mode')}</th>
                <th className="p-2 text-left">{bi('下载', 'Download')}</th>
                <th className="p-2 text-left">Hash</th>
              </tr>
            </thead>
            <tbody>
              {access.export_packages.map(pack => (
                <tr key={pack.package_id} className="border-b border-slate-800/50">
                  <td className="p-2">
                    <div className="font-bold text-slate-200">{pack.name_zh} / {pack.name}</div>
                    <div className="text-slate-500">{pack.package_id}</div>
                  </td>
                  <td className="p-2 text-amber-300">{pack.status}</td>
                  <td className="p-2 text-right text-slate-300">{pack.records}</td>
                  <td className="p-2 text-cyan-300">{pack.export_mode}</td>
                  <td className={pack.download_enabled ? 'p-2 text-emerald-300' : 'p-2 text-red-300'}>{pack.download_enabled ? 'ENABLED' : 'DISABLED'}</td>
                  <td className="p-2 font-mono text-slate-500">{pack.content_hash}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
          <table className="w-full min-w-[700px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase">
                <th className="p-2 text-left">{bi('复核', 'Review')}</th>
                <th className="p-2 text-left">{bi('角色', 'Role')}</th>
                <th className="p-2 text-left">{bi('状态', 'Status')}</th>
                <th className="p-2 text-left">{bi('负责人', 'Owner')}</th>
                <th className="p-2 text-right">Age</th>
                <th className="p-2 text-left">{bi('发现', 'Finding')}</th>
              </tr>
            </thead>
            <tbody>
              {access.access_reviews.map(review => (
                <tr key={review.review_id} className="border-b border-slate-800/50">
                  <td className="p-2 font-bold text-cyan-300">{review.review_id}</td>
                  <td className="p-2 text-slate-300">{review.role_id}</td>
                  <td className={review.status.includes('BLOCKED') ? 'p-2 text-red-300' : 'p-2 text-amber-300'}>{review.status}</td>
                  <td className="p-2 text-slate-400">{review.owner}</td>
                  <td className="p-2 text-right text-slate-300">{review.age_hours}h</td>
                  <td className="p-2 text-slate-500">{review.finding}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
