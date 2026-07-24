import React, { createContext, useCallback, useContext, useRef, useState } from 'react';
import { TONE_VAR } from './format';

/**
 * Shared toast/notification stack — replaces the ad hoc `setMsg`/`setTrigMsg`/
 * `setLastAction` inline-text pattern repeated across pages. Auto-dismisses,
 * stacks multiple, and is anchored to the viewport so it survives scrolling
 * away from the panel that triggered it.
 */

type ToastTone = 'pos' | 'neg' | 'info';
interface ToastItem { id: number; tone: ToastTone; text: React.ReactNode; }

interface ToastApi {
  success: (text: React.ReactNode) => void;
  error: (text: React.ReactNode) => void;
  info: (text: React.ReactNode) => void;
}

const Ctx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}

const TONE_ICON: Record<ToastTone, string> = { pos: '✓', neg: '✕', info: 'ⓘ' };
const DURATION_MS = 5000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);

  const push = useCallback((tone: ToastTone, text: React.ReactNode) => {
    const id = ++counter.current;
    setItems(prev => [...prev, { id, tone, text }]);
    setTimeout(() => setItems(prev => prev.filter(i => i.id !== id)), DURATION_MS);
  }, []);

  const api: ToastApi = {
    success: text => push('pos', text),
    error: text => push('neg', text),
    info: text => push('info', text),
  };

  function dismiss(id: number) {
    setItems(prev => prev.filter(i => i.id !== id));
  }

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2" aria-live="polite">
        {items.map(item => {
          const tone = TONE_VAR[item.tone];
          return (
            <div key={item.id} className="q-rise pointer-events-auto flex max-w-sm items-start gap-2 rounded-lg border px-3.5 py-2.5 text-xs shadow-panel"
              style={{
                background: 'var(--panel-2)',
                borderColor: `color-mix(in srgb, ${tone} 40%, transparent)`,
                boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${tone} 25%, transparent), var(--shadow-panel)`,
              }}
              role="status">
              <span className="mt-0.5" style={{ color: tone }} aria-hidden="true">{TONE_ICON[item.tone]}</span>
              <span className="min-w-0 flex-1 text-ink">{item.text}</span>
              <button onClick={() => dismiss(item.id)} aria-label="dismiss"
                className="shrink-0 text-faint hover:text-ink">×</button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}
