import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppShell, type NavGroup } from '@/ui/AppShell';

import Overview from './pages/Overview';
import BacktestResults from './pages/BacktestResults';
import CryptoLive from './pages/CryptoLive';
import AlphaFactory from './pages/AlphaFactory';
import AlphaZoo from './pages/AlphaZoo';
import DslExplorer from './pages/DslExplorer';
import AlphaGraph from './pages/AlphaGraph';
import PopulationOptimizer from './pages/PopulationOptimizer';
import SignalToPortfolio from './pages/SignalToPortfolio';
import Experiments from './pages/Experiments';
import DataCatalog from './pages/DataCatalog';
import Validation from './pages/Validation';
import LifecycleMonitor from './pages/LifecycleMonitor';
import BacktestExplainability from './pages/BacktestExplainability';
import ScenarioStress from './pages/ScenarioStress';
import ModelRiskReview from './pages/ModelRiskReview';
import EvidenceLedger from './pages/EvidenceLedger';
import ReviewerWorkflow from './pages/ReviewerWorkflow';
import AccessControl from './pages/AccessControl';
import MonitoringDrift from './pages/MonitoringDrift';
import IncidentResponse from './pages/IncidentResponse';
import PromotionControl from './pages/PromotionControl';
import ReleaseCommittee from './pages/ReleaseCommittee';
import Agents from './pages/Agents';
import PaperTrading from './pages/PaperTrading';
import Portfolio from './pages/Portfolio';
import Risk from './pages/Risk';
import Events from './pages/Events';
import ComputationLab from './pages/ComputationLab';
import Phase3AReadiness from './pages/Phase3AReadiness';
import SystemHealth from './pages/SystemHealth';
import Architecture from './pages/Architecture';
import SystemCompleteness from './pages/SystemCompleteness';

const nav: NavGroup[] = [
  { zh: '研究', en: 'RESEARCH', items: [
    { to: '/research', zh: '总览', en: 'Overview', icon: 'OV', end: true },
    { to: '/research/backtest-results', zh: '回测战果', en: 'Results', icon: 'BR' },
    { to: '/research/crypto-live', zh: '加密实盘研究', en: 'Crypto Live', icon: '₿' },
    { to: '/research/alpha-factory', zh: '因子工厂', en: 'Alpha Factory', icon: 'AF' },
    { to: '/research/alpha-zoo', zh: 'Alpha 资产库', en: 'Alpha Zoo', icon: 'AZ' },
    { to: '/research/dsl-explorer', zh: '因子 DSL', en: 'DSL Explorer', icon: 'DSL' },
    { to: '/research/alpha-graph', zh: 'Alpha 相关图', en: 'Alpha Graph', icon: 'GR' },
    { to: '/research/experiments', zh: '实验登记', en: 'Experiments', icon: 'EX' },
    { to: '/research/data-catalog', zh: '数据目录', en: 'Data Catalog', icon: 'DC' },
    { to: '/research/computation-lab', zh: '计算实验室', en: 'Computation', icon: 'CL' },
    { to: '/research/agents', zh: '研究智能体', en: 'Agents', icon: 'AG' },
  ]},
  { zh: '验证', en: 'VALIDATION', items: [
    { to: '/research/validation', zh: '验证门', en: 'Validation', icon: 'VA' },
    { to: '/research/lifecycle-monitor', zh: '生命周期监控', en: 'Lifecycle', icon: 'LC' },
    { to: '/research/backtest-explainability', zh: '回测解释', en: 'Backtest', icon: 'BT' },
    { to: '/research/scenario-stress', zh: '情景压力', en: 'Stress', icon: 'ST' },
    { to: '/research/model-risk-review', zh: '模型风险', en: 'Model Risk', icon: 'MR' },
    { to: '/research/paper-trading', zh: '纸面交易', en: 'Paper Trading', icon: 'PT' },
  ]},
  { zh: '组合·风险', en: 'PORTFOLIO & RISK', items: [
    { to: '/research/portfolio', zh: '组合研究', en: 'Portfolio', icon: 'PF' },
    { to: '/research/population-optimizer', zh: '种群优化器', en: 'Optimizer', icon: 'PO' },
    { to: '/research/signal-to-portfolio', zh: '信号→组合', en: 'Signal→Book', icon: 'SP' },
    { to: '/research/risk', zh: '风险仪表', en: 'Risk', icon: 'RK' },
    { to: '/research/events', zh: '事件流', en: 'Events', icon: 'EV' },
  ]},
  { zh: '治理', en: 'GOVERNANCE', items: [
    { to: '/research/evidence-ledger', zh: '证据链', en: 'Evidence', icon: 'EL' },
    { to: '/research/reviewer-workflow', zh: '复核流', en: 'Reviewer', icon: 'RV' },
    { to: '/research/access-control', zh: '权限', en: 'Access', icon: 'AC' },
    { to: '/research/monitoring-drift', zh: '监控漂移', en: 'Monitoring', icon: 'MD' },
    { to: '/research/incident-response', zh: '事件响应', en: 'Incidents', icon: 'IR' },
    { to: '/research/promotion-control', zh: '晋级控制', en: 'Promotion', icon: 'PC' },
    { to: '/research/release-committee', zh: '发布委员会', en: 'Committee', icon: 'RC' },
  ]},
  { zh: '系统', en: 'SYSTEM', items: [
    { to: '/research/architecture', zh: '架构', en: 'Architecture', icon: 'AR' },
    { to: '/research/system-completeness', zh: '完整度', en: 'Completeness', icon: 'CP' },
    { to: '/research/phase-3a', zh: '3A 准入', en: 'Phase 3A', icon: '3A' },
    { to: '/research/system-health', zh: '系统健康', en: 'System', icon: 'SY' },
  ]},
];

export default function App() {
  return (
    <AppShell nav={nav}>
      <Routes>
        <Route path="/" element={<Navigate to="/research" replace />} />
        <Route path="/research" element={<Overview />} />
        <Route path="/research/backtest-results" element={<BacktestResults />} />
        <Route path="/research/crypto-live" element={<CryptoLive />} />
        <Route path="/research/alpha-factory" element={<AlphaFactory />} />
        <Route path="/research/alpha-zoo" element={<AlphaZoo />} />
        <Route path="/research/dsl-explorer" element={<DslExplorer />} />
        <Route path="/research/alpha-graph" element={<AlphaGraph />} />
        <Route path="/research/population-optimizer" element={<PopulationOptimizer />} />
        <Route path="/research/signal-to-portfolio" element={<SignalToPortfolio />} />
        <Route path="/research/experiments" element={<Experiments />} />
        <Route path="/research/data-catalog" element={<DataCatalog />} />
        <Route path="/research/validation" element={<Validation />} />
        <Route path="/research/lifecycle-monitor" element={<LifecycleMonitor />} />
        <Route path="/research/backtest-explainability" element={<BacktestExplainability />} />
        <Route path="/research/scenario-stress" element={<ScenarioStress />} />
        <Route path="/research/model-risk-review" element={<ModelRiskReview />} />
        <Route path="/research/evidence-ledger" element={<EvidenceLedger />} />
        <Route path="/research/reviewer-workflow" element={<ReviewerWorkflow />} />
        <Route path="/research/access-control" element={<AccessControl />} />
        <Route path="/research/monitoring-drift" element={<MonitoringDrift />} />
        <Route path="/research/incident-response" element={<IncidentResponse />} />
        <Route path="/research/promotion-control" element={<PromotionControl />} />
        <Route path="/research/release-committee" element={<ReleaseCommittee />} />
        <Route path="/research/agents" element={<Agents />} />
        <Route path="/research/paper-trading" element={<PaperTrading />} />
        <Route path="/research/portfolio" element={<Portfolio />} />
        <Route path="/research/risk" element={<Risk />} />
        <Route path="/research/events" element={<Events />} />
        <Route path="/research/computation-lab" element={<ComputationLab />} />
        <Route path="/research/architecture" element={<Architecture />} />
        <Route path="/research/system-completeness" element={<SystemCompleteness />} />
        <Route path="/research/phase-3a" element={<Phase3AReadiness />} />
        <Route path="/research/system-health" element={<SystemHealth />} />
      </Routes>
    </AppShell>
  );
}
