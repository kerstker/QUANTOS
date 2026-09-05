# QUANTOS Cockpit — 前端设计系统(page agent 规范)

顶级 AI 量化交易研究驾驶舱。**保留数据层(types/api/mock/i18n)**,只重建表现层。

## 铁律
1. **保留数据合约**:不改 `src/lib/types/research.ts` 的 `*Payload` 类型;不改 `src/lib/api/*`。页面消费同样的字段。
2. **保留取数+回退**:用 `useConsoleSnapshot(fetcher)`;无后端时回退到原 mock。**保留 ProvenanceRibbon**(诚实合成数据标记)。
3. **先读后写**:重建某页前,先读该页**现有文件**学到它的精确数据接线(fetcher 函数、mock 回退、payload 字段);**完整保留数据逻辑**,只替换 JSX/呈现。
4. **双语**:所有可见文案用 `bi('中文','English')`。数字用 `format.ts`(`num/pct/pctPoints/fixed/signed/money/compact/shortHash/stamp`),加 `tnum` 等宽。
5. **零新依赖**;新代码**不得用 `Math.random`/`Date.now`(确定性);只渲染 payload/mock 已有数据。
6. 全幅响应式栅格,吃满屏宽。strict TS 必须通过。

## 页面骨架(照抄 Overview.tsx 样板)
```tsx
export default function X() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<XPayload>(s => fetchXSnapshot(s));
  const data = usingBackend ? payload! : /* mock fallback as the old file did */;
  return (
    <PageShell>
      <PageHeader title="English" titleZh="中文" subtitle={bi(...)} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />
      <KpiStrip cols={N}><StatTile .../>...</KpiStrip>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow=".." title="..">{/* chart */}</Panel>
        <Panel title="..">{/* ... */}</Panel>
      </div>
      <Panel title="..">{/* DataTable */}</Panel>
    </PageShell>
  );
}
```
有查询参数/交互的页(ComputationLab：alphaId/costBps/stress；Backtest/Stress/Risk/Paper：alphaId）必须**保留原有的选择器与取数参数**(读原文件照搬控件与 fetcher 调用,可不用 useConsoleSnapshot 而沿用原 useEffect 逻辑,但呈现换成新基元)。

## 基元 API(`@/ui/primitives`)
- `PageShell`、`PageHeader{title,titleZh,subtitle?,actions?}`、`ProvenanceRibbon{usingBackend,apiState,source?,error?}`
- `Panel{title?,eyebrow?,right?,tone?,className?,bodyClass?}`、`SectionHeader{title,sub?}`、`Grid{min?}`
- `KpiStrip{cols?}` + `StatTile{label,value,unit?,sub?,tone?,accent?}`
- `DataTable<T>{columns:Column<T>[],rows,dense?,empty?,getKey?}`;`Column{key,header,align?,mono?,width?,render(row,i)}`
- `Chip{label,tone?,soft?}`、`StatusChip{value}`(自动配色+双语)、`GateDot{ok,label?}`、`ProgressBar{value,max?,tone?,height?}`、`KeyValue{items:{k,v}[],cols?}`、`EmptyState`
- 色调 `Tone = pos|neg|warn|info|accent|special|muted`;`statusTone(s)` 把状态串映射到色调;`deltaTone(v)`。

## 图表 API(`@/ui/charts`,纯 SVG,响应式)
- `Sparkline{data,tone?,height?,width?}` — data: number[] 或 {x,y}[]
- `AreaChart{data,height?,tone?,baselineAtZero?,yFormat?,xCount?}` — 权益/曲线
- `UnderwaterChart{data,height?,yFormat?}` — 回撤(0 下方负面积)
- `BarSeries{data:number[],height?,labels?,posTone?,negTone?,valueFormat?}` — 发散正负柱(月度收益等)
- `Histogram{bins:{label,count}[],height?,tone?}` — 分布
- `Funnel{stages:{label,value,tone?}[],height?}` — 转化漏斗
- `RadialGauge{value,max?,size?,label?,tone?}` — 评分弧(max=1 显示百分比)
- `Heatmap{ids:string[],matrix:number[][],size?}` — 相关性矩阵
- `Donut{segments:{label,value,tone?}[],size?}` — 构成环
- `DivergingBars{items:{label,value}[],height?,valueFormat?}` — 横向归因(正右负左)

## 每页强调点(结合各自 Payload 字段;均为只读快照)
- **AlphaFactory**: summary KPIs;candidates 表(ic/rank_ic/icir/sharpe/turnover/capacity money/composite,lifecycle StatusChip);screening.buckets→Donut;correlation_matrix→Heatmap;lineage 列表。
- **Experiments**: summary KPIs;stage_counts→Histogram;experiments 表(stage/decision Chip/metrics/reproducibility_hash mono);lineage_policy→GateDot 行。
- **DataCatalog**: summary KPIs;datasets 表(quality_score ProgressBar/coverage/freshness/point_in_time_safe GateDot);features 表;quality_policy→GateDot;lineage_edges 计数。
- **Validation**: selected_report 详情(decision Chip;gates_passed/total RadialGauge;oos_sharpe/oos_ic/net_sharpe 三 StatTile;六个门 leakage/walk_forward/multiple_testing/costs/capacity/decay→GateDot 行);reports 表。
- **BacktestExplainability**: equity_curve→AreaChart;drawdown_curve→UnderwaterChart;summary KPIs;monthly_returns→BarSeries;cost_breakdown→DivergingBars/Histogram;segments 表;drawdown_periods 表;trade_distribution→Histogram;regime_attribution 表;decision_drivers→DivergingBars(按 score,impact 配色);assumptions 列表。**保留 alphaId 选择器。**
- **ScenarioStress**: baseline 4 StatTile;summary;scenarios 卡/表(severity Chip/decision/metrics/curve Sparkline 或 AreaChart/drivers);exposure_buckets+risk_factor_contributions→DivergingBars;assumptions。**保留 alphaId。**
- **ModelRiskReview**: summary;models 表(risk_tier/review_status Chip,residual_risk ProgressBar,validation_scores 四维→小 RadialGauge 或 bars);validation_dimensions→bars;issue_register 表;governance_policy→GateDot。
- **EvidenceLedger**: root_hash mono;summary(hash_chain_valid GateDot 醒目);entries 表(sequence/source_module/content_hash mono/immutability_status/verification 四 GateDot);blockers;ledger_policy。强调哈希链完整。
- **ReviewerWorkflow**: summary;workload(utilization ProgressBar);tasks 表(severity/state/due_in_hours/blocks_live GateDot);approval_gates;evidence_links。
- **AccessControl**: summary;roles 卡(permissions 允许/denied_capabilities 拒绝);permission_matrix→布尔网格(GateDot);export_packages 表(contains_credentials/contains_live_orders 必须 false→GateDot);forbidden_capabilities 全红 GateDot;access_policy。
- **MonitoringDrift**: status_counts→Donut;summary;monitors 表(drift_score ProgressBar 对 warn/breach 阈值,status Chip);top_breaches;watchlist;category_counts→Histogram。
- **IncidentResponse**: severity_counts→Donut;summary;incidents 表(severity/state/sla_breached GateDot/age/due);routing_rules;runbooks;timeline 步进列表。
- **PromotionControl**: summary;decision_counts;promotions 表(readiness_score RadialGauge,gates pass/watch/fail,paper_replay/broker_test/live GateDot 全部受阻);gates 表;release_policy+safety_boundary。
- **ReleaseCommittee**: summary(quorum_met GateDot,committee_decision 醒目 NO_GO);seats;agenda 表;votes;required_attestations→GateDot;blockers;committee_policy。
- **Agents**: summary 4 StatTile;agents 表(state Chip/runs/approved/performance ProgressBar/budget money)。
- **PaperTrading**: run 详情(status Chip/final_equity money/total_trades/metrics StatTile);equity_curve→AreaChart(equity 字段);lineage。**保留 alphaId。**
- **Portfolio**: simulation.metrics→StatTile;equity_curve→AreaChart;drawdown_curve→UnderwaterChart;weights.weights→Donut 或 bar;risk_contributions+return_attribution→DivergingBars;analytics_method Chip;lineage。
- **Risk**: alpha 信息;primary_report(decision Chip,gates_passed/total RadialGauge,gate_results→GateDot 网格,reasons 列表);reports 表;lineage。**保留 alphaId。**
- **Events**: events 表(event_type Chip/timestamp stamp/source/correlation_id mono/payload 预览);可加 type 计数 Histogram。
- **ComputationLab**: **保留 alphaId/costBps/stress 控件 + fetchComputationLabSnapshot 调用与 abort**;alpha_candidates 选择;metrics→StatTile;equity_curve→AreaChart;drawdown_curve→UnderwaterChart;validation+risk_report;lineage。
- **Architecture**: current/target_level Chip;architecture_score RadialGauge;modules 表(maturity ProgressBar);data_flows 列表;gaps 表;safety_boundary.forbidden_capabilities 红 GateDot。
- **SystemCompleteness**: readiness_score RadialGauge;summary;modules 表(completion_score ProgressBar,frontend/backend status);checks→GateDot;gaps 表;roadmap 步进;safety_boundary。
- **Phase3AReadiness**: decision NO_GO 醒目横幅;approved_count/total RadialGauge;criteria 表(status Chip);blockers 表。
- **SystemHealth**: services 表(status Chip/latency_ms/error_rate);gates→GateDot;safety_checks 列表。

## 自检
重建后该页应:strict TS 通过、只用上述基元/图表、保留数据接线与 ProvenanceRibbon、双语、全幅栅格、无新依赖。
