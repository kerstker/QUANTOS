import React, { createContext, useCallback, useContext, useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';

/**
 * In-app confirmation dialog — replaces native window.confirm/window.prompt for
 * order-adjacent and destructive actions. Promise-based: `await confirm({...})`
 * resolves true/false, mirroring window.confirm's ergonomics without the jarring
 * browser chrome, and supports a "type X to confirm" step for high-risk actions
 * (window.prompt equality-check pattern, but with visible state and no silent
 * typo failure).
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

/** Free-text capture (e.g. "your name for the audit trail") — the window.prompt equivalent. */
export interface PromptOptions {
  title: React.ReactNode;
  message?: React.ReactNode;
  label?: React.ReactNode;
  placeholder?: string;
  severity?: ConfirmSeverity;
  confirmLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  /** Defaults to true — confirm stays disabled until non-empty. */
  required?: boolean;
}

type PendingItem =
  | ({ kind: 'confirm' } & ConfirmOptions & { resolve: (ok: boolean) => void })
  | ({ kind: 'prompt' } & PromptOptions & { resolve: (value: string | null) => void });

type ConfirmFn = (opts: ConfirmOptions) => Promise<boolean>;
type PromptFn = (opts: PromptOptions) => Promise<string | null>;

const ConfirmCtx = createContext<ConfirmFn | null>(null);
const PromptCtx = createContext<PromptFn | null>(null);

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmCtx);
  if (!ctx) throw new Error('useConfirm must be used within ConfirmDialogHost');
  return ctx;
}

export function usePrompt(): PromptFn {
  const ctx = useContext(PromptCtx);
  if (!ctx) throw new Error('usePrompt must be used within ConfirmDialogHost');
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
      setPending({ kind: 'confirm', ...opts, resolve });
    });
  }, []);

  const prompt = useCallback<PromptFn>(opts => {
    return new Promise<string | null>(resolve => {
      setTyped('');
      setPending({ kind: 'prompt', ...opts, resolve });
    });
  }, []);

  function closeConfirm(ok: boolean) {
    if (pending?.kind === 'confirm') pending.resolve(ok);
    setPending(null);
  }

  function closePrompt(value: string | null) {
    if (pending?.kind === 'prompt') pending.resolve(value);
    setPending(null);
  }

  const severity = pending?.severity ?? 'info';
  const tone = SEV_TONE[severity];
  const isPrompt = pending?.kind === 'prompt';
  const needsType = pending?.kind === 'confirm' && pending.typeToConfirm != null;
  const promptRequired = pending?.kind === 'prompt' && pending.required !== false;
  const canConfirm = isPrompt
    ? (!promptRequired || typed.trim().length > 0)
    : (!needsType || typed === (pending as { typeToConfirm?: string } | null)?.typeToConfirm);

  function handleConfirmClick() {
    if (isPrompt) closePrompt(canConfirm ? typed.trim() : null);
    else closeConfirm(true);
  }
  function handleCancelClick() {
    if (isPrompt) closePrompt(null);
    else closeConfirm(false);
  }

  return (
    <ConfirmCtx.Provider value={confirm}>
      <PromptCtx.Provider value={prompt}>
        {children}
        {pending && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            role="dialog" aria-modal="true" aria-label={typeof pending.title === 'string' ? pending.title : undefined}
            onKeyDown={e => { if (e.key === 'Escape') handleCancelClick(); }}
            onClick={e => { if (e.target === e.currentTarget) handleCancelClick(); }}>
            <div className="q-rise w-full max-w-md rounded-panel border border-line bg-panel-2 p-5"
              style={{ boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${tone} 32%, transparent), var(--shadow-lift)` }}>
              <div className="mb-2 flex items-start gap-2.5">
                <span className="mt-0.5 text-base" style={{ color: tone }} aria-hidden="true">{SEV_ICON[severity]}</span>
                <div className="min-w-0 flex-1">
                  <div className="text-[15px] font-semibold text-ink">{pending.title}</div>
                  {pending.message && <div className="mt-1 text-[13px] leading-relaxed text-muted">{pending.message}</div>}
                </div>
              </div>
              {needsType && pending.kind === 'confirm' && (
                <div className="mb-1 mt-3">
                  <label className="mb-1 block text-2xs text-faint">
                    {bi(`输入 "${pending.typeToConfirm}" 以确认`, `Type "${pending.typeToConfirm}" to confirm`)}
                  </label>
                  <input autoFocus value={typed} onChange={e => setTyped(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && canConfirm) closeConfirm(true); }}
                    className="mono w-full rounded-md border border-line bg-inset/50 px-2.5 py-1.5 text-xs text-ink" />
                </div>
              )}
              {pending.kind === 'prompt' && (
                <div className="mb-1 mt-3">
                  {pending.label && <label className="mb-1 block text-2xs text-faint">{pending.label}</label>}
                  <input autoFocus value={typed} onChange={e => setTyped(e.target.value)} placeholder={pending.placeholder}
                    onKeyDown={e => { if (e.key === 'Enter' && canConfirm) closePrompt(typed.trim()); }}
                    className="w-full rounded-md border border-line bg-inset/50 px-2.5 py-1.5 text-xs text-ink" />
                </div>
              )}
              <div className="mt-4 flex justify-end gap-2">
                <button onClick={handleCancelClick}
                  className="rounded-md border border-line px-3 py-1.5 text-xs text-muted transition hover:text-ink">
                  {pending.cancelLabel ?? bi('取消', 'Cancel')}
                </button>
                <button onClick={handleConfirmClick} disabled={!canConfirm}
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
      </PromptCtx.Provider>
    </ConfirmCtx.Provider>
  );
}
