import React from 'react';
import { statusLabel } from '@/lib/i18n/bilingual';

const badgeBase = "inline-flex max-w-full items-center rounded px-2 py-0.5 text-left text-xs font-medium leading-tight";
const decisionColors: Record<string,string> = {
  APPROVED:"bg-emerald-900/50 text-emerald-300 border border-emerald-700",
  APPROVED_WITH_LIMITS:"bg-amber-900/50 text-amber-300 border border-amber-700",
  PENDING:"bg-amber-900/50 text-amber-300 border border-amber-700",
  BLOCKED:"bg-red-900/50 text-red-300 border border-red-700",
  NO_GO:"bg-red-900/50 text-red-300 border border-red-700",
  NOT_AUTHORIZED:"bg-red-900/50 text-red-300 border border-red-700",
  BACKEND_COMPUTED:"bg-emerald-900/50 text-emerald-300 border border-emerald-700",
  API_CONNECTED:"bg-emerald-900/50 text-emerald-300 border border-emerald-700",
  API_LOADING:"bg-cyan-900/50 text-cyan-300 border border-cyan-700",
  API_ERROR:"bg-red-900/50 text-red-300 border border-red-700",
  MOCK_MODE:"bg-amber-900/50 text-amber-300 border border-amber-700",
  NOT_CONNECTED:"bg-slate-800 text-slate-300 border border-slate-600",
  READ_ONLY:"bg-cyan-900/50 text-cyan-300 border border-cyan-700",
  L2_DRY_RUN_ONLY:"bg-cyan-900/50 text-cyan-300 border border-cyan-700",
  L2_5_RESEARCH_CONTROL_PLANE:"bg-cyan-900/50 text-cyan-300 border border-cyan-700",
  L3_BROKER_TEST_ENV_READY:"bg-slate-800 text-slate-300 border border-slate-600",
  LIVE_TRADING_DISALLOWED:"bg-red-900/50 text-red-300 border border-red-700",
  TOP_TIER_AI_QUANT_TRADING_SYSTEM:"bg-slate-800 text-slate-300 border border-slate-600",
  REJECTED_OVERFIT:"bg-red-900/50 text-red-300 border border-red-700",
  REJECTED_LEAKAGE:"bg-red-900/50 text-red-300 border border-red-700",
  REJECTED_LOW_CAPACITY:"bg-orange-900/50 text-orange-300 border border-orange-700",
  REJECTED_UNSTABLE:"bg-orange-900/50 text-orange-300 border border-orange-700",
  REJECTED_HIGH_CORRELATION:"bg-purple-900/50 text-purple-300 border border-purple-700",
  NEEDS_MORE_DATA:"bg-slate-700/50 text-slate-300 border border-slate-600",
  RISK_APPROVED:"bg-emerald-900/50 text-emerald-300 border border-emerald-700",
  RISK_APPROVED_WITH_LIMITS:"bg-amber-900/50 text-amber-300 border border-amber-700",
  REJECTED_MARKET_RISK:"bg-red-900/50 text-red-300 border border-red-700",
  REJECTED_DRAWDOWN_RISK:"bg-red-900/50 text-red-300 border border-red-700",
  REJECTED_STRESS_FAILURE:"bg-red-900/50 text-red-300 border border-red-700",
  REJECTED_CONCENTRATION_RISK:"bg-orange-900/50 text-orange-300 border border-orange-700",
  REJECTED_CAPACITY_RISK:"bg-orange-900/50 text-orange-300 border border-orange-700",
  REJECTED_LIQUIDITY_RISK:"bg-blue-900/50 text-blue-300 border border-blue-700",
  REJECTED_CORRELATION_RISK:"bg-purple-900/50 text-purple-300 border border-purple-700",
  REJECTED_REGIME_RISK:"bg-violet-900/50 text-violet-300 border border-violet-700",
  REJECTED_MODEL_ALPHA_RISK:"bg-fuchsia-900/50 text-fuchsia-300 border border-fuchsia-700",
  PAPER_PASSED:"bg-emerald-900/50 text-emerald-300 border border-emerald-700",
  PAPER_FAILED:"bg-red-900/50 text-red-300 border border-red-700",
  SIMULATION_PASSED:"bg-emerald-900/50 text-emerald-300 border border-emerald-700",
  SIMULATION_FAILED:"bg-red-900/50 text-red-300 border border-red-700",
};
const stateColors: Record<string,string> = {
  RUNNING:"bg-cyan-900/50 text-cyan-300",IDLE:"bg-slate-700/50 text-slate-300",
  COMPLETED:"bg-emerald-900/50 text-emerald-300",FAILED:"bg-red-900/50 text-red-300",
  PAUSED:"bg-amber-900/50 text-amber-300",REFLECTING:"bg-purple-900/50 text-purple-300",
  ACTIVE:"bg-emerald-900/50 text-emerald-300",VALIDATING:"bg-cyan-900/50 text-cyan-300",
  DECAYING:"bg-amber-900/50 text-amber-300",RETIRED:"bg-slate-700/50 text-slate-300",
  READY:"bg-emerald-900/50 text-emerald-300",PAPER_PASSED:"bg-emerald-900/50 text-emerald-300",
  PAPER_FAILED:"bg-red-900/50 text-red-300",SIMULATION_PASSED:"bg-emerald-900/50 text-emerald-300",
  SIMULATION_FAILED:"bg-red-900/50 text-red-300",
  healthy:"bg-emerald-900/50 text-emerald-300",degraded:"bg-amber-900/50 text-amber-300",
  down:"bg-red-900/50 text-red-300",
};

export function DecisionBadge({decision}:{decision:string}){
  return <span className={`${badgeBase} ${decisionColors[decision]||"bg-slate-800 text-slate-300"}`}>{statusLabel(decision)}</span>;
}
export function StatusBadge({status}:{status:string}){
  return <span className={`${badgeBase} ${stateColors[status]||"bg-slate-800 text-slate-300"}`}>{statusLabel(status)}</span>;
}
export function RiskBadge({passed}:{passed:boolean}){
  return <span className={`${badgeBase} ${passed?"bg-emerald-900/50 text-emerald-300":"bg-red-900/50 text-red-300"}`}>{passed?"通过 / PASS":"失败 / FAIL"}</span>;
}
export function MetricCard({label,value,unit}:{label:string;value:string|number;unit?:string}){
  return <div className="min-w-0 bg-slate-900 border border-slate-800 rounded-lg p-3"><div className="text-slate-500 text-xs uppercase leading-tight">{label}</div><div className="mt-1 break-words text-lg font-bold leading-tight text-slate-100 md:text-xl">{typeof value==="number"?value.toLocaleString():value}{unit&&<span className="ml-1 text-sm text-slate-400">{unit}</span>}</div></div>;
}
