import type {BinanceDemoPayload} from '@/lib/types/binance-demo';

export interface DemoAccountPosition {
  observedAt: number;
  flat: boolean;
  quantityLabel: string;
}

/** A current venue observation is separate from the cycle's ledger result. */
export function currentDemoPosition(data: BinanceDemoPayload | null, refreshFailed: boolean): DemoAccountPosition | null {
  const account=data?.account;
  const at=account?.observed_at_ms;
  if (!data || refreshFailed || data.snapshot_status!=='REAL' || account?.status!=='READY' || at==null ||
      !Number.isSafeInteger(at) || at<=0 || at>data.as_of_ms || data.as_of_ms-at>120000 ||
      data.functional_cycle.report && at<=data.functional_cycle.report.observed_at_ms) return null;
  const rows=account.positions.filter(p=>p.symbol==='BTCUSDC');
  if (new Set(rows.map(p=>p.position_side)).size!==rows.length ||
      rows.some(p=>!/^-?\d{1,30}(\.\d{1,24})?$/.test(p.quantity))) return null;
  const nonzero=rows.filter(p=>! /^-?0+(\.0+)?$/.test(p.quantity));
  // Never net a long and a short to zero, or round a small residual away.
  return {observedAt:at,flat:nonzero.length===0,quantityLabel:nonzero.length===0?'0 BTC':nonzero.map(p=>
    `${p.position_side==='LONG'?'多仓 ':p.position_side==='SHORT'?'空仓腿 ':''}${p.quantity} BTC`).join('；')};
}
