import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import type {ResearchAssessmentPayload} from '@/lib/types/research';
import {Empty,ErrorNote,Header,Loading,Panel} from './components';
import {useResource} from './data';
const endpoint='/api/v1/console/research-assessment';
const names:Record<string,string>={data_trust:'数据可信',backtest_correctness:'回测正确性',experiment_validity:'实验有效性',profitability_evidence:'盈利证据',forward_and_testnet_evidence:'前向与测试网',research_efficiency:'研究效率'};
const labels:Record<string,string>={PASS:'已通过',PARTIAL:'部分证据',FAIL:'未通过',REJECTED:'已拒绝',UNKNOWN:'尚无证据',INSUFFICIENT:'证据不足'};
const gaps:Record<string,string>={data_trust:'仍需独立核验原始数据的可知时间与缺口。',backtest_correctness:'已保存账目不替代原始行情重放和真实成本校准。',experiment_validity:'仍需独立登记与未见样本外验证。',profitability_evidence:'已见历史结果不能证明独立盈利能力。',forward_and_testnet_evidence:'此批资料没有自然前向或测试网逐单对账。',research_efficiency:'案例耗时不包含完整加载、内存和性能预算。'};
export function ResearchAssessment(){
 const r=useResource('research-assessment',s=>fetchConsoleModuleSnapshot<ResearchAssessmentPayload>(endpoint,[{endpoint}],s),60000);
 const report=r.error?null:r.data?.assessment;
 const allPassed=Boolean(report&&report.dimensions.length>0&&report.dimensions.every(d=>d.verdict==='PASS'));
 return <><Header eyebrow="RESEARCH / EVIDENCE" title="研究验收">逐项查看研究证据与缺口；延期事项不计为通过。</Header><ErrorNote error={r.error} retry={r.refresh}/>
 {r.loading&&!r.data?<Loading/>:!report?<Empty title={r.data?.snapshot_status==='DATA_BLOCKED'?'研究资料校验失败':'尚未接入研究评估资料'}/>:<>
 <Panel title="当前结论"><p className="s-pad">{allPassed?'当前核验维度均已记录通过，仍须独立复核与人工审批方可进入后续阶段。':'研究证据尚不完整，未获得交易资格。以下为保存的历史实验资料核对结果。'}</p></Panel>
 <div className="s-analytics-grid">{report.dimensions.map(d=><Panel key={d.id} title={names[d.id]??d.id}><p className="s-pad"><strong>{labels[d.verdict]??'未知状态'}</strong></p><p className="s-pad">{gaps[d.id]}</p></Panel>)}</div>
 {report.forward_observation&&<Panel title="14天自然前向观察"><p className="s-pad">固定实验 RF3 · BTCUSDC（日线）</p><p className="s-pad">{report.forward_observation.status==='OBSERVING'?'已登记，按固定窗口观察':'观察资料缺失或存在缺口'} · 已发布 {report.forward_observation.published?.length??'未知'} / 14 · 缺口 {report.forward_observation.gaps?.length??'未知'}</p><div className="s-analytics-grid">{report.forward_observation.slots?.map(s=><div className="s-pad" key={s.index}><strong>{new Date(s.slot_ms).toLocaleDateString('zh-CN')}</strong><p>{s.published?'已发布':s.missed?'错过窗口，禁止补写':'等待窗口'} · 请求 {s.attempts} 次</p></div>)}</div><p className="s-pad">此观察独立于上方历史实验评分。14天结束后仍须核验资料与成本，不自动获得交易资格。</p></Panel>}
 <Panel title="实验与范围"><dl className="s-facts"><div><dt>已完成计算</dt><dd>{report.run_evidence?`${report.run_evidence.completed_cases} / ${report.run_evidence.planned_trials}`:'未接入'}</dd></div><div><dt>计算失败</dt><dd>{report.run_evidence?.failed_cases??'未知'}</dd></div><div><dt>核心要求</dt><dd>{report.counts.required_research_core}</dd></div><div><dt>缩小范围核验</dt><dd>{report.counts.adapted_scoped_check}</dd></div><div><dt>延期机构要求</dt><dd>{report.counts.deferred_institutional_requirement}</dd></div></dl><p className="s-pad">完成计算不等于策略通过。所有候选及失败仍保留在实验归档中。</p></Panel>
 </> }</>;
}
