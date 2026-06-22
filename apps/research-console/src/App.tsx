import React from 'react';
import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import Overview from './pages/Overview';
import AlphaFactory from './pages/AlphaFactory';
import Experiments from './pages/Experiments';
import DataCatalog from './pages/DataCatalog';
import Validation from './pages/Validation';
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
import { statusLabel } from './lib/i18n/bilingual';

const navItems = [
  { to: '/research', zh: '\u603b\u89c8', en: 'Overview', icon: 'OV' },
  { to: '/research/alpha-factory', zh: '\u56e0\u5b50\u5de5\u5382', en: 'Alpha Factory', icon: 'AF' },
  { to: '/research/experiments', zh: '\u5b9e\u9a8c', en: 'Experiments', icon: 'EX' },
  { to: '/research/data-catalog', zh: '\u6570\u636e', en: 'Data Catalog', icon: 'DC' },
  { to: '/research/validation', zh: '\u9a8c\u8bc1', en: 'Validation', icon: 'VA' },
  { to: '/research/backtest-explainability', zh: '\u56de\u6d4b', en: 'Backtest', icon: 'BT' },
  { to: '/research/scenario-stress', zh: '\u538b\u529b', en: 'Stress', icon: 'ST' },
  { to: '/research/model-risk-review', zh: '\u6a21\u578b\u98ce\u9669', en: 'Model Risk', icon: 'MR' },
  { to: '/research/evidence-ledger', zh: '\u8bc1\u636e\u94fe', en: 'Evidence', icon: 'EL' },
  { to: '/research/reviewer-workflow', zh: '\u590d\u6838', en: 'Reviewer', icon: 'RV' },
  { to: '/research/access-control', zh: '\u6743\u9650', en: 'Access', icon: 'AC' },
  { to: '/research/monitoring-drift', zh: '\u76d1\u63a7', en: 'Monitoring', icon: 'MD' },
  { to: '/research/incident-response', zh: '\u4e8b\u4ef6\u54cd\u5e94', en: 'Incidents', icon: 'IR' },
  { to: '/research/promotion-control', zh: '\u664b\u7ea7\u63a7\u5236', en: 'Promotion', icon: 'PC' },
  { to: '/research/release-committee', zh: '\u53d1\u5e03\u59d4\u5458\u4f1a', en: 'Committee', icon: 'RC' },
  { to: '/research/agents', zh: '\u7814\u7a76\u667a\u80fd\u4f53', en: 'Agents', icon: 'AG' },
  { to: '/research/paper-trading', zh: '\u7eb8\u9762\u4ea4\u6613', en: 'Paper Trading', icon: 'PT' },
  { to: '/research/portfolio', zh: '\u7ec4\u5408\u7814\u7a76', en: 'Portfolio', icon: 'PF' },
  { to: '/research/risk', zh: '\u98ce\u9669', en: 'Risk', icon: 'RK' },
  { to: '/research/events', zh: '\u4e8b\u4ef6', en: 'Events', icon: 'EV' },
  { to: '/research/computation-lab', zh: '\u8ba1\u7b97', en: 'Compute', icon: 'CL' },
  { to: '/research/architecture', zh: '\u67b6\u6784', en: 'Architecture', icon: 'AR' },
  { to: '/research/system-completeness', zh: '\u5b8c\u6574\u5ea6', en: 'Completeness', icon: 'CP' },
  { to: '/research/phase-3a', zh: '3A \u51c6\u5165', en: 'Phase 3A', icon: '3A' },
  { to: '/research/system-health', zh: '\u7cfb\u7edf', en: 'System', icon: 'SY' },
];

const mode = import.meta.env.VITE_MOCK_MODE !== 'false' ? 'MOCK_MODE' : 'REAL_API';

export default function App() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 font-mono">
      <header className="min-h-10 bg-slate-900 border-b border-slate-800 flex flex-wrap items-center gap-y-1 px-4 py-2 text-xs">
        <span className="text-cyan-400 font-bold tracking-wider mr-4">QUANTOS RESEARCH / {'\u91cf\u5316\u7814\u7a76'}</span>
        <span className="text-slate-600">|</span>
        <span className="text-slate-500 ml-4">Phase 1K.2 - {'\u7814\u7a76\u6307\u6325\u4e2d\u5fc3'} / Research Command Center</span>
        <span className={`ml-auto rounded px-2 py-0.5 text-xs font-bold ${mode === 'MOCK_MODE' ? 'bg-amber-900/50 text-amber-300' : 'bg-emerald-900/50 text-emerald-300'}`}>{statusLabel(mode)}</span>
      </header>
      <div className="flex flex-col md:flex-row">
        <nav className="flex w-full flex-shrink-0 gap-1 overflow-x-auto border-b border-slate-800 bg-slate-900 p-2 md:block md:w-48 md:min-h-[calc(100vh-2.5rem)] md:border-b-0 md:border-r">
          {navItems.map(item => (
            <NavLink key={item.to} to={item.to} end={item.to === '/research'}
              className={({isActive}) => `flex flex-shrink-0 items-center gap-2 whitespace-nowrap rounded px-3 py-2 text-sm transition-colors ${isActive ? 'text-slate-100 bg-slate-800' : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800'}`}>
              <span className="text-cyan-500 w-5 text-center text-[10px] font-bold">{item.icon}</span>
              <span className="min-w-0 leading-tight">
                <span className="block">{item.zh}</span>
                <span className="block text-[10px] text-slate-500">{item.en}</span>
              </span>
            </NavLink>
          ))}
        </nav>
        <main className="min-w-0 flex-1 overflow-auto p-4">
          <Routes>
            <Route path="/" element={<Navigate to="/research" replace />} />
            <Route path="/research" element={<Overview />} />
            <Route path="/research/alpha-factory" element={<AlphaFactory />} />
            <Route path="/research/experiments" element={<Experiments />} />
            <Route path="/research/data-catalog" element={<DataCatalog />} />
            <Route path="/research/validation" element={<Validation />} />
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
        </main>
      </div>
    </div>
  );
}
