import { useEffect, useRef, useState } from 'react';

const DRAWERS = [
  { id: 'governance', label: '治理', description: '治理规则与安全边界的只读入口。' },
  { id: 'audit', label: '审计', description: '事件链与证据账本的只读入口。' },
  { id: 'health', label: '健康', description: '运行健康与数据新鲜度的只读入口。' },
  { id: 'tasks', label: '任务', description: '调度任务与恢复状态的只读入口。' },
] as const;

type Drawer = typeof DRAWERS[number];

export function GlobalDrawerHost() {
  const [active, setActive] = useState<Drawer | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const close = () => {
    setActive(null);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [active]);

  return (
    <>
      <div className="flex shrink-0 items-center gap-1" aria-label="global drawers">
        {DRAWERS.map(drawer => (
          <button
            key={drawer.id}
            type="button"
            aria-haspopup="dialog"
            onClick={event => {
              triggerRef.current = event.currentTarget;
              setActive(drawer);
            }}
            className="rounded border border-line bg-inset/65 px-2 py-1.5 text-[11px] font-medium text-muted hover:border-line-strong hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            {drawer.label}
          </button>
        ))}
      </div>
      {active && (
        <div
          className="fixed inset-0 z-[90] flex justify-end bg-black/45"
          onMouseDown={event => { if (event.target === event.currentTarget) close(); }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`${active.label}抽屉`}
            className="h-full w-full max-w-md border-l border-line bg-panel p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-3xs font-semibold uppercase tracking-[0.16em] text-faint">GLOBAL DRAWER</p>
                <h2 className="mt-1 text-xl font-semibold text-ink">{active.label}</h2>
              </div>
              <button
                type="button"
                autoFocus
                aria-label={`关闭${active.label}抽屉`}
                onClick={close}
                className="rounded border border-line px-2.5 py-1.5 text-xs text-muted hover:bg-inset hover:text-ink"
              >
                关闭
              </button>
            </div>
            <p className="mt-6 text-sm leading-6 text-muted">{active.description}</p>
            <div className="mt-4 rounded-lg border border-warn/35 bg-warn/10 p-4 text-sm text-warn">
              <strong className="block">UNAVAILABLE</strong>
              <span className="mt-1 block">Task 48 接入真实权威数据；当前只读，绝不使用合成回退。</span>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
