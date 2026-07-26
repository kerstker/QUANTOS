import { Fragment, useMemo, useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, SectionHeader, LoadingVeil,
  DataTable, Chip, KeyValue, KpiStrip, StatTile, type Column,
} from '@/ui/primitives';
import { AsOfStamp, EndpointFootnote, StateView, StatusBanner } from '@/ui/workbench';
import { compact, fixed, num, pct, stamp, statusTone } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchAlphaLifecycle } from '@/lib/api/research-console-api';
import type {
  AlphaLifecycleBacktestStage,
  AlphaLifecycleCapacityStage,
  AlphaLifecycleCrisis,
  AlphaLifecycleDecayStage,
  AlphaLifecycleDiscoveryHorizon,
  AlphaLifecycleDiscoveryStage,
  AlphaLifecycleEntry,
  AlphaLifecycleFalsificationStage,
  AlphaLifecycleLiveRun,
  AlphaLifecycleLiveStage,
  AlphaLifecyclePaperStage,
  AlphaLifecyclePayload,
  AlphaLifecyclePortfolioStage,
  AlphaLifecycleRetirementStage,
  AlphaLifecycleStressStage,
} from '@/lib/types/research';

/* ============================================================================
   AlphaLifecycle — Alpha 生命周期驾驶舱(2026-07-24 重构)。
   每条 alpha 画两样东西:① 五档 derived_stage 进度轨(CANDIDATE→…→LIVE_TRACK,
   机械推导非晋级背书);② 十段生命周期证据格(发掘→证伪→压力→回测→容量→组合→
   纸面→实盘→衰弱→退役),PRESENT 格内联 1–2 个关键数字,ABSENT 格诚实标「缺证据」。
   数据 = GET /api/v1/console/alpha-lifecycle(REAL_ALPHA_LIFECYCLE,
   tools/alpha_lifecycle_cockpit.py 对既有证据仓的只读聚合,无新研究数学);
   artifact 缺席 → 后端诚实 NO_SNAPSHOT,本页空态指引人工跑驱动器,绝不编造。
   ========================================================================== */

const STAGE_ORDER = [
  'discovery', 'falsification', 'stress', 'backtest', 'capacity',
  'portfolio', 'paper', 'live', 'decay', 'retirement',
] as const;
type StageKey = (typeof STAGE_ORDER)[number];

const STAGE_ZH: Record<StageKey, string> = {
  discovery: '发掘', falsification: '证伪', stress: '压力', backtest: '回测', capacity: '容量',
  portfolio: '组合', paper: '纸面', live: '实盘', decay: '衰弱', retirement: '退役',
};
const STAGE_EN: Record<StageKey, string> = {
  discovery: 'DISCOVERY', falsification: 'FALSIFICATION', stress: 'STRESS', backtest: 'BACKTEST',
  capacity: 'CAPACITY', portfolio: 'PORTFOLIO', paper: 'PAPER', live: 'LIVE',
  decay: 'DECAY', retirement: 'RETIREMENT',
};

/** derived_stage 阶梯(升序,与后端 stage_legend 一致)。 */
const DERIVED_LADDER = ['CANDIDATE', 'VALIDATED', 'DRY_RUN', 'PAPER_TRACKED', 'LIVE_TRACK'] as const;
const DERIVED_TONE: Record<string, 'pos' | 'info' | 'accent' | 'warn' | 'muted'> = {
  LIVE_TRACK: 'pos', PAPER_TRACKED: 'info', DRY_RUN: 'accent', VALIDATED: 'warn', CANDIDATE: 'muted',
};

/** 部署 book 的四条策略(干跑/实盘轨道);其余 alpha 全部归入「研究候选」表。 */
const FOCUS_IDS = ['crowd_contra', 'carry_oi_majors', 'top_contra', 'btc_spot_trend'] as const;

/* ---------- helpers -------------------------------------------------------- */

function derivedRank(stage: string): number {
  return (DERIVED_LADDER as readonly string[]).indexOf(stage);
}

function nPresent(e: AlphaLifecycleEntry): number {
  return STAGE_ORDER.filter(k => e.stages[k]?.status === 'PRESENT').length;
}

/** 前向证据注意力:paper-cycle blockers 非空、或前向期数 < 要求期数 → 琥珀 chip。
    forward 为 null 的研究候选不在 paper-cycle 登记内,不误报。 */
function forwardAttention(e: AlphaLifecycleEntry): string[] {
  const p = e.stages.paper;
  if (!p || p.status !== 'PRESENT' || !p.forward) return [];
  const out: string[] = [...(p.forward.blockers ?? [])];
  const req = p.required_forward_periods;
  const n = p.forward.n_forward_periods ?? 0;
  if (req != null && n < req) out.push(bi(`前向 ${n}/${req} 期`, `forward ${n}/${req}`));
  return out;
}

function resid(x: number | null | undefined): string {
  if (x == null || Number.isNaN(x)) return '—';
  return Math.abs(x) < 1e-4 ? x.toExponential(1) : fixed(x, 6);
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null;
}

/** 从自由结构证据里挑可读的标量(数字/布尔/短字符串)做 KeyValue 摘要。 */
function scalarItems(rec: Record<string, unknown> | null | undefined, max = 8): Array<{ k: string; v: string }> {
  const out: Array<{ k: string; v: string }> = [];
  for (const [k, v] of Object.entries(rec ?? {})) {
    if (out.length >= max) break;
    if (typeof v === 'number') out.push({ k, v: Number.isInteger(v) ? String(v) : v.toFixed(4) });
    else if (typeof v === 'boolean') out.push({ k, v: v ? '✓' : '✗' });
    else if (typeof v === 'string' && v.length <= 48) out.push({ k, v });
  }
  return out;
}

/** 每格内联的 1–2 个关键数字(仅 PRESENT;返回 null 由格子回退「—」)。 */
function headline(e: AlphaLifecycleEntry, k: StageKey): string | null {
  switch (k) {
    case 'discovery': {
      const s = e.stages.discovery;
      if (s?.status !== 'PRESENT') return null;
      const bits = [`IC ${fixed(s.mean_ic, 3)}`];
      if (s.t_stat != null) bits.push(`t ${fixed(s.t_stat, 2)}`);
      return bits.join(' · ');
    }
    case 'falsification': {
      const s = e.stages.falsification;
      if (s?.status !== 'PRESENT') return null;
      if (s.n_checks_pass != null && s.n_checks_total != null) {
        return `${s.n_checks_pass}/${s.n_checks_total} ${s.verdict_pass ? '✓' : '✗'}`;
      }
      if (s.verdict_pass != null) return s.verdict_pass ? 'PASS ✓' : 'FAIL ✗';
      return s.research_verdict ?? null;
    }
    case 'stress': {
      const s = e.stages.stress;
      if (s?.status !== 'PRESENT') return null;
      return `VaR95 ${pct(s.var_cvar_95?.var, 1)} · DD ${pct(s.worst_drawdown_window?.drawdown, 1)}`;
    }
    case 'backtest': {
      const s = e.stages.backtest;
      if (s?.status !== 'PRESENT') return null;
      const vt = s.sharpe_vol_target != null;
      const sr = s.sharpe_vol_target ?? s.sharpe_raw;
      return `${vt ? 'VT' : 'RAW'} ${fixed(sr, 2)} · DD ${pct(s.max_drawdown_vol_target ?? s.max_drawdown_raw, 1)}`;
    }
    case 'capacity': {
      const s = e.stages.capacity;
      if (s?.status !== 'PRESENT') return null;
      return `$${compact(s.participation_cap_aum)} · ${s.binding ?? '—'}`;
    }
    case 'portfolio': {
      const s = e.stages.portfolio;
      if (s?.status !== 'PRESENT') return null;
      const sr = s.optimizer_combo?.combo_econ_sharpe ?? s.return_stream_combo?.combo_inverse_vol_sharpe;
      return sr != null ? `组合 SR ${fixed(sr, 2)}` : null;
    }
    case 'paper': {
      const s = e.stages.paper;
      if (s?.status !== 'PRESENT') return null;
      const t = s.track;
      if (t) {
        const bits: string[] = [];
        if (t.paper_ic != null) bits.push(`IC ${fixed(t.paper_ic, 3)}`);
        if (t.paper_sharpe != null) bits.push(`SR ${fixed(t.paper_sharpe, 2)}`);
        bits.push(`${s.n_periods ?? t.n_periods ?? 0}期`);
        return bits.join(' · ');
      }
      if (s.forward) return `前向 ${s.forward.n_forward_periods ?? 0}/${s.required_forward_periods ?? '?'}`;
      return `${s.n_periods ?? 0}期`;
    }
    case 'live': {
      const s = e.stages.live;
      if (s?.status !== 'PRESENT') return null;
      const runs = s.runs ?? [];
      const subs = runs.reduce((n, r) => n + (r.n_submitted ?? 0), 0);
      return `${runs.length} 场 · ${subs} 单`;
    }
    case 'decay': {
      const s = e.stages.decay;
      if (s?.status !== 'PRESENT') return null;
      if (s.n_actions) return `${s.n_actions} 动作`;
      return s.degraded ? 'DEGRADED' : bi('无降级', 'no decay');
    }
    case 'retirement': {
      const s = e.stages.retirement;
      if (s?.status !== 'PRESENT') return null;
      return s.retired ? bi('已退役', 'RETIRED') : `${s.n_retirement_events ?? 0} ${bi('事件', 'events')}`;
    }
    default:
      return null;
  }
}

/* ---------- mock fixture(mock mode 专用;数值=真实 artifact 2026-07-24 快照摘录) -- */

const mockDecay: AlphaLifecycleDecayStage = {
  status: 'PRESENT', source: 'data\\cache\\alpha_maintenance.db',
  sweep_generated_at: 1784509501642, actor_kind: 'AGENT', n_actions: 0, alpha_action: null, degraded: false,
};
const mockRetirement: AlphaLifecycleRetirementStage = {
  status: 'PRESENT', source: 'data\\cache\\alpha_maintenance.db',
  retired: false, n_retirement_events: 0, events: [],
};

const mockAlphas: AlphaLifecycleEntry[] = [
  {
    alpha_id: 'crowd_contra', display_name: 'crowd_contra',
    derived_stage: 'LIVE_TRACK', lifecycle_state: null,
    warnings: [
      '前向证据不足:1/8 期 —— paper-cycle 判 BLOCKED',
      'paper-cycle blockers: DATA_ADMISSION_BLOCK, EVENT_BUS_NOT_HEALTHY, FORWARD_EVIDENCE_INSUFFICIENT',
      'capacity 为未校准原则性区间带(诊断,非门禁)',
    ],
    stages: {
      discovery: {
        status: 'PRESENT', source: 'data\\cache\\ic_panel_snapshot.json',
        factor: 'crowd_contra', universe: 'crypto_oi_majors',
        mean_ic: 0.0047, icir: 0.0191, t_stat: 0.776, hit_rate: 0.5033, n_periods: 1643, n_instruments: 24,
        by_holding_period: {
          '1': { mean_ic: 0.0047, icir: 0.0191, t_stat: 0.776, hit_rate: 0.5033, n_periods: 1643 },
          '7': { mean_ic: 0.0463, icir: 0.1942, t_stat: 2.9644, hit_rate: 0.5579, n_periods: 233 },
          '14': { mean_ic: 0.0728, icir: 0.3044, t_stat: 3.2787, hit_rate: 0.6121, n_periods: 116 },
        },
      },
      falsification: {
        status: 'PRESENT', sources: ['data\\cache\\crypto_oi_verify.json', 'evidence\\alpha_paper_cycle.json'],
        checks: {
          survives_1d_lag: true, survives_10bps_cost: true, broad_across_years: true,
          survives_subsets: true, adds_to_carry: true,
        },
        n_checks_pass: 5, n_checks_total: 5, verdict_pass: true, research_verdict: 'PASS_WITH_WARNINGS',
        research_evidence: {
          refutation_pass_count: 5, net_sharpe_10bps: 0.687,
          one_day_lag_sharpe: 1.312, embargoed_oos_mean_sharpe: 1.1269,
        },
        research_warnings: ['survivorship and recent-decay risk remain open'],
        verify_summary: {
          verdict: 'SURVIVES 5/5 refutation checks',
          net_sharpe_by_fee_bps: { '2': 1.096, '5': 0.943, '10': 0.687, '20': 0.175 },
          embargoed_oos: { n_folds: 10, mean_is: 1.5631, mean_oos: 1.1269, haircut: -0.4362 },
        },
      },
      stress: {
        status: 'PRESENT', source: 'data\\cache\\scenario_tail_snapshot.json',
        book: 'crowd_contra', provenance: 'REAL_HISTORICAL_REPLAY', n_periods: 233,
        var_cvar_95: { var: 0.0293, cvar: 0.0414, alpha: 0.95 },
        var_cvar_99: { var: 0.0501, cvar: 0.0537, alpha: 0.99 },
        hill_tail_index: 2.6748, lower_tail_dep_vs_btc: 0.0435,
        worst_drawdown_window: {
          drawdown: 0.1382, peak_to_trough_periods: 19,
          peak_date: '2023-10-19', trough_date: '2024-02-29',
        },
        crises: {
          '2022-05 LUNA': { n_periods: 3, total_return: 0.085, max_drawdown: 0.0233, worst_period: -0.0233 },
          '2022-11 FTX': { n_periods: 2, total_return: 0.0529, max_drawdown: 0.0, worst_period: 0.0124 },
        },
      },
      backtest: {
        status: 'PRESENT', source: 'data\\cache\\dry_run.db',
        strategy_id: 'crowd_contra', run_id: 'af119aff537f7092', timestamp: 1784160000000,
        canonical: true, n_runs_for_strategy: 2, mode: 'multi_period', cadence: 'weekly',
        n_periods: 235, window_start_ts: 1642636800000, window_end_ts: 1784160000000,
        total_return: 1.21154, sharpe_raw: 1.0697, sharpe_vol_target: 1.095,
        max_drawdown_raw: 0.0885, max_drawdown_vol_target: 0.0971,
        reconciliation_residual: 2e-9, n_intents: 5155, n_approved: 5155, n_rejected: 0,
        research_risk_decision: 'RISK_APPROVED', cost_model: 'FLAT_BPS', cost_bps: 10.0,
        crowding_gate: { n_pass: 0, n_warn: 214, n_block: 21, worst_score: 0.5 },
      },
      capacity: {
        status: 'PRESENT', source: 'data\\cache\\capacity_snapshot.json',
        book: 'crowd_contra', provenance: 'UNCALIBRATED_PRINCIPLED',
        participation_cap_aum: 7164102.96, binding: 'participation',
        calibrated_range: { low: 3582051.48, high: 71641029.64 },
        gross_sharpe: 1.566, n_periods: 234, data_quality: 'OK',
      },
      portfolio: {
        status: 'PRESENT',
        return_stream_combo: {
          kind: 'RETURN_STREAM_COMBINE_GROSS_RELATIVE',
          standalone_sharpe: 1.297, standalone_max_drawdown: 0.148, inverse_vol_weight: 0.44,
          combo_equal_weight_sharpe: 1.949, combo_inverse_vol_sharpe: 1.977,
          combo_inverse_vol_max_drawdown: 0.0799, corr_carry_crowd: -0.038, common_weeks: 231,
        },
        optimizer_combo: {
          kind: 'OPTIMIZER_COMBO', weight: 0.3511, alone_econ_sharpe: 1.277, alone_max_drawdown: 0.168,
          combo_econ_sharpe: 1.787, combo_max_drawdown: 0.087, lift_vs_best_alone: 0.51,
          verdict: 'combo SR 1.787 vs best-alone 1.277 (lift +0.510); diversification helps',
        },
      },
      paper: {
        status: 'PRESENT', source: 'data\\cache\\paper_track.db',
        paper_id: 'crowd_contra', n_periods: 1,
        track: {
          start_ts: 1783555200000, last_ts: 1783555200000, n_periods: 1,
          cum_return: 0.000955, paper_sharpe: 0.0, paper_ic: 0.3451,
          backtest_sharpe: 3.6602, perf_gap: 1.0,
        },
        forward: {
          n_forward_periods: 0, paper_sharpe: null, evidence_state: 'DATA_BLOCKED',
          paper_verdict: 'BLOCKED',
          blockers: ['DATA_ADMISSION_BLOCK', 'EVENT_BUS_NOT_HEALTHY', 'FORWARD_EVIDENCE_INSUFFICIENT'],
          pending_decisions: 1, attested_pending_decisions: 1, last_decision_ts: 1784592000000,
        },
        required_forward_periods: 8,
      },
      live: {
        status: 'PRESENT',
        runs: [{
          venue: 'binance', mode: 'submit', tier: 'AUTO', armed: null, hard_stop: false,
          n_submitted: 14, n_pending_review: 0, n_rejected_by_gate: 0, n_dropped_by_filters: 4,
          n_submission_errors: 0, order_stats: { FILLED: 29 },
          ran_at: 1783905000982, status_file: 'data\\cache\\live_track_status.json',
        }],
      },
      decay: mockDecay,
      retirement: mockRetirement,
    },
  },
  {
    alpha_id: 'carry_oi_majors', display_name: 'carry_oi_majors',
    derived_stage: 'PAPER_TRACKED', lifecycle_state: null,
    warnings: [
      '权威 dry-run 的 research-risk 决策为 RISK_REJECTED(非 RISK_APPROVED)',
      '前向证据不足:1/8 期 —— paper-cycle 判 BLOCKED',
      'paper-cycle blockers: DATA_ADMISSION_BLOCK, EVENT_BUS_NOT_HEALTHY, DRY_RUN_RESEARCH_RISK_NOT_PASS, FORWARD_EVIDENCE_INSUFFICIENT',
      'capacity 为未校准原则性区间带(诊断,非门禁)',
    ],
    stages: {
      discovery: {
        status: 'PRESENT', source: 'data\\cache\\ic_panel_snapshot.json',
        factor: 'carry_composite', universe: 'crypto_funding',
        mean_ic: 0.0005, icir: 0.0037, t_stat: 0.1757, hit_rate: 0.5016, n_periods: 2205, n_instruments: 115,
        by_holding_period: {
          '7': { mean_ic: 0.0229, icir: 0.1574, t_stat: 2.789, hit_rate: 0.5446, n_periods: 314 },
          '14': { mean_ic: 0.0299, icir: 0.2177, t_stat: 2.7281, hit_rate: 0.5987, n_periods: 157 },
        },
      },
      falsification: {
        status: 'PRESENT', sources: ['evidence\\alpha_paper_cycle.json'],
        checks: null, n_checks_pass: null, n_checks_total: null,
        verdict_pass: true, research_verdict: 'PASS_WITH_WARNINGS',
        research_evidence: { net_sharpe_5bps: 2.01, walkforward_verdict: 'DESIGN_ROBUST', walkforward_blocks: 5 },
        research_warnings: ['carry is an economic funding-yield edge; price-only alpha is weaker'],
        verify_summary: null,
      },
      stress: {
        status: 'PRESENT', source: 'data\\cache\\scenario_tail_snapshot.json',
        book: 'carry', provenance: 'REAL_HISTORICAL_REPLAY', n_periods: 313,
        crises: {
          '2021-05 crash': { n_periods: 3, total_return: 0.0242, max_drawdown: 0.0257, worst_period: -0.0257 },
        },
      },
      backtest: {
        status: 'PRESENT', source: 'data\\cache\\dry_run.db',
        strategy_id: 'carry', run_id: '90eb3789e575ff54', timestamp: 1784246400000,
        canonical: true, n_runs_for_strategy: 2, mode: 'multi_period', cadence: 'weekly',
        n_periods: 316, window_start_ts: 1593734400000, window_end_ts: 1784246400000,
        total_return: 1.501318, sharpe_raw: 0.4771, sharpe_vol_target: 0.7458,
        max_drawdown_raw: 0.7107, max_drawdown_vol_target: 0.2208,
        reconciliation_residual: 4e-9, n_intents: 16148, n_approved: 16041, n_rejected: 107,
        research_risk_decision: 'RISK_REJECTED', cost_model: 'IMPACT_AC', cost_bps: 8.5,
        crowding_gate: null,
      },
      capacity: {
        status: 'PRESENT', source: 'data\\cache\\capacity_snapshot.json',
        book: 'carry', provenance: 'UNCALIBRATED_PRINCIPLED',
        participation_cap_aum: 1953401.1, binding: 'participation',
        calibrated_range: { low: 976700.55, high: 19534010.95 },
        gross_sharpe: 1.672, n_periods: 314, data_quality: 'OK',
      },
      portfolio: {
        status: 'PRESENT',
        return_stream_combo: {
          kind: 'RETURN_STREAM_COMBINE_GROSS_RELATIVE',
          standalone_sharpe: 1.444, standalone_max_drawdown: 0.1642, inverse_vol_weight: 0.56,
          combo_equal_weight_sharpe: 1.949, combo_inverse_vol_sharpe: 1.977,
          combo_inverse_vol_max_drawdown: 0.0799, corr_carry_crowd: -0.038, common_weeks: 231,
        },
        optimizer_combo: {
          kind: 'OPTIMIZER_COMBO', weight: 0.2098, alone_econ_sharpe: 1.201, alone_max_drawdown: 0.271,
          combo_econ_sharpe: 1.787, combo_max_drawdown: 0.087, lift_vs_best_alone: 0.51,
          verdict: 'combo SR 1.787 vs best-alone 1.277 (lift +0.510); diversification helps',
        },
      },
      paper: {
        status: 'PRESENT', source: 'data\\cache\\paper_track.db',
        paper_id: 'carry_oi_majors', n_periods: 1,
        track: {
          start_ts: 1783555200000, last_ts: 1783555200000, n_periods: 1,
          cum_return: 0.009949, paper_sharpe: 0.0, paper_ic: 0.1824,
          backtest_sharpe: 3.1388, perf_gap: 1.0,
        },
        forward: {
          n_forward_periods: 0, paper_sharpe: null, evidence_state: 'DATA_BLOCKED',
          paper_verdict: 'BLOCKED',
          blockers: ['DATA_ADMISSION_BLOCK', 'EVENT_BUS_NOT_HEALTHY', 'DRY_RUN_RESEARCH_RISK_NOT_PASS', 'FORWARD_EVIDENCE_INSUFFICIENT'],
          pending_decisions: 1, attested_pending_decisions: 1, last_decision_ts: 1784592000000,
        },
        required_forward_periods: 8,
      },
      live: { status: 'ABSENT' },
      decay: mockDecay,
      retirement: mockRetirement,
    },
  },
  {
    alpha_id: 'top_contra', display_name: 'top_contra',
    derived_stage: 'DRY_RUN', lifecycle_state: null,
    warnings: [
      '证伪证据存在但未全过(4/5) —— VALIDATED 不成立,derived_stage 不含证伪背书',
      '前向证据不足:0/8 期 —— paper-cycle 判 BLOCKED',
      'paper-cycle blockers: DATA_ADMISSION_BLOCK, EVENT_BUS_NOT_HEALTHY, FORWARD_EVIDENCE_INSUFFICIENT',
      'capacity 为未校准原则性区间带(诊断,非门禁)',
    ],
    stages: {
      discovery: { status: 'ABSENT' },
      falsification: {
        status: 'PRESENT', sources: ['data\\cache\\crypto_oi_top_verify.json', 'evidence\\alpha_paper_cycle.json'],
        checks: {
          survives_1d_lag: true, survives_10bps_cost: true, broad_across_years: false,
          survives_subsets: true, adds_to_carry: true,
        },
        n_checks_pass: 4, n_checks_total: 5, verdict_pass: false, research_verdict: 'PASS_WITH_WARNINGS',
        research_evidence: {
          price_sharpe: 0.727, economic_sharpe: 0.879, ic: 0.0216,
          dedicated_refutation_pass_count: 4,
        },
        research_warnings: ['capacity remains an uncalibrated diagnostic, not a promotion gate'],
        verify_summary: { verdict: 'SURVIVES 4/5 refutation checks' },
      },
      stress: { status: 'ABSENT' },
      backtest: {
        status: 'PRESENT', source: 'data\\cache\\dry_run.db',
        strategy_id: 'top_contra', run_id: '993d3a28a95ea411', timestamp: 1784160000000,
        canonical: true, n_runs_for_strategy: 2, mode: 'multi_period', cadence: 'weekly',
        n_periods: 192, window_start_ts: 1654128000000, window_end_ts: 1784160000000,
        total_return: 1.650056, sharpe_raw: 1.5246, sharpe_vol_target: 1.3013,
        max_drawdown_raw: 0.2015, max_drawdown_vol_target: 0.2015,
        reconciliation_residual: 1e-9, n_intents: 4425, n_approved: 4425, n_rejected: 0,
        research_risk_decision: 'RISK_APPROVED', cost_model: 'FLAT_BPS', cost_bps: 10.0,
        crowding_gate: { n_pass: 0, n_warn: 181, n_block: 11, worst_score: 0.5 },
      },
      capacity: {
        status: 'PRESENT', source: 'data\\cache\\capacity_snapshot.json',
        book: 'top_contra', provenance: 'UNCALIBRATED_PRINCIPLED',
        participation_cap_aum: 7164102.96, binding: 'participation',
        calibrated_range: { low: 3582051.48, high: 71641029.64 },
        gross_sharpe: 0.985, n_periods: 191, data_quality: 'OK',
      },
      portfolio: {
        status: 'PRESENT',
        return_stream_combo: null,
        optimizer_combo: {
          kind: 'OPTIMIZER_COMBO', weight: 0.4391, alone_econ_sharpe: 1.132, alone_max_drawdown: 0.151,
          combo_econ_sharpe: 1.787, combo_max_drawdown: 0.087, lift_vs_best_alone: 0.51,
          verdict: 'combo SR 1.787 vs best-alone 1.277 (lift +0.510); diversification helps',
        },
      },
      paper: {
        status: 'PRESENT', source: 'data\\cache\\paper_track.db',
        paper_id: 'top_contra', n_periods: 0, track: null,
        forward: {
          n_forward_periods: 0, paper_sharpe: null, evidence_state: 'DATA_BLOCKED',
          paper_verdict: 'BLOCKED',
          blockers: ['DATA_ADMISSION_BLOCK', 'EVENT_BUS_NOT_HEALTHY', 'FORWARD_EVIDENCE_INSUFFICIENT'],
          pending_decisions: 1, attested_pending_decisions: 1, last_decision_ts: 1784592000000,
        },
        required_forward_periods: 8,
      },
      live: { status: 'ABSENT' },
      decay: mockDecay,
      retirement: mockRetirement,
    },
  },
  {
    alpha_id: 'btc_spot_trend', display_name: 'btc_spot_trend',
    derived_stage: 'DRY_RUN', lifecycle_state: null,
    warnings: [
      '无 falsification 证据却有后段证据 —— 阶段跳跃(未证伪先行),需人工确认',
      '权威 dry-run 的 research-risk 决策为 RISK_REJECTED(非 RISK_APPROVED)',
    ],
    stages: {
      discovery: { status: 'ABSENT' },
      falsification: { status: 'ABSENT' },
      stress: { status: 'ABSENT' },
      backtest: {
        status: 'PRESENT', source: 'data\\cache\\dry_run.db',
        strategy_id: 'btc_spot_trend', run_id: 'b73df9ca627cdf9c', timestamp: 1784160000000,
        canonical: false, n_runs_for_strategy: 1, mode: 'multi_period', cadence: 'weekly',
        n_periods: 318, window_start_ts: 1592438400000, window_end_ts: 1784160000000,
        total_return: 1.053452, sharpe_raw: 0.818, sharpe_vol_target: 0.9247,
        max_drawdown_raw: 0.0718, max_drawdown_vol_target: 0.0526,
        reconciliation_residual: 0.0, n_intents: 54, n_approved: 54, n_rejected: 0,
        research_risk_decision: 'RISK_REJECTED', cost_model: 'FLAT_BPS', cost_bps: 0.0,
        crowding_gate: null,
      },
      capacity: { status: 'ABSENT' },
      portfolio: { status: 'ABSENT' },
      paper: { status: 'ABSENT' },
      live: { status: 'ABSENT' },
      decay: mockDecay,
      retirement: mockRetirement,
    },
  },
  {
    alpha_id: 'ashare_microcap_composite_v2_u120', display_name: 'ashare_microcap_composite_v2_u120',
    derived_stage: 'PAPER_TRACKED', lifecycle_state: null,
    warnings: [
      '无 falsification 证据却有后段证据 —— 阶段跳跃(未证伪先行),需人工确认',
      '权威 dry-run 的 research-risk 决策为 RISK_REJECTED(非 RISK_APPROVED)',
    ],
    stages: {
      backtest: {
        status: 'PRESENT', source: 'data\\cache\\dry_run.db',
        strategy_id: 'ashare_microcap_composite_v2_u120', run_id: '1d71ce97c06c3b00',
        timestamp: 1784246400000, canonical: false, n_runs_for_strategy: 1,
        mode: 'multi_period', cadence: 'weekly',
        n_periods: 75, window_start_ts: 1735862400000, window_end_ts: 1784246400000,
        total_return: 0.207559, sharpe_raw: 0.6622, sharpe_vol_target: null,
        max_drawdown_raw: 0.3485, max_drawdown_vol_target: null,
        reconciliation_residual: 2e-8, n_intents: 7441, n_approved: 7441, n_rejected: 0,
        research_risk_decision: 'RISK_REJECTED', cost_model: 'FLAT_BPS', cost_bps: 15.0,
        crowding_gate: null,
      },
      paper: {
        status: 'PRESENT', source: 'data\\cache\\paper_track.db',
        paper_id: 'ashare_microcap_composite_v2_u120', n_periods: 74,
        track: {
          start_ts: 1735862400000, last_ts: 1783641600000, n_periods: 74,
          cum_return: 0.216348, paper_sharpe: 1.4368, paper_ic: 0.1065,
          backtest_sharpe: 0.2967, perf_gap: -3.8427,
        },
        forward: null, required_forward_periods: 8,
      },
      decay: mockDecay,
      retirement: mockRetirement,
    },
  },
  {
    alpha_id: 'h_momentum_3', display_name: 'h_momentum_3',
    derived_stage: 'PAPER_TRACKED', lifecycle_state: null,
    warnings: [
      '无 falsification 证据却有后段证据 —— 阶段跳跃(未证伪先行),需人工确认',
      '无前向 paper 证据或权威 dry-run 执行 —— 阶段跳跃(缺执行现实证据)',
      'paper-track 时间戳锚在 2000 年前 —— 合成研究会话锚定,非真实前向证据',
    ],
    stages: {
      paper: {
        status: 'PRESENT', source: 'data\\cache\\paper_track.db',
        paper_id: 'h_momentum_3', n_periods: 24,
        track: {
          start_ts: 4579200000, last_ts: 6566400000, n_periods: 24,
          cum_return: 0.025382, paper_sharpe: 4.7778, paper_ic: null,
          backtest_sharpe: 0.3194, perf_gap: -13.9588,
        },
        forward: null, required_forward_periods: 8,
      },
      decay: mockDecay,
      retirement: mockRetirement,
    },
  },
  {
    alpha_id: 'h_zscore_5', display_name: 'h_zscore_5',
    derived_stage: 'PAPER_TRACKED', lifecycle_state: null,
    warnings: [
      '无 falsification 证据却有后段证据 —— 阶段跳跃(未证伪先行),需人工确认',
      '无前向 paper 证据或权威 dry-run 执行 —— 阶段跳跃(缺执行现实证据)',
      'paper-track 时间戳锚在 2000 年前 —— 合成研究会话锚定,非真实前向证据',
    ],
    stages: {
      paper: {
        status: 'PRESENT', source: 'data\\cache\\paper_track.db',
        paper_id: 'h_zscore_5', n_periods: 24,
        track: {
          start_ts: 4579200000, last_ts: 6566400000, n_periods: 24,
          cum_return: 0.010961, paper_sharpe: 2.3584, paper_ic: null,
          backtest_sharpe: 1.859, perf_gap: -0.2687,
        },
        forward: null, required_forward_periods: 8,
      },
      decay: mockDecay,
      retirement: mockRetirement,
    },
  },
];

const mockPayload: AlphaLifecyclePayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_ALPHA_LIFECYCLE', snapshot_status: 'REAL', snapshot_present: true,
  artifact: 'ALPHA_LIFECYCLE_COCKPIT', generated_at_ms: 1784905411990,
  readiness: 'L2_DRY_RUN_ONLY',
  alphas: mockAlphas,
  stage_legend: {},
  honesty_note: 'Mock fixture —— 真实载荷为 evidence/alpha_lifecycle_cockpit.json 原样 serve;数值摘录自 2026-07-24 真实快照。derived_stage 是机械推导,非晋级背书;全绿也不授予任何能力。',
};

/* ---------- 五档 derived_stage 进度轨 --------------------------------------- */

function DerivedRail({ stage }: { stage: string }) {
  const rank = derivedRank(stage);
  return (
    <div className="flex flex-wrap items-center gap-y-1" role="img" aria-label={`derived stage ${stage}`}>
      {DERIVED_LADDER.map((s, i) => {
        const reached = rank >= 0 && i <= rank;
        const current = i === rank;
        const c = reached ? (current ? 'var(--accent)' : 'var(--pos)') : 'var(--faint)';
        return (
          <Fragment key={s}>
            {i > 0 && <span className="mx-1 h-px w-3 sm:w-5" style={{ background: reached ? 'var(--pos)' : 'var(--line)' }} />}
            <span className="rounded-full px-2 py-0.5 text-3xs font-semibold"
              style={{
                color: c,
                background: reached ? `color-mix(in srgb, ${c} ${current ? 18 : 10}%, transparent)` : 'var(--inset)',
                boxShadow: current
                  ? `0 0 0 1px color-mix(in srgb, ${c} 55%, transparent), 0 0 10px -2px ${c}`
                  : `inset 0 0 0 1px color-mix(in srgb, ${c} 25%, transparent)`,
              }}>
              {s}
            </span>
          </Fragment>
        );
      })}
      {rank < 0 && <span className="ml-2 text-3xs text-faint">{bi('未知阶段', 'unknown stage')}</span>}
    </div>
  );
}

/* ---------- 十段生命周期证据格 ---------------------------------------------- */

function StageGrid({ entry }: { entry: AlphaLifecycleEntry }) {
  return (
    <div className="grid gap-1.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(108px, 1fr))' }}>
      {STAGE_ORDER.map(k => {
        const st = entry.stages[k];
        const present = st?.status === 'PRESENT';
        const head = present ? headline(entry, k) : null;
        const c = present ? 'var(--pos)' : 'var(--faint)';
        const src = st?.source ?? (Array.isArray(st?.sources) ? st.sources.join(' · ') : undefined);
        return (
          <div key={k} className="rounded-md border px-2 py-1.5" title={src}
            style={{
              borderColor: `color-mix(in srgb, ${c} ${present ? 35 : 18}%, transparent)`,
              background: present ? `color-mix(in srgb, ${c} 7%, transparent)` : 'var(--inset)',
              opacity: present ? 1 : 0.72,
            }}>
            <div className="flex items-center justify-between gap-1">
              <span className={`text-3xs font-semibold ${present ? 'text-ink' : 'text-faint'}`}>{STAGE_ZH[k]}</span>
              <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: c }} />
            </div>
            <div className={`mt-0.5 truncate text-3xs ticker-num ${present ? 'text-muted' : 'text-faint'}`}>
              {present ? (head ?? '—') : bi('缺证据', 'no evidence')}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- 分阶段证据明细(展开) --------------------------------------------- */

function AbsentStage() {
  return (
    <div className="text-2xs text-faint">
      {bi('缺证据 —— 该阶段证据仓无此 alpha 记录,后端诚实落 ABSENT,前端绝不编造。',
        'No evidence — the stage’s evidence store has no record for this alpha; the backend honestly marks ABSENT and the frontend never fabricates.')}
    </div>
  );
}

function DiscoveryDetail({ s }: { s: AlphaLifecycleDiscoveryStage }) {
  const horizons = Object.entries(s.by_holding_period ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  const horizonCols: Column<[string, AlphaLifecycleDiscoveryHorizon]>[] = [
    { key: 'h', header: bi('持有期', 'horizon'), render: ([h]) => `${h}d` },
    { key: 'ic', header: 'mean IC', align: 'right', mono: true, render: ([, v]) => fixed(v.mean_ic, 4) },
    { key: 'icir', header: 'ICIR', align: 'right', mono: true, render: ([, v]) => fixed(v.icir, 4) },
    { key: 't', header: 't-stat', align: 'right', mono: true, render: ([, v]) => fixed(v.t_stat, 2) },
    { key: 'hit', header: bi('命中率', 'hit'), align: 'right', mono: true, render: ([, v]) => pct(v.hit_rate, 1) },
    { key: 'n', header: 'n', align: 'right', mono: true, render: ([, v]) => num(v.n_periods) },
  ];
  return (
    <div>
      <KeyValue cols={4} items={[
        { k: 'mean IC', v: fixed(s.mean_ic, 4) }, { k: 'ICIR', v: fixed(s.icir, 4) },
        { k: 't-stat', v: fixed(s.t_stat, 2) }, { k: bi('命中率', 'hit rate'), v: pct(s.hit_rate, 1) },
        { k: bi('期数', 'periods'), v: num(s.n_periods) }, { k: bi('宇宙', 'universe'), v: s.universe ?? '—' },
        { k: 'factor', v: s.factor ?? '—' }, { k: bi('标的数', 'instruments'), v: num(s.n_instruments) },
      ]} />
      {horizons.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-3xs text-faint">{bi('分持有期 rank IC(初筛诊断,非晋级门禁)', 'per-horizon rank IC (screening diagnostic, not a promotion gate)')}</div>
          <DataTable rows={horizons} columns={horizonCols} getKey={([h]) => h} density="compact" />
        </div>
      )}
    </div>
  );
}

function FalsificationDetail({ s }: { s: AlphaLifecycleFalsificationStage }) {
  const checks = Object.entries(s.checks ?? {});
  const ev = scalarItems(s.research_evidence);
  const vs = asRecord(s.verify_summary);
  const feeGrid = asRecord(vs?.net_sharpe_by_fee_bps);
  const oos = asRecord(vs?.embargoed_oos);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {s.n_checks_pass != null && s.n_checks_total != null && (
          <Chip label={`${s.n_checks_pass}/${s.n_checks_total} checks`} tone={s.verdict_pass ? 'pos' : 'warn'} />
        )}
        {s.verdict_pass != null && <Chip label={s.verdict_pass ? 'VERDICT_PASS' : 'VERDICT_FAIL'} tone={s.verdict_pass ? 'pos' : 'neg'} />}
        {s.research_verdict && <Chip label={s.research_verdict} tone={statusTone(s.research_verdict)} />}
      </div>
      {checks.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {checks.map(([name, ok]) => <Chip key={name} label={`${ok ? '✓' : '✗'} ${name}`} tone={ok ? 'pos' : 'neg'} />)}
        </div>
      )}
      {ev.length > 0 && <KeyValue cols={4} items={ev} />}
      {typeof vs?.verdict === 'string' && <div className="text-2xs text-muted">{vs.verdict}</div>}
      {feeGrid && (
        <div>
          <div className="mb-1 text-3xs text-faint">{bi('净 Sharpe × 费率', 'net Sharpe × fee')}</div>
          <KeyValue cols={4} items={Object.entries(feeGrid).map(([bps, v]) => ({
            k: `${bps} bps`, v: fixed(typeof v === 'number' ? v : null, 3),
          }))} />
        </div>
      )}
      {oos && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-2xs text-muted">
          <span>embargoed OOS: mean_oos <span className="ticker-num text-ink">{fixed(typeof oos.mean_oos === 'number' ? oos.mean_oos : null, 3)}</span></span>
          <span>haircut <span className="ticker-num text-ink">{fixed(typeof oos.haircut === 'number' ? oos.haircut : null, 3)}</span></span>
          <span>folds <span className="ticker-num text-ink">{num(typeof oos.n_folds === 'number' ? oos.n_folds : null)}</span></span>
        </div>
      )}
      {(s.research_warnings ?? []).length > 0 && (
        <ul className="list-disc pl-4 text-2xs text-warn">
          {(s.research_warnings ?? []).map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}

function StressDetail({ s }: { s: AlphaLifecycleStressStage }) {
  const crises = Object.entries(s.crises ?? {});
  const crisisCols: Column<[string, AlphaLifecycleCrisis]>[] = [
    { key: 'name', header: bi('危机窗口', 'crisis'), render: ([n]) => <span className="text-ink">{n}</span> },
    { key: 'n', header: 'n', align: 'right', mono: true, render: ([, c]) => num(c.n_periods) },
    { key: 'ret', header: bi('窗口收益', 'return'), align: 'right', mono: true, render: ([, c]) => pct(c.total_return, 1) },
    { key: 'dd', header: bi('窗口回撤', 'drawdown'), align: 'right', mono: true, render: ([, c]) => pct(c.max_drawdown, 1) },
    { key: 'worst', header: bi('最差单期', 'worst'), align: 'right', mono: true, render: ([, c]) => pct(c.worst_period, 1) },
  ];
  const w = s.worst_drawdown_window;
  return (
    <div className="flex flex-col gap-2">
      {s.provenance && <div><Chip label={s.provenance} tone="info" /></div>}
      <KeyValue cols={4} items={[
        { k: 'VaR 95', v: pct(s.var_cvar_95?.var, 2) }, { k: 'CVaR 95', v: pct(s.var_cvar_95?.cvar, 2) },
        { k: 'VaR 99', v: pct(s.var_cvar_99?.var, 2) }, { k: 'CVaR 99', v: pct(s.var_cvar_99?.cvar, 2) },
        { k: bi('Hill 尾指数', 'Hill tail index'), v: fixed(s.hill_tail_index, 2) },
        { k: bi('与 BTC 下尾依赖', 'lower tail dep vs BTC'), v: fixed(s.lower_tail_dep_vs_btc, 3) },
        { k: bi('最差回撤', 'worst drawdown'), v: pct(w?.drawdown, 1) },
        { k: bi('回撤窗口', 'window'), v: w?.peak_date ? `${w.peak_date} → ${w.trough_date ?? '?'}` : '—' },
      ]} />
      {crises.length > 0 && <DataTable rows={crises} columns={crisisCols} getKey={([n]) => n} density="compact" />}
    </div>
  );
}

function BacktestDetail({ s }: { s: AlphaLifecycleBacktestStage }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {s.research_risk_decision && <Chip label={s.research_risk_decision} tone={statusTone(s.research_risk_decision)} />}
        {s.canonical === true && <Chip label="CANONICAL" tone="info" />}
        {s.mode && <Chip label={`${s.mode}${s.cadence ? ` · ${s.cadence}` : ''}`} tone="muted" />}
        <Chip label={`${s.cost_model ?? '—'} ${s.cost_bps != null ? `${s.cost_bps}bps` : ''}`.trim()} tone="muted" />
      </div>
      <KeyValue cols={4} items={[
        { k: 'Sharpe RAW', v: fixed(s.sharpe_raw, 2) }, { k: 'Sharpe VT', v: fixed(s.sharpe_vol_target, 2) },
        { k: bi('回撤 RAW', 'DD raw'), v: pct(s.max_drawdown_raw, 1) }, { k: bi('回撤 VT', 'DD VT'), v: pct(s.max_drawdown_vol_target, 1) },
        { k: bi('总收益', 'total return'), v: pct(s.total_return, 1) }, { k: bi('期数', 'periods'), v: num(s.n_periods) },
        { k: bi('窗口', 'window'), v: `${stamp(s.window_start_ts)} → ${stamp(s.window_end_ts)}` },
        { k: bi('对账残差', 'residual'), v: resid(s.reconciliation_residual) },
        { k: bi('意图 批/拒', 'intents ✓/✗'), v: `${num(s.n_approved)} / ${num(s.n_rejected)}` },
        { k: bi('资金费现金流', 'funding cashflow'), v: s.total_funding != null ? num(s.total_funding, 0) : '—' },
        { k: bi('平均净敞口', 'avg net exposure'), v: pct(s.avg_net_exposure_pct, 2) },
        { k: 'run', v: `${s.strategy_id ?? '—'} · ${(s.run_id ?? '').slice(0, 8) || '—'}` },
      ]} />
      {s.crowding_gate && (
        <div className="flex flex-wrap items-center gap-1.5 text-2xs text-muted">
          <span>{bi('拥挤门', 'crowding gate')}:</span>
          <Chip label={`pass ${s.crowding_gate.n_pass}`} tone="pos" />
          <Chip label={`warn ${s.crowding_gate.n_warn}`} tone="warn" />
          <Chip label={`block ${s.crowding_gate.n_block}`} tone={s.crowding_gate.n_block > 0 ? 'neg' : 'muted'} />
          <span className="ticker-num">worst {fixed(s.crowding_gate.worst_score, 3)}</span>
        </div>
      )}
    </div>
  );
}

function CapacityDetail({ s }: { s: AlphaLifecycleCapacityStage }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        {s.provenance && <Chip label={s.provenance} tone="warn" />}
        <Chip label={bi('诊断非门禁', 'diagnostic, not a gate')} tone="muted" />
        {s.data_quality && <Chip label={`DQ ${s.data_quality}`} tone={statusTone(s.data_quality)} />}
      </div>
      <KeyValue cols={3} items={[
        { k: bi('参与率容量上限', 'participation cap AUM'), v: `$${compact(s.participation_cap_aum)}` },
        { k: bi('校准区间', 'calibrated range'), v: s.calibrated_range ? `$${compact(s.calibrated_range.low)} – $${compact(s.calibrated_range.high)}` : '—' },
        { k: bi('绑定约束', 'binding'), v: s.binding ?? '—' },
        { k: 'gross Sharpe', v: fixed(s.gross_sharpe, 2) },
        { k: bi('期数', 'periods'), v: num(s.n_periods) },
        { k: 'book', v: s.book ?? '—' },
      ]} />
    </div>
  );
}

function PortfolioDetail({ s }: { s: AlphaLifecyclePortfolioStage }) {
  const rs = s.return_stream_combo;
  const opt = s.optimizer_combo;
  if (!rs && !opt) {
    return <div className="text-2xs text-faint">{bi('无组合证据(该 alpha 不在组合验证快照内)', 'no combo evidence (alpha absent from the combo snapshots)')}</div>;
  }
  return (
    <div className="flex flex-col gap-3">
      {rs && (
        <div>
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <span className="text-2xs font-semibold text-ink">{bi('收益流组合', 'return-stream combo')}</span>
            <Chip label="GROSS · RELATIVE" tone="warn" />
          </div>
          <KeyValue cols={4} items={[
            { k: bi('单条 Sharpe', 'standalone SR'), v: fixed(rs.standalone_sharpe, 2) },
            { k: bi('等权组合 SR', 'equal-weight SR'), v: fixed(rs.combo_equal_weight_sharpe, 2) },
            { k: bi('逆波动组合 SR', 'inv-vol SR'), v: fixed(rs.combo_inverse_vol_sharpe, 2) },
            { k: bi('组合回撤', 'combo DD'), v: pct(rs.combo_inverse_vol_max_drawdown, 1) },
            { k: 'corr(carry,crowd)', v: fixed(rs.corr_carry_crowd, 3) },
            { k: bi('共同周', 'common weeks'), v: num(rs.common_weeks) },
          ]} />
        </div>
      )}
      {opt && (
        <div>
          <div className="mb-1 text-2xs font-semibold text-ink">{bi('优化器组合', 'optimizer combo')}</div>
          <KeyValue cols={4} items={[
            { k: bi('权重', 'weight'), v: pct(opt.weight, 1) },
            { k: bi('单条 econ SR', 'alone econ SR'), v: fixed(opt.alone_econ_sharpe, 2) },
            { k: bi('组合 econ SR', 'combo econ SR'), v: fixed(opt.combo_econ_sharpe, 2) },
            { k: bi('提升', 'lift'), v: fixed(opt.lift_vs_best_alone, 3) },
          ]} />
          {opt.verdict && <div className="mt-1 text-2xs text-muted">{opt.verdict}</div>}
        </div>
      )}
    </div>
  );
}

function PaperDetail({ s }: { s: AlphaLifecyclePaperStage }) {
  const t = s.track;
  const f = s.forward;
  return (
    <div className="flex flex-col gap-2">
      {t ? (
        <KeyValue cols={4} items={[
          { k: bi('累计收益', 'cum return'), v: pct(t.cum_return, 2) },
          { k: 'paper Sharpe', v: fixed(t.paper_sharpe, 2) },
          { k: 'paper IC', v: fixed(t.paper_ic, 3) },
          { k: 'backtest Sharpe', v: fixed(t.backtest_sharpe, 2) },
          { k: 'perf gap', v: fixed(t.perf_gap, 2) },
          { k: bi('期数', 'periods'), v: num(t.n_periods) },
          { k: bi('起点', 'start'), v: stamp(t.start_ts) },
          { k: bi('最近', 'last'), v: stamp(t.last_ts) },
        ]} />
      ) : (
        <div className="text-2xs text-faint">{bi(`无 paper 轨迹(${s.n_periods ?? 0} 期)`, `no paper track yet (${s.n_periods ?? 0} periods)`)}</div>
      )}
      {f ? (
        <div className="rounded-md border border-warn/30 bg-warn/5 px-2.5 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {f.evidence_state && <Chip label={f.evidence_state} tone={statusTone(f.evidence_state)} />}
            {f.paper_verdict && <Chip label={`VERDICT ${f.paper_verdict}`} tone={statusTone(f.paper_verdict)} />}
            <Chip label={bi(`前向 ${f.n_forward_periods ?? 0}/${s.required_forward_periods ?? '?'} 期`,
              `forward ${f.n_forward_periods ?? 0}/${s.required_forward_periods ?? '?'} periods`)}
              tone={(f.n_forward_periods ?? 0) >= (s.required_forward_periods ?? Infinity) ? 'pos' : 'warn'} />
            {f.pending_decisions != null && <Chip label={`pending ${f.pending_decisions} · attested ${f.attested_pending_decisions ?? 0}`} tone="muted" />}
            {f.last_decision_ts != null && <span className="text-3xs text-faint">last decision {stamp(f.last_decision_ts)}</span>}
          </div>
          {(f.blockers ?? []).length > 0 && (
            <ul className="mt-1.5 list-disc pl-4 text-2xs text-warn">
              {(f.blockers ?? []).map((b, i) => <li key={i} className="mono">{b}</li>)}
            </ul>
          )}
        </div>
      ) : (
        <div className="text-2xs text-faint">
          {bi('无 paper-cycle 前向判定(该 alpha 不在前向关卡登记内,非 BLOCKED)',
            'no paper-cycle forward verdict (alpha not registered in the forward gate — not BLOCKED)')}
        </div>
      )}
    </div>
  );
}

function LiveDetail({ s }: { s: AlphaLifecycleLiveStage }) {
  const runs = s.runs ?? [];
  const runCols: Column<AlphaLifecycleLiveRun>[] = [
    { key: 'venue', header: bi('场地', 'venue'), render: r => <span className="text-ink">{r.venue}</span> },
    { key: 'mode', header: 'mode', render: r => <Chip label={r.mode} tone={r.mode === 'submit' ? 'warn' : 'info'} /> },
    { key: 'tier', header: 'tier', render: r => <span className="text-muted">{r.tier}</span> },
    { key: 'armed', header: 'armed', align: 'center', render: r => (r.armed == null ? '—' : r.armed ? '✓' : '✗') },
    { key: 'stop', header: 'hard_stop', align: 'center', render: r => (r.hard_stop ? <span className="text-neg">✓</span> : '✗') },
    { key: 'sub', header: bi('提交', 'submitted'), align: 'right', mono: true, render: r => num(r.n_submitted) },
    { key: 'rej', header: bi('门拒', 'gate-rejected'), align: 'right', mono: true, render: r => num(r.n_rejected_by_gate) },
    { key: 'at', header: bi('时间', 'ran at'), align: 'right', mono: true, render: r => stamp(r.ran_at) },
  ];
  return (
    <div className="flex flex-col gap-1.5">
      {runs.length > 0
        ? <DataTable rows={runs} columns={runCols} getKey={(r, i) => `${r.venue}-${r.ran_at ?? i}`} density="compact" />
        : <div className="text-2xs text-faint">{bi('无 live-track 运行记录', 'no live-track runs')}</div>}
      <div className="text-3xs text-faint">
        {bi('原样转录 live_track_status*.json;key 权限/mandate/attestation 就绪归「上线向导」(/api/v1/console/go-live-readiness),此处不重复。',
          'Verbatim transcript of live_track_status*.json; key-permission/mandate/attestation readiness lives in the Go-Live wizard, not duplicated here.')}
      </div>
    </div>
  );
}

function DecayDetail({ s }: { s: AlphaLifecycleDecayStage }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-1.5">
        <Chip label={s.degraded ? 'DEGRADED' : bi('未降级', 'NOT DEGRADED')} tone={s.degraded ? 'neg' : 'pos'} />
        {s.actor_kind && <Chip label={`sweep actor ${s.actor_kind}`} tone="muted" />}
      </div>
      <KeyValue cols={3} items={[
        { k: bi('降级动作数', 'decay actions'), v: num(s.n_actions) },
        { k: 'alpha_action', v: s.alpha_action ?? bi('无(权重侧无降级记录)', 'none') },
        { k: bi('sweep 时间', 'sweep at'), v: stamp(s.sweep_generated_at) },
      ]} />
    </div>
  );
}

function RetirementDetail({ s }: { s: AlphaLifecycleRetirementStage }) {
  const events = s.events ?? [];
  return (
    <div className="flex flex-col gap-2">
      <KeyValue cols={3} items={[
        { k: bi('已退役', 'retired'), v: s.retired ? bi('是', 'YES') : bi('否', 'no') },
        { k: bi('退役事件数', 'retirement events'), v: num(s.n_retirement_events) },
      ]} />
      {events.length > 0 && (
        <ul className="flex flex-col gap-1">
          {events.map((ev, i) => (
            <li key={i} className="rounded border border-line bg-inset/60 px-2 py-1 text-3xs mono text-muted">
              {JSON.stringify(ev)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StageDetailBody({ k, entry }: { k: StageKey; entry: AlphaLifecycleEntry }) {
  switch (k) {
    case 'discovery': {
      const s = entry.stages.discovery;
      return s?.status === 'PRESENT' ? <DiscoveryDetail s={s} /> : <AbsentStage />;
    }
    case 'falsification': {
      const s = entry.stages.falsification;
      return s?.status === 'PRESENT' ? <FalsificationDetail s={s} /> : <AbsentStage />;
    }
    case 'stress': {
      const s = entry.stages.stress;
      return s?.status === 'PRESENT' ? <StressDetail s={s} /> : <AbsentStage />;
    }
    case 'backtest': {
      const s = entry.stages.backtest;
      return s?.status === 'PRESENT' ? <BacktestDetail s={s} /> : <AbsentStage />;
    }
    case 'capacity': {
      const s = entry.stages.capacity;
      return s?.status === 'PRESENT' ? <CapacityDetail s={s} /> : <AbsentStage />;
    }
    case 'portfolio': {
      const s = entry.stages.portfolio;
      return s?.status === 'PRESENT' ? <PortfolioDetail s={s} /> : <AbsentStage />;
    }
    case 'paper': {
      const s = entry.stages.paper;
      return s?.status === 'PRESENT' ? <PaperDetail s={s} /> : <AbsentStage />;
    }
    case 'live': {
      const s = entry.stages.live;
      return s?.status === 'PRESENT' ? <LiveDetail s={s} /> : <AbsentStage />;
    }
    case 'decay': {
      const s = entry.stages.decay;
      return s?.status === 'PRESENT' ? <DecayDetail s={s} /> : <AbsentStage />;
    }
    case 'retirement': {
      const s = entry.stages.retirement;
      return s?.status === 'PRESENT' ? <RetirementDetail s={s} /> : <AbsentStage />;
    }
    default:
      return null;
  }
}

/* ---------- alpha 卡片(焦点区全宽卡片 / 研究表展开共用) ----------------------- */

function AlphaCard({ entry, defaultOpen = false }: { entry: AlphaLifecycleEntry; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const attention = forwardAttention(entry);
  const present = nPresent(entry);
  return (
    <Panel className="terminal-panel" bodyClass="flex flex-col gap-3"
      title={<span className="mono">{entry.display_name}</span>}
      eyebrow={bi(`证据覆盖 ${present}/${STAGE_ORDER.length} 段`, `evidence ${present}/${STAGE_ORDER.length} stages`)}
      right={<div className="flex flex-wrap items-center justify-end gap-1.5">
        <Chip label={entry.derived_stage} tone={DERIVED_TONE[entry.derived_stage] ?? 'muted'} />
        {attention.length > 0 && <Chip label={bi('前向待补', 'forward gaps')} tone="warn" />}
        {entry.warnings.length > 0 && <Chip label={`⚠ ${entry.warnings.length}`} tone="warn" />}
      </div>}>
      <DerivedRail stage={entry.derived_stage} />
      <StageGrid entry={entry} />

      {attention.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-3xs text-warn">{bi('前向证据缺口', 'forward evidence gaps')}:</span>
          {attention.map((a, i) => <Chip key={`${a}-${i}`} label={a} tone="warn" />)}
        </div>
      )}

      {entry.warnings.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {entry.warnings.map((w, i) => (
            <li key={i} className="text-2xs leading-snug text-warn">⚠ {w}</li>
          ))}
        </ul>
      )}

      <button type="button" onClick={() => setOpen(o => !o)}
        className="w-fit rounded-md border border-line px-2.5 py-1 text-2xs text-muted transition hover:text-ink">
        {open ? bi('▾ 收起证据明细', '▾ hide evidence') : bi('▸ 证据明细', '▸ evidence detail')}
      </button>
      {open && (
        <div className="grid gap-2 xl:grid-cols-2">
          {STAGE_ORDER.map(k => {
            const presentStage = entry.stages[k]?.status === 'PRESENT';
            return (
              <div key={k} className="rounded-md border border-line bg-inset/40 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-2xs font-semibold text-ink">
                    {STAGE_ZH[k]} <span className="font-normal text-faint">{STAGE_EN[k]}</span>
                  </span>
                  <Chip label={presentStage ? 'PRESENT' : 'ABSENT'} tone={presentStage ? 'pos' : 'muted'} />
                </div>
                <StageDetailBody k={k} entry={entry} />
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

/* ---------- page ----------------------------------------------------------- */

export default function AlphaLifecycle() {
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<AlphaLifecyclePayload>(
    s => fetchAlphaLifecycle(s));
  const p: AlphaLifecyclePayload = usingBackend ? payload! : mockPayload;
  const absent = p.snapshot_present === false || p.snapshot_status === 'NO_SNAPSHOT';
  const pageState = apiState === 'loading' ? 'loading' : absent ? 'empty' : 'ready';

  const focus = useMemo(() => {
    const byId = new Map(p.alphas.map(a => [a.alpha_id, a]));
    return FOCUS_IDS.map(id => byId.get(id)).filter((a): a is AlphaLifecycleEntry => a != null);
  }, [p.alphas]);
  const research = useMemo(
    () => p.alphas
      .filter(a => !(FOCUS_IDS as readonly string[]).includes(a.alpha_id))
      .sort((a, b) => derivedRank(b.derived_stage) - derivedRank(a.derived_stage) || a.alpha_id.localeCompare(b.alpha_id)),
    [p.alphas]);

  const [sel, setSel] = useState<string | null>(null);
  const toggleSel = (id: string) => setSel(s => (s === id ? null : id));
  const selEntry = research.find(a => a.alpha_id === sel) ?? null;

  const count = (stage: string) => p.alphas.filter(a => a.derived_stage === stage).length;
  const nAttention = p.alphas.filter(a => forwardAttention(a).length > 0).length;
  const legendEntries = Object.entries(p.stage_legend ?? {});

  const researchCols: Column<AlphaLifecycleEntry>[] = [
    {
      key: 'alpha', header: 'Alpha', render: a => (
        <button type="button" onClick={() => toggleSel(a.alpha_id)}
          className="mono text-ink underline decoration-dotted underline-offset-2 hover:text-accent">
          {a.display_name}
        </button>
      ),
    },
    { key: 'stage', header: bi('推导阶段', 'derived'), render: a => <Chip label={a.derived_stage} tone={DERIVED_TONE[a.derived_stage] ?? 'muted'} /> },
    { key: 'cov', header: bi('证据', 'evidence'), align: 'right', mono: true, render: a => `${nPresent(a)}/${STAGE_ORDER.length}` },
    { key: 't', header: 'IC t-stat', align: 'right', mono: true, render: a => fixed(a.stages.discovery?.t_stat, 2) },
    {
      key: 'vt', header: 'VT Sharpe', align: 'right', mono: true,
      render: a => fixed(a.stages.backtest?.sharpe_vol_target ?? a.stages.backtest?.sharpe_raw, 2),
    },
    { key: 'pic', header: 'paper IC', align: 'right', mono: true, render: a => fixed(a.stages.paper?.track?.paper_ic, 3) },
    {
      key: 'att', header: bi('前向', 'forward'), render: a => {
        const att = forwardAttention(a);
        return att.length > 0 ? <Chip label={bi(`待补 ${att.length}`, `${att.length} gaps`)} tone="warn" /> : <span className="text-faint">—</span>;
      },
    },
  ];

  return (
    <PageShell>
      <PageHeader title="Lifecycle Cockpit" titleZh="生命周期驾驶舱"
        subtitle={bi('每条边的五档推导阶段 + 十段生命周期证据链:发掘→证伪→压力→回测→容量→组合→纸面→实盘→衰弱→退役。derived_stage 是机械证据推导,非晋级背书;晋级永远走人工门。',
          'Five-rung derived stage + ten-stage evidence chain per edge: discovery→falsification→stress→backtest→capacity→portfolio→paper→live→decay→retirement. derived_stage is a mechanical evidence derivation, never a promotion endorsement; promotion always stays a human gate.')}
        actions={<AsOfStamp ts={p.generated_at_ms} />} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_ALPHA_LIFECYCLE'}
        source={usingBackend ? p.data_provenance ?? 'ALPHA_LIFECYCLE' : 'FRONTEND_ALPHA_LIFECYCLE_MOCK'}
        error={error}
        dataStatus={snapshotState} />
      <StatusBanner />

      <LoadingVeil active={apiState === 'loading'}>
        <StateView state={pageState}
          empty={bi('生命周期证据尚未产出 —— 先人工运行 python -m tools.alpha_lifecycle_cockpit(全本地输入:既有证据仓只读聚合,无网络)。本页绝不伪造生命周期行。',
            'Lifecycle evidence not produced yet — run python -m tools.alpha_lifecycle_cockpit by hand (read-only aggregation of existing local evidence stores, no network). This page never fakes lifecycle rows.')}>
          <KpiStrip cols={5}>
            <StatTile label={bi('Alpha 总数', 'Alphas')} value={p.alphas.length} accent />
            <StatTile label="LIVE_TRACK" value={count('LIVE_TRACK')} tone="pos" />
            <StatTile label="PAPER_TRACKED" value={count('PAPER_TRACKED')} tone="info" />
            <StatTile label="DRY_RUN" value={count('DRY_RUN')} />
            <StatTile label={bi('前向待补', 'Forward gaps')} value={nAttention} tone={nAttention > 0 ? 'warn' : 'pos'} />
          </KpiStrip>

          {/* 1. 焦点区:部署 book 四条策略(全宽卡片) */}
          <div>
            <SectionHeader title={bi('部署轨道策略', 'DEPLOYMENT-TRACK STRATEGIES')}
              sub={bi('干跑/实盘 book 的四条策略 —— 五档推导轨 + 十段证据格,点开「证据明细」看每段原始字段',
                'The four strategies of the dry-run/live book — derived-stage rail + ten-stage evidence grid; expand “evidence detail” for raw per-stage fields')} />
            <div className="flex flex-col gap-4">
              {focus.map(a => <AlphaCard key={a.alpha_id} entry={a} />)}
              {focus.length === 0 && (
                <div className="rounded-lg border border-dashed border-line py-6 text-center text-2xs text-faint">
                  {bi('部署轨道策略不在本快照内', 'deployment-track strategies absent from this snapshot')}
                </div>
              )}
            </div>
          </div>

          {/* 2. 研究候选表(点开行 = 同一套证据格钻取) */}
          <Panel eyebrow={bi('研究候选', 'RESEARCH CANDIDATES')}
            title={bi('研究会话候选(alpha 工厂 / A股微盘)—— 点 alpha 名展开同一套生命周期证据格',
              'Research-session candidates (alpha factory / A-share micro-cap) — click an alpha name to drill into the same lifecycle evidence grid')}>
            <DataTable rows={research} columns={researchCols} getKey={a => a.alpha_id} density="compact"
              searchable={a => `${a.alpha_id} ${a.derived_stage}`}
              empty={bi('无研究候选', 'no research candidates')} />
          </Panel>
          {selEntry && <AlphaCard key={selEntry.alpha_id} entry={selEntry} defaultOpen />}

          {/* 3. 阶段判定图例(后端原样,折叠) */}
          {legendEntries.length > 0 && (
            <Panel eyebrow={bi('图例', 'LEGEND')} title={bi('阶段判定口径(后端 stage_legend 原样)', 'Stage semantics (backend stage_legend, verbatim)')}>
              <details>
                <summary className="cursor-pointer text-2xs text-muted hover:text-ink">
                  {bi('展开每段证据来源与 derived_stage 推导规则', 'Expand evidence sources per stage and the derived_stage rule')}
                </summary>
                <dl className="mt-2 flex flex-col gap-2">
                  {legendEntries.map(([k, text]) => (
                    <div key={k} className="rounded-md border border-line bg-inset/40 px-3 py-2">
                      <dt className="text-2xs font-semibold text-ink">
                        {STAGE_ZH[k as StageKey] ?? k} <span className="font-normal text-faint mono">{k}</span>
                      </dt>
                      <dd className="mt-0.5 text-2xs leading-relaxed text-muted">{text}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            </Panel>
          )}
        </StateView>

        <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
          {p.honesty_note}
        </div>
        <EndpointFootnote path="/api/v1/console/alpha-lifecycle"
          note={bi('只读 · 离线 artifact 原样 serve · derived_stage 为机械推导非晋级背书 · 不授予任何能力',
            'read-only · offline artifact served verbatim · derived_stage is a mechanical derivation, never a promotion endorsement · grants no capability')} />
      </LoadingVeil>
    </PageShell>
  );
}
