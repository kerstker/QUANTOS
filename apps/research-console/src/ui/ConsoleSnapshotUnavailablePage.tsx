import { bi } from '@/lib/i18n/bilingual';
import { PageHeader, PageShell } from '@/ui/primitives';
import type { ConsoleApiState } from '@/ui/useConsoleSnapshot';

export interface ConsoleSnapshotUnavailablePageProps {
  endpoint: `/api/v1/console/${string}`;
  apiState: ConsoleApiState;
  error: string | null;
}

/** A fail-closed production page: no fixture tree is mounted underneath it. */
export function ConsoleSnapshotUnavailablePage({
  endpoint,
  apiState,
  error,
}: ConsoleSnapshotUnavailablePageProps) {
  const loading = apiState === 'loading';
  return (
    <PageShell>
      <PageHeader
        title="Backend Snapshot"
        titleZh={loading ? '正在读取后端快照' : '后端快照不可用'}
        subtitle={bi(
          '生产控制台只显示通过契约验证的真实后端响应；传输失败时不会渲染前端 fixture。',
          'The production console renders only contract-verified backend responses; frontend fixtures are not rendered after a transport failure.',
        )}
      />
      <section
        className={`rounded-xl border p-5 ${loading
          ? 'border-info/35 bg-info/10 text-info'
          : 'border-neg/35 bg-neg/10 text-neg'}`}
        role={loading ? 'status' : 'alert'}
        data-testid="console-snapshot-unavailable"
      >
        <p className="font-mono text-xs font-semibold">
          {loading ? 'BACKEND_SNAPSHOT_LOADING' : 'BACKEND_SNAPSHOT_UNAVAILABLE'}
        </p>
        <p className="mt-2 break-all font-mono text-[10px] text-muted">GET {endpoint}</p>
        <p className="mt-2 break-all font-mono text-[10px] text-muted">
          {error ?? apiState}
        </p>
        <p className="mt-3 font-mono text-[10px] font-semibold text-warn">
          PRODUCTION_FIXTURES_DISABLED
        </p>
      </section>
    </PageShell>
  );
}
