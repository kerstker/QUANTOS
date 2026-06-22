/** Mock data for development. All data lives here, never inside components. */
import type { AlphaCandidate, ValidationReport, ResearchAgent, PaperRun, PortfolioSimulation, RiskReport, ServiceHealth, Phase3AReadiness } from "@/lib/types/research";

export const mockAlphaCandidates: AlphaCandidate[] = Array.from({length:12},(_,i)=>({
  candidate_id:`alpha-${i+1}`,expression:`(ts_mean :close ${[5,10,20,30,60][i%5]})`,
  ic:0.02+Math.random()*0.04,rank_ic:0.015+Math.random()*0.04,icir:0.4+Math.random()*0.8,
  sharpe:0.5+Math.random()*1.2,turnover:0.2+Math.random()*0.8,decay_half_life:5+Math.floor(Math.random()*30),
  capacity_estimate:10_000_000+Math.random()*200_000_000,correlation_penalty:Math.random()*0.3,
  composite_score:0.3+Math.random()*0.6,lifecycle_state:["ACTIVE","VALIDATING","DECAYING","RETIRED"][i%4],
}));

export const mockValidationReports: ValidationReport[] = [
  {run_id:"val-001",alpha_id:"alpha-1",decision:"APPROVED",gates_passed:9,gates_total:9,reasons:[],
   leakage_pass:true,walk_forward_pass:true,multiple_testing_pass:true,costs_pass:true,
   capacity_pass:true,decay_pass:true,oos_sharpe:0.85,oos_ic:0.035,net_sharpe:0.72},
  {run_id:"val-002",alpha_id:"alpha-2",decision:"REJECTED_OVERFIT",gates_passed:4,gates_total:9,
   reasons:["WFCV failed","PBO=0.78"],leakage_pass:true,walk_forward_pass:false,
   multiple_testing_pass:false,costs_pass:true,capacity_pass:true,decay_pass:true,oos_sharpe:0.12,oos_ic:0.005,net_sharpe:0.08},
  {run_id:"val-003",alpha_id:"alpha-3",decision:"REJECTED_LEAKAGE",gates_passed:3,gates_total:9,
   reasons:["Leakage: 3 critical"],leakage_pass:false,walk_forward_pass:true,
   multiple_testing_pass:true,costs_pass:true,capacity_pass:true,decay_pass:true,oos_sharpe:0.45,oos_ic:0.028,net_sharpe:0.40},
];

export const mockAgents: ResearchAgent[] = [
  {agent_id:"agent-1",name:"Macro Analyst",state:"RUNNING",runs_completed:42,alpha_approved:7,performance_score:0.78,budget_spent_cny:150},
  {agent_id:"agent-2",name:"Factor Hunter",state:"REFLECTING",runs_completed:28,alpha_approved:3,performance_score:0.62,budget_spent_cny:280},
  {agent_id:"agent-3",name:"Momentum Specialist",state:"IDLE",runs_completed:15,alpha_approved:2,performance_score:0.55,budget_spent_cny:95},
];

export const mockPaperRuns: PaperRun[] = [{
  run_id:"paper-001",alpha_id:"alpha-1",status:"PAPER_PASSED",
  metrics:{sharpe:0.82,max_drawdown:12.5,annual_return:18.3},
  equity_curve:Array.from({length:60},(_,i)=>({timestamp:i,equity:10_000_000*(1+0.002*(i+Math.sin(i*0.1)*5))})),
  total_trades:45,final_equity:10_800_000,
}];

export const mockPortfolioSims: PortfolioSimulation[] = [{
  run_id:"sim-001",status:"COMPLETED",
  metrics:{sharpe:1.15,total_return:22.5,max_drawdown:8.3,volatility:12.1},
  equity_curve:Array.from({length:120},(_,i)=>({day:i,equity:100_000_000*(1+0.0015*(i+Math.cos(i*0.05)*3))})),
}];

export const mockRiskReports: RiskReport[] = [
  {run_id:"risk-001",portfolio_id:"p-001",decision:"RISK_APPROVED",gates_passed:9,gates_total:9,reasons:[],
   gate_results:{market:true,liquidity:true,concentration:true,correlation:true,drawdown:true,regime:true,stress:true,capacity:true,model_alpha:true}},
  {run_id:"risk-002",portfolio_id:"p-002",decision:"REJECTED_CONCENTRATION_RISK",gates_passed:6,gates_total:9,
   reasons:["HHI=0.32, max_single=0.45"],gate_results:{market:true,liquidity:true,concentration:false,correlation:true,drawdown:true,regime:true,stress:true,capacity:true,model_alpha:true}},
];

export const mockHealthServices: ServiceHealth[] = [
  {service:"market-data-ingestor",status:"healthy",latency_ms:3.2,error_rate:0.001},
  {service:"alpha-discovery-engine",status:"healthy",latency_ms:45.0,error_rate:0.01},
  {service:"alpha-validation",status:"healthy",latency_ms:12.5,error_rate:0.002},
  {service:"research-agent-loop",status:"healthy",latency_ms:250.0,error_rate:0.05},
  {service:"paper-trading",status:"healthy",latency_ms:8.1,error_rate:0.0},
  {service:"portfolio-research",status:"healthy",latency_ms:15.3,error_rate:0.001},
  {service:"research-risk",status:"degraded",latency_ms:85.0,error_rate:0.03},
];

export const mockStats = {
  total_hypotheses:142,alpha_candidates:1250,validation_pass_rate:0.18,paper_pass_rate:0.62,
  portfolio_candidates:8,risk_approved:3,active_runs:3,
  rejection_reasons:{REJECTED_OVERFIT:45,REJECTED_LEAKAGE:12,REJECTED_LOW_CAPACITY:28,REJECTED_UNSTABLE:15,REJECTED_HIGH_CORRELATION:8},
};

export const mockPhase3AReadiness: Phase3AReadiness = {
  current_level: "L2_DRY_RUN_ONLY",
  target_level: "L3_BROKER_TEST_ENV_READY",
  decision: "NO_GO",
  phase_status: "BLOCKED",
  implementation_authorized: false,
  approved_count: 10,
  total_count: 15,
  criteria: [
    {id:"E01",criterion:"Phase 2H governance approved",category:"Governance",owner:"CTO",status:"PENDING",evidence:"Governance signoff register"},
    {id:"E02",criterion:"Broker test env account confirmed",category:"Broker",owner:"Broker Relationship Owner",status:"PENDING",evidence:"Broker response intake"},
    {id:"E03",criterion:"Legal/compliance approval",category:"Compliance",owner:"Compliance Officer",status:"PENDING",evidence:"Legal/compliance checklist"},
    {id:"E04",criterion:"Test credentials in Vault",category:"Credentials",owner:"Security Officer",status:"PENDING",evidence:"Vault path and access policy"},
    {id:"E05",criterion:"No credentials in code",category:"Security",owner:"Security Officer",status:"APPROVED",evidence:"Governance scanner report"},
    {id:"E06",criterion:"Network access reviewed",category:"Network",owner:"Security Officer",status:"PENDING",evidence:"Network/IP whitelist review"},
    {id:"E07",criterion:"Broker readiness checklist",category:"Operations",owner:"Operations Lead",status:"APPROVED",evidence:"Broker readiness checklist"},
    {id:"E08",criterion:"Kill switch tested",category:"Risk",owner:"Trading System Owner",status:"APPROVED",evidence:"Kill switch drill report"},
    {id:"E09",criterion:"Rollback tested",category:"Risk",owner:"Trading System Owner",status:"APPROVED",evidence:"Rollback drill report"},
    {id:"E10",criterion:"DR runbooks approved",category:"DR",owner:"Operations Lead",status:"APPROVED",evidence:"DR dry-run report"},
    {id:"E11",criterion:"UAT plan approved",category:"UAT",owner:"Operations Lead",status:"APPROVED",evidence:"UAT dry-run report"},
    {id:"E12",criterion:"Incident response approved",category:"IR",owner:"Operations Lead",status:"APPROVED",evidence:"Incident response plan"},
    {id:"E13",criterion:"No production credentials",category:"Security",owner:"Security Officer",status:"APPROVED",evidence:"Governance scanner report"},
    {id:"E14",criterion:"No production trading",category:"Security",owner:"Risk Officer",status:"APPROVED",evidence:"Governance scanner report"},
    {id:"E15",criterion:"Phase 2G.1 tests pass",category:"Technical",owner:"Trading System Owner",status:"APPROVED",evidence:"Dry-run adapter gate evidence"},
  ],
  blockers: [
    {id:"E01",blocker:"Governance signatures incomplete",owner:"CTO",dependency:"None",next_action:"Collect required role signatures"},
    {id:"E02",blocker:"Broker test environment not confirmed",owner:"Broker Relationship Owner",dependency:"Broker response",next_action:"Send broker request package"},
    {id:"E03",blocker:"Legal/compliance approval incomplete",owner:"Compliance Officer",dependency:"E02",next_action:"Review broker agreement and test scope"},
    {id:"E04",blocker:"Test credentials not provisioned in Vault",owner:"Security Officer",dependency:"E02",next_action:"Prepare Vault-only intake workflow"},
    {id:"E06",blocker:"Network access not reviewed",owner:"Security Officer",dependency:"E02",next_action:"Collect broker endpoint and whitelist details"},
  ],
};

export const mockCalculationLineage = [
  {
    id: "IC",
    layer: "Alpha",
    formula: "corr(factor_values, forward_returns)",
    backend: "services/research/factor_engine/domain/model.py",
    inputs: ["factor_values", "forward_returns"],
    output: "information_coefficient",
  },
  {
    id: "RANK_IC",
    layer: "Alpha",
    formula: "corr(rank(factor_values), rank(forward_returns))",
    backend: "services/research/factor_engine/domain/model.py",
    inputs: ["ranked_factor_values", "ranked_forward_returns"],
    output: "rank_information_coefficient",
  },
  {
    id: "SHARPE",
    layer: "Paper",
    formula: "mean(daily_returns) / std(daily_returns) * sqrt(252)",
    backend: "services/research/paper_trading/domain/pnl.py",
    inputs: ["equity_curve"],
    output: "annualized_sharpe",
  },
  {
    id: "MAX_DD",
    layer: "Paper",
    formula: "max((peak_equity - equity) / peak_equity)",
    backend: "services/research/paper_trading/domain/pnl.py",
    inputs: ["equity_curve"],
    output: "max_drawdown",
  },
  {
    id: "RISK_GATE",
    layer: "Risk",
    formula: "all(required_gates.pass) && fail_closed_on_unknown",
    backend: "services/risk/research_risk/domain/gate.py",
    inputs: ["market", "liquidity", "concentration", "correlation", "drawdown", "stress"],
    output: "risk_decision",
  },
  {
    id: "AGENT_SCORE",
    layer: "Agent",
    formula: "0.20*pass_rate + 0.20*novelty + 0.15*budget + 0.15*net_sharpe + ...",
    backend: "services/research/research_agent_loop/domain/supervisor.py",
    inputs: ["validation_results", "tool_costs", "failure_history"],
    output: "agent_performance_score",
  },
];
