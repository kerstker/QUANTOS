import React, { useMemo } from 'react';
import { fetchAccessControlSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { AccessControlPayload, AccessControlRole } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, Chip, type Column,
} from '@/ui/primitives';
import { num, shortHash } from '@/ui/format';

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

type ExportPackage = AccessControlPayload['export_packages'][number];
type AccessReview = AccessControlPayload['access_reviews'][number];

function RoleCard({ role }: { role: AccessControlRole }) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-line bg-gradient-to-b from-elevated/30 to-panel p-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-ink">{role.name_zh} <span className="font-medium text-muted">/ {role.name}</span></div>
          <div className="mt-0.5 truncate text-2xs text-faint mono">{role.role_id}</div>
        </div>
        <Chip label={bi(`${role.users} 用户`, `${role.users} users`)} tone="info" />
      </div>

      <Chip label={role.scope} tone="accent" className="self-start" />

      <div className="flex flex-col gap-1.5">
        <div className="text-2xs uppercase tracking-wide text-faint">{bi('允许', 'Allowed')}</div>
        <div className="flex flex-wrap gap-1">
          {role.permissions.map(p => (
            <span key={p} className="inline-flex items-center gap-1 text-2xs">
              <GateDot ok={true} /><span className="mono text-pos">{p}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="text-2xs uppercase tracking-wide text-faint">{bi('拒绝', 'Denied')}</div>
        <div className="flex flex-wrap gap-1">
          {role.denied_capabilities.map(c => (
            <span key={c} className="inline-flex items-center gap-1 text-2xs">
              <GateDot ok={false} /><span className="mono text-neg">{c}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function AccessControl() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<AccessControlPayload>(
    s => fetchAccessControlSnapshot(s));

  const access = usingBackend ? payload! : fallbackAccess;
  const policy = access.access_policy;

  const dangerousDenied = useMemo(
    () => access.permission_matrix.filter(
      item => !item.allowed && ['approve:live', 'trade:any', 'credential:any'].includes(item.permission)).length,
    [access.permission_matrix],
  );

  // Permission matrix as a boolean grid: rows = roles, cols = distinct permissions.
  const matrixRoles = useMemo(() => {
    const seen = new Set<string>();
    const order: string[] = [];
    for (const item of access.permission_matrix) {
      if (!seen.has(item.role_id)) { seen.add(item.role_id); order.push(item.role_id); }
    }
    return order;
  }, [access.permission_matrix]);

  const matrixPerms = useMemo(() => {
    const seen = new Set<string>();
    const order: string[] = [];
    for (const item of access.permission_matrix) {
      if (!seen.has(item.permission)) { seen.add(item.permission); order.push(item.permission); }
    }
    return order;
  }, [access.permission_matrix]);

  const cellAllowed = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const item of access.permission_matrix) m.set(`${item.role_id}|${item.permission}`, item.allowed);
    return m;
  }, [access.permission_matrix]);

  const roleLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of access.roles) m.set(r.role_id, r.name);
    return m;
  }, [access.roles]);

  const policyRows: Array<[string, boolean]> = [
    [bi('只读控制台', 'READ_ONLY_CONSOLE'), policy.read_only_console],
    [bi('最小权限', 'LEAST_PRIVILEGE'), policy.least_privilege],
    [bi('职责分离', 'SEGREGATION_OF_DUTIES'), policy.segregation_of_duties_required],
    [bi('签名导出', 'SIGNED_EXPORT_REQUIRED'), policy.signed_export_required],
    [bi('权限不可变', 'IMMUTABLE_PERMISSIONS'), !policy.mutable_permissions],
    [bi('禁用下载', 'DOWNLOADS_DISABLED'), !policy.downloads_enabled],
    [bi('无实盘授权', 'NO_LIVE_AUTHORIZATION'), !policy.authorizes_live_trading],
  ];

  const exportCols: Column<ExportPackage>[] = [
    { key: 'pkg', header: bi('导出包', 'Export Package'), render: p => (
      <div className="min-w-0">
        <div className="truncate font-medium text-ink">{p.name_zh} <span className="text-muted">/ {p.name}</span></div>
        <div className="truncate text-2xs text-faint mono">{p.package_id}</div>
      </div>
    ) },
    { key: 'status', header: bi('状态', 'Status'), render: p => <StatusChip value={p.status} /> },
    { key: 'records', header: bi('记录', 'Records'), align: 'right', mono: true, render: p => num(p.records) },
    { key: 'mode', header: bi('模式', 'Mode'), render: p => <Chip label={p.export_mode} tone="info" /> },
    { key: 'creds', header: bi('含凭证', 'Credentials'), align: 'center', render: p => <GateDot ok={!p.contains_credentials} label={p.contains_credentials ? 'YES' : 'NONE'} /> },
    { key: 'orders', header: bi('含实盘单', 'Live Orders'), align: 'center', render: p => <GateDot ok={!p.contains_live_orders} label={p.contains_live_orders ? 'YES' : 'NONE'} /> },
    { key: 'dl', header: bi('下载', 'Download'), align: 'center', render: p => <GateDot ok={!p.download_enabled} label={p.download_enabled ? 'ENABLED' : 'DISABLED'} /> },
    { key: 'hash', header: 'Hash', mono: true, render: p => <span className="text-faint">{shortHash(p.content_hash)}</span> },
  ];

  const reviewCols: Column<AccessReview>[] = [
    { key: 'review', header: bi('复核', 'Review'), mono: true, render: r => <span className="text-accent">{r.review_id}</span> },
    { key: 'role', header: bi('角色', 'Role'), render: r => <span className="text-ink">{roleLabel.get(r.role_id) ?? r.role_id}</span> },
    { key: 'status', header: bi('状态', 'Status'), render: r => <StatusChip value={r.status} /> },
    { key: 'owner', header: bi('负责人', 'Owner'), render: r => <span className="text-muted">{r.owner}</span> },
    { key: 'age', header: bi('时长', 'Age'), align: 'right', mono: true, render: r => `${num(r.age_hours)}h` },
    { key: 'finding', header: bi('发现', 'Finding'), render: r => <span className="text-muted">{r.finding}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Access Control / Audit Export" titleZh="访问控制 / 审计导出"
        subtitle={bi('只读 RBAC、最小权限边界、签名审计导出 — L2 干跑禁止任何实盘授权',
          'Read-only RBAC, least-privilege boundaries, signed audit export — no live authorization under L2 dry-run')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={bi('只读', 'READ_ONLY')} tone="info" />
            <Chip label="L2_DRY_RUN_ONLY" tone="warn" />
            <Chip label={access.access_mode} tone="accent" />
            <StatusChip value={access.summary.live_authorization_allowed ? 'APPROVED' : 'NO_GO'} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={access.source} error={error} />
      {/* Honesty: this subsystem is NOT built — needs the Phase 14 identity/RBAC layer. */}
      <div className="rounded-lg border border-warn/30 bg-warn/5 px-3 py-1.5 text-2xs text-warn">
        蓝图 / BLUEPRINT:本页为合成演示,对应的「RBAC/权限层」子系统尚未实现(需 Phase 14 身份/RBAC 层);L2 下维持诚实展示,不伪造审批/权限。 · synthetic demo — the RBAC / access-control layer is not built yet.
      </div>

      <KpiStrip cols={8}>
        <StatTile label={bi('角色', 'Roles')} value={num(access.summary.roles)} accent />
        <StatTile label={bi('用户', 'Users')} value={num(access.summary.users)} />
        <StatTile label={bi('权限行', 'Permission Rows')} value={num(access.summary.permission_rows)} />
        <StatTile label={bi('只读允许', 'Read Allows')} value={num(access.summary.allowed_read_permissions)} tone="pos" />
        <StatTile label={bi('危险拒绝', 'Danger Denied')} value={num(dangerousDenied)} tone="neg"
          sub={bi(`策略拒绝 ${access.summary.denied_dangerous_permissions}`, `policy ${access.summary.denied_dangerous_permissions}`)} />
        <StatTile label={bi('导出包', 'Exports')} value={num(access.summary.export_packages)} />
        <StatTile label={bi('访问复核', 'Reviews')} value={num(access.summary.access_reviews)} tone="warn" />
        <StatTile label={bi('实盘授权', 'Live Auth')} value={access.summary.live_authorization_allowed ? bi('是', 'YES') : bi('否', 'NO')}
          tone={access.summary.live_authorization_allowed ? 'neg' : 'pos'} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('最小权限', 'Least Privilege')} title={bi('角色边界', 'Role Boundaries')}
          right={<Chip label={bi(`${access.roles.length} 角色`, `${access.roles.length} roles`)} tone="info" />}>
          <div className="grid gap-3 md:grid-cols-2">
            {access.roles.map(role => <RoleCard key={role.role_id} role={role} />)}
          </div>
        </Panel>

        <Panel eyebrow={bi('安全边界', 'Safety Boundary')} title={bi('禁止能力', 'Forbidden Capabilities')} tone="neg">
          <div className="flex flex-col gap-2">
            {access.forbidden_capabilities.map(item => (
              <div key={item.capability} className="rounded-lg border border-neg/30 bg-neg/5 p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5">
                    <GateDot ok={false} />
                    <span className="mono text-xs font-semibold text-neg">{item.capability}</span>
                  </span>
                  <Chip label={item.status} tone="neg" />
                </div>
                <div className="mt-1 text-2xs text-muted">{item.reason}</div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('RBAC', 'RBAC')} title={bi('权限矩阵', 'Permission Matrix')}
          right={<span className="inline-flex items-center gap-3 text-2xs"><GateDot ok={true} label={bi('允许', 'Allow')} /><GateDot ok={false} label={bi('拒绝', 'Deny')} /></span>}>
          {matrixRoles.length && matrixPerms.length ? (
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr className="border-b border-line-strong">
                    <th className="px-3 py-2 text-left text-2xs font-semibold uppercase tracking-wide text-faint">{bi('角色', 'Role')}</th>
                    {matrixPerms.map(p => (
                      <th key={p} className="px-3 py-2 text-center text-2xs font-semibold uppercase tracking-wide text-faint">
                        <span className="mono">{p}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {matrixRoles.map(rid => (
                    <tr key={rid} className="border-b border-line/60 hover:bg-elevated/40">
                      <td className="px-3 py-2 align-middle text-ink">{roleLabel.get(rid) ?? rid}</td>
                      {matrixPerms.map(p => {
                        const v = cellAllowed.get(`${rid}|${p}`);
                        return (
                          <td key={p} className="px-3 py-2 text-center align-middle">
                            {v === undefined
                              ? <span className="text-faint">—</span>
                              : <span className="inline-flex justify-center"><GateDot ok={v} /></span>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div className="py-6 text-center text-xs text-faint">{bi('无权限矩阵', 'No permission matrix')}</div>}
        </Panel>

        <Panel eyebrow={bi('治理', 'Governance')} title={bi('访问策略', 'Access Policy')}>
          <div className="flex flex-col gap-2">
            {policyRows.map(([label, ok]) => (
              <div key={label} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2">
                <span className="text-2xs text-muted">{label}</span>
                <GateDot ok={ok} label={ok ? bi('通过', 'PASS') : bi('失败', 'FAIL')} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('审计导出', 'Audit Export')} title={bi('导出包(仅元数据 · 禁用下载)', 'Export Packages (metadata-only · downloads disabled)')}
        right={<Chip label="METADATA_ONLY" tone="info" />}>
        <DataTable columns={exportCols} rows={access.export_packages} getKey={p => p.package_id}
          empty={bi('无导出包', 'No export packages')} />
      </Panel>

      <Panel eyebrow={bi('治理复核', 'Governance Reviews')} title={bi('访问复核', 'Access Reviews')}
        right={<Chip label={bi(`${access.access_reviews.length} 项`, `${access.access_reviews.length} reviews`)} tone="warn" />}>
        <DataTable columns={reviewCols} rows={access.access_reviews} getKey={r => r.review_id}
          empty={bi('无访问复核', 'No access reviews')} />
      </Panel>
    </PageShell>
  );
}
