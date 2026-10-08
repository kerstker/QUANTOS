import React, { createContext, useCallback, useContext, useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';

/**
 * In-app confirmation dialog — replaces native window.confirm for
 * order-adjacent and destructive actions. Promise-based: `await confirm({...})`
 * resolves true/false, mirroring window.confirm's ergonomics without the jarring
 * browser chrome, and supports an exact "type X to confirm" step for high-risk actions.
 */

export type ConfirmSeverity = 'info' | 'warn' | 'danger';

export interface ConfirmOptions {
  title: React.ReactNode;
  message?: React.ReactNode;
  severity?: ConfirmSeverity;
  confirmLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  /** If set, the confirm button stays disabled until the input exactly matches this string. */
  typeToConfirm?: string;
}

type PendingItem = ConfirmOptions & { resolve: (ok: boolean) => void };

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;

const ConfirmCtx = createContext<ConfirmFn | null>(null);

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmCtx);
  if (!ctx) throw new Error('useConfirm must be used within ConfirmDialogHost');
  return ctx;
}

const SEV_TONE: Record<ConfirmSeverity, string> = {
  info: 'var(--info)', warn: 'var(--warn)', danger: 'var(--neg)',
};
const SEV_ICON: Record<ConfirmSeverity, string> = { info: 'ⓘ', warn: '⚠', danger: '⛔' };

export function ConfirmDialogHost({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<PendingItem | null>(null);
  const [typed, setTyped] = useState('');

  const confirm = useCallback<ConfirmFn>(opts => {
    return new Promise<boolean>(resolve => {
      setTyped('');
      setPending({ ...opts, resolve });
    });
  }, []);

  function closeConfirm(ok: boolean) {
    pending?.resolve(ok);
    setPending(null);
  }

  const severity = pending?.severity ?? 'info';
  const tone = SEV_TONE[severity];
  const needsType = pending?.typeToConfirm != null;
  const canConfirm = !needsType || typed === pending?.typeToConfirm;

  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      {pending && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          role="dialog" aria-modal="true" aria-label={typeof pending.title === 'string' ? pending.title : undefined}
          onKeyDown={e => { if (e.key === 'Escape') closeConfirm(false); }}
          onClick={e => { if (e.target === e.currentTarget) closeConfirm(false); }}>
          <div className="q-rise w-full max-w-md rounded-panel border border-line bg-panel-2 p-5"
            style={{ boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${tone} 32%, transparent), var(--shadow-lift)` }}>
            <div className="mb-2 flex items-start gap-2.5">
              <span className="mt-0.5 text-base" style={{ color: tone }} aria-hidden="true">{SEV_ICON[severity]}</span>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold text-ink">{pending.title}</div>
                {pending.message && <div className="mt-1 text-[13px] leading-relaxed text-muted">{pending.message}</div>}
              </div>
            </div>
            {needsType && (
              <div className="mb-1 mt-3">
                <label className="mb-1 block text-2xs text-faint">
                  {bi(`输入 "${pending.typeToConfirm}" 以确认`, `Type "${pending.typeToConfirm}" to confirm`)}
                </label>
                <input autoFocus value={typed} onChange={e => setTyped(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && canConfirm) closeConfirm(true); }}
                  className="mono w-full rounded-md border border-line bg-inset/50 px-2.5 py-1.5 text-xs text-ink" />
              </div>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => closeConfirm(false)}
                className="rounded-md border border-line px-3 py-1.5 text-xs text-muted transition hover:text-ink">
                {pending.cancelLabel ?? bi('取消', 'Cancel')}
              </button>
              <button onClick={() => closeConfirm(true)} disabled={!canConfirm}
                className="rounded-md border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40"
                style={{
                  borderColor: `color-mix(in srgb, ${tone} 55%, transparent)`,
                  color: tone,
                  background: `color-mix(in srgb, ${tone} 12%, transparent)`,
                }}>
                {pending.confirmLabel ?? bi('确认', 'Confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}
