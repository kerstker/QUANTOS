import {useId,useState} from 'react';
import type {FactoryPayload} from '@/lib/types/ai-factory';
import {Panel,Empty,Badge} from './components';
import {Chart} from './Charts';
import {fmt,stamp,statusLabel} from './data';
import {ProfitabilityAttribution} from './ProfitabilityAttribution';
import {ProfitabilityStudy} from './ProfitabilityStudy';
import {ProfitabilityInputUpload} from './ProfitabilityInputUpload';
import {ExecutionCalibrationEvidence} from './ExecutionCalibrationEvidence';
import {ResearchNotes} from './ResearchNotes';
import {PortfolioForward} from './PortfolioForward';
import {ForwardCostEvidence} from './ForwardCostEvidence';
import {ProfitabilityReadiness} from './ProfitabilityReadiness';
import {CausalInformationEvidence} from './CausalInformationEvidence';
import {PortfolioResearch} from './PortfolioResearch';

export function FactoryEvidence({data,busy,action,importInput}:{data:FactoryPayload|null;busy:boolean;action:(name:string)=>void;importInput?:(kind:string,content:string)=>Promise<void>}) {
 const workflow=data?.workflow;
 const stages:Record<string,string>={RESEARCH:'主研究',POSTPROCESSING:'后续处理',PROCESS_COMPARISON:'完整过程对照',INDEPENDENT_PACKAGE:'验证包准备',NATURAL_OBSERVATION:'自然时间观察',PORTFOLIO_OBSERVATION:'组合前向观察',ASSESSMENT:'压力与组合诊断',PORTFOLIO_RESEARCH:'组合内层选择',COMPARISON:'完整过程对照',VERIFICATION:'验证包准备',FORWARD:'自然时间观察',CHECKPOINT:'保存检查点',RESEARCH_EXPLANATION:'证据结论',PORTFOLIO_FORWARD:'组合前向观察',COMPLETED:'处理结束',RESULT_REGISTRATION:'结果登记'};
 const reasons:Record<string,string>={RUNNER_FAILED:'执行器失败或中断，可恢复流程；已有实验记录保留。',WORKFLOW_INCOMPLETE:'执行器已结束，流程尚未完整完成，可继续恢复。',RUNNER_EVIDENCE_UNAVAILABLE:'尚无可核实的执行器记录，暂不能确认整条流程状态。'};
 const tabs=['运行与证据','前向观察','成本决策','压力与组合','过程对照','执行与数据','前向账户'];
 const [tab,setTab]=useState(0),id=useId();
 const forward=data?.forward,assessment=data?.assessment,complete=data?.selected?.state==='COMPLETED';
 const execution=data?.selected?.result.outer?.execution_feedback;
 const trace=data?.selected?.result.outer?.decision_trace??[];
 const resident=data?.resident,residentStale=!!resident&&Date.now()-resident.observed_at_ms>180000;
 return <>
 <div className="f-evidence-nav" role="tablist" aria-label="研究证据工作区">{tabs.map((name,i)=><button key={name} role="tab" id={`${id}-tab-${i}`} aria-controls={`${id}-panel`} aria-selected={tab===i} tabIndex={tab===i?0:-1} onClick={()=>setTab(i)} onKeyDown={event=>{let next=i;if(event.key==='ArrowRight')next=(i+1)%tabs.length;else if(event.key==='ArrowLeft')next=(i+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();setTab(next);document.getElementById(`${id}-tab-${next}`)?.focus();}}>{name}</button>)}</div>
 <div className="f-evidence-stack" role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${tab}`} tabIndex={0}>
 {tab===0&&<><ResearchNotes data={data?.research_notes} busy={busy} complete={complete} action={()=>action('explain')}/>
 <Panel title="常驻研究观察" note="此状态只覆盖当前状态根中的小时观察工作者"><div className="f-toolbar"><Badge value={residentStale?'STALE':resident?.status??'NOT_CONFIGURED'}/><span>最近心跳 {stamp(resident?.observed_at_ms,true)}</span><span>检查点 {resident?.checkpoint?.status?statusLabel(resident.checkpoint.status):'尚无记录'}</span></div><p className="f-message">{residentStale?'工作者超过三分钟未更新，请查看运行日志。':'研究状态与正式交易服务分开显示；没有心跳时不推断后台正在运行。'}</p></Panel>
 {resident?.checkpoint?.status==='RETRY_REQUIRED'&&<p className="f-message" role="alert">检查点校验或发布失败，本次恢复未验收。请查看运行日志并处理存储问题；后台下一轮会重新校验。保留的下载文件不能作为本次备份成功的依据。</p>}
 <Panel title="自动化流程" note="研究完成后自动进行六种压力诊断、两次共享账户对照、三折完整过程比较、验证包准备与前向激活"><div className="f-toolbar"><Badge value={workflow?.status??'UNKNOWN'}/><span>{stages[workflow?.stage??'']??workflow?.stage??'实验 → 诊断 → 验证包 → 观察'}</span><button className="s-button" disabled={busy||!complete||!workflow?.can_resume} onClick={()=>action('resume')}>恢复后续流程</button></div><p className="f-message">{reasons[workflow?.reason??'']} {workflow?.run_id&&`Runner：${workflow.runner_status} · ${workflow.run_id}`}。流程完成表示研究处理结束，前向观察仍按自然时间持续积累。</p></Panel>
 <Panel title="来源读取记录" note="仅抓取所选公开方法页；拒绝重定向，无法读取时如实记录，旧研究不补造来源">
 {data?.source_capture?<><p className="f-message">读取时间 {stamp(data.source_capture.captured_at_ms,true)} · 当前公开资料，不代表历史当时可知</p><div className="s-table-wrap" role="region" aria-label="来源读取状态" tabIndex={0}><table className="s-table"><thead><tr><th>来源</th><th>读取状态</th><th>内容摘要身份 / 原因</th></tr></thead><tbody>{data.source_capture.records.map(r=><tr key={r.source_id}><td>{r.source_id}</td><td>{r.status==='CAPTURED_PUBLIC_TEXT'?'已获取公开文本':'无法读取'}</td><td>{r.content_sha256?.slice(0,16)??r.reason}</td></tr>)}</tbody></table></div></>:<p className="f-message">此版本没有来源抓取回执；方法来源清单可在 Alpha 生命周期工作区查看。</p>}
 </Panel>
 <Panel title="AI 工具调用" note="模型只接收方法来源和内层反馈；这里展示实际回执，未提供的用量保持未知">
 <div className="s-table-wrap" role="region" aria-label="AI 调用回执" tabIndex={0}><table className="s-table"><thead><tr><th>实验</th><th>提案模型</th><th>耗时 ms</th><th>Token</th><th>协议与分歧</th><th>请求身份</th></tr></thead><tbody>{data?.trials.map(t=><tr key={t.ordinal}><td>#{t.ordinal+1}</td><td>{t.proposal?.model??'未形成提案回执'}</td><td>{t.proposal?.elapsed_ms??'—'}</td><td>{t.proposal?.usage?.total_tokens??'—'}</td><td>{t.proposal?.roles?<details><summary>{t.proposal.roles.length} 次调用 · {t.proposal.disagreement?'存在分歧':'一致'}</summary>{t.proposal.roles.map(r=><p key={r.role}>{({PROPOSE:'提案',COUNTER_PROPOSE:'反证',SYNTHESIZE:'综合'} as Record<string,string>)[r.role]??r.role} → 候选 #{r.candidate_id} · {r.elapsed_ms??'—'} ms</p>)}</details>:'单次 / 旧版回执'}</td><td>{t.proposal?.request_hash?.slice(0,16)??'—'}</td></tr>)}</tbody></table></div>
 </Panel>
 <Panel title="实验谱系" note="方法来源、父实验和数据截止固定在每次试验记录中"><div className="s-table-wrap" role="region" aria-label="实验谱系" tabIndex={0}><table className="s-table"><thead><tr><th>实验</th><th>父实验数</th><th>评价范围</th><th>标签截止</th><th>数据摘要</th></tr></thead><tbody>{data?.trials.map(t=><tr key={t.ordinal}><td>#{t.ordinal+1}</td><td>{t.hypothesis?.parent_trial_ids.length??'—'}</td><td>{t.hypothesis?.evaluation_scope??'旧版本未记录'}</td><td>{stamp(t.hypothesis?.evaluation_cutoff_ms,true)}</td><td>{t.hypothesis?.dataset_hash.slice(0,12)??'—'}</td></tr>)}</tbody></table></div></Panel>
 <Panel title="独立验证与证据包" note="绑定数据、模型、结果及完整实验账本，保留独立否定入口" action={<button className="s-button" disabled={busy||!complete} onClick={()=>action('verify')}>准备 / 检查验证包</button>}><p className="f-message">{data?.verification?.verification.status??'尚未准备独立验证包'}</p><p className="f-message">{data?.verification?.artifact_sha256&&`包摘要：${data.verification.artifact_sha256}`} · 经济准入未开放</p></Panel>
 </>}
 {tab===1&&<>
 <Panel title="自然时间前向" note="冻结模型，每小时生成预测；四小时标签收到后追加，原预测不改写" action={<div className="f-toolbar"><button className="s-button" disabled={busy||!complete} onClick={()=>action('observe')}>启用观察</button><button className="s-button" disabled={busy||!forward} onClick={()=>action('stop_observe')}>停止观察</button><button className="s-button" disabled={busy||!forward} onClick={()=>action('refresh_observe')}>推进一次</button></div>}>
 {forward?<><div className="f-toolbar"><Badge value={forward.status}/><span>激活 {stamp(forward.activated_at_ms,true)}</span><span>更新 {stamp(forward.observed_at_ms,true)}</span></div><div className="f-metrics"><div><small>已发布预测</small><strong>{forward.predictions??'—'}</strong></div><div><small>成熟标签</small><strong>{forward.labels??'—'}</strong></div><div><small>前向 RMSE</small><strong>{fmt(forward.diagnostics?.rmse,5)}</strong></div><div><small>漂移观察</small><strong style={{fontSize:13}}>{forward.diagnostics?.drift??'UNKNOWN'}</strong></div></div><p className="f-message">仅预测观察，没有账户收益。四小时重叠标签不能视作独立样本。</p></>:<Empty title="模型尚未激活">完成研究后启用观察；现有两分钟调度会在有效决策窗口推进。</Empty>}
 </Panel>
 <Panel title="最新预测" note="参考目标按空仓阈值政策展示，不代表实际账户持仓或订单">
 <div className="s-table-wrap" role="region" aria-label="已发布的小时预测" tabIndex={0}><table className="s-table"><thead><tr><th>信息时点</th><th>发布时点</th><th>预测 bp</th><th>参考目标</th><th>目标终点</th></tr></thead><tbody>{forward?.recent_forecasts?.slice(-12).reverse().map(r=><tr key={r.slot_ms}><td>{stamp(r.slot_ms,true)}</td><td>{stamp(r.issued_at_ms,true)}</td><td>{fmt(r.prediction*10000)}</td><td>{fmt(r.target_weight*100)}%</td><td>{stamp(r.exit_ms,true)}</td></tr>)}</tbody></table></div>
 </Panel>
 <Chart title="预测与实际 · 成熟前向标签" rows={(forward?.recent_labels??[]).map(r=>({label:stamp(r.slot_ms,true),time:r.slot_ms,prediction:r.prediction*100,realized:r.realized_return*100}))} series={[{key:'prediction',label:'事前预测'},{key:'realized',label:'成熟实际收益'}]} unit="%" note="标签在实际收到终点参考行情后成熟。"/>
 </>}
 {tab===2&&<>
 <ProfitabilityStudy data={data?.profitability} busy={busy} complete={complete} action={()=>action('profitability')}/>
 <ProfitabilityReadiness study={data?.profitability} forward={data?.forward_profitability}/>
 {!data?.profitability?.p0&&<ProfitabilityAttribution data={execution?.profitability_attribution}/>}
 <Panel title="执行与现金流归因" note="直接读取唯一账本；这里是技术回放，尚无真实场所成交验收">
 {execution?<><div className="f-metrics">{[['价格损益','price_pnl'],['费用','fee'],['资金费','funding'],['对账残差','residual']].map(([label,key])=><div key={key}><small>{label} · USDC</small><strong>{execution.reconciliation[key]??'—'}</strong></div>)}</div><p className="f-message">订单记录 {execution.order_count} · 成交 {execution.fill_count} · 平均敞口 {fmt(execution.average_exposure,3)}。滑点已计入成交价格，不重复扣费。</p><div className="s-table-wrap" role="region" aria-label="技术成交回执" tabIndex={0}><table className="s-table"><thead><tr><th>时间</th><th>数量</th><th>参考价 → 成交价</th><th>费用</th><th>原因</th></tr></thead><tbody>{execution.recent_fills.map((f,i)=><tr key={i}><td>{stamp(f.time,true)}</td><td>{f.quantity}</td><td>{f.reference} → {f.price}</td><td>{f.fee}</td><td>{f.reason}</td></tr>)}</tbody></table></div></>:<Empty title="此版本尚无执行归因回执">新研究会保留账本现金流与末尾成交记录；原有冻结结果不回写。</Empty>}
 </Panel>
 <Panel title="预测如何成为持仓" note="使用账本真实持仓、费用、滑点、资金费和风险项；仅展示前 128 个决策">
 <div className="s-table-wrap" role="region" aria-label="成本决策明细" tabIndex={0}><table className="s-table"><thead><tr><th>时点</th><th>预测 bp</th><th>当前 → 目标</th><th>交易成本 bp</th><th>风险项 bp</th><th>资金费 bp</th><th>原因</th></tr></thead><tbody>{trace.map(r=><tr key={r.time}><td>{stamp(r.time,true)}</td><td>{fmt(r.prediction*10000)}</td><td>{fmt(r.current_weight*100)} → {fmt(r.weight*100)}%</td><td>{fmt(r.trade_cost*10000)}</td><td>{fmt(r.risk_penalty*10000)}</td><td>{fmt(r.funding_cost*10000)}</td><td>{r.reason==='HOLD_COST_BAND'?'持有 · 降低换手':r.reason==='NET_UTILITY'?'净效用较高':'无优势 / 降低风险'}</td></tr>)}</tbody></table></div>{!trace.length&&<p className="f-message">此版本尚无成本决策明细；旧冻结模型保持原政策。</p>}
 </Panel>
 </>}
 {tab===3&&<>
 <PortfolioResearch data={data?.portfolio_research} busy={busy} complete={complete} action={()=>action('portfolio')}/>
 <Panel title="压力测试与共享账户" note="固定模型的后选择诊断，不回传 AI，也不建立晋级证据" action={<button className="s-button" disabled={busy||!complete||!!assessment} onClick={()=>action('assess')}>运行诊断</button>}>
 {assessment?<><div className="s-table-wrap" role="region" aria-label="压力测试结果" tabIndex={0}><table className="s-table"><thead><tr><th>滑点 bp</th><th>费用 bp</th><th>额外延迟</th><th>净损益 USDC</th><th>最大回撤</th><th>成交数</th></tr></thead><tbody>{assessment.cases.map(c=><tr key={c.case}><td>{c.slippage_bps}</td><td>{c.fee_bps}</td><td>{c.delay_hours} 小时</td><td>{fmt(c.metrics.net_pnl)}</td><td>{fmt((c.metrics.drawdown??0)*100)}%</td><td>{c.metrics.fills}</td></tr>)}</tbody></table></div><div className="f-metrics"><div><small>组合加入净损益增量</small><strong>{fmt(assessment.portfolio.add_net_pnl_delta)}</strong></div><div><small>替换净损益增量</small><strong>{fmt(assessment.portfolio.replace_net_pnl_delta)}</strong></div><div><small>搜索重采样尾部比例</small><strong>{fmt(assessment.search_diagnostics.family_tail_fraction,3)}</strong></div><div><small>记录尝试次数</small><strong>{assessment.search_diagnostics.trials_attempted??'—'}</strong></div></div><p className="f-message">组合使用同一账户和真实账本，等权合并两模型预测；不是独立资金曲线相加。重采样保留七日块，但不能认证自适应搜索。</p></>:<Empty title="尚未进行压力与组合诊断">在同一输入上重放六种费用、滑点和延迟情景，并比较加入与替换模型后的共享账户结果。</Empty>}
 </Panel>
 {assessment&&<p className="f-message">预测相关性 {fmt(assessment.portfolio.prediction_correlation,3)} · 方向分歧 {fmt(assessment.portfolio.prediction_disagreement_fraction===undefined?undefined:assessment.portfolio.prediction_disagreement_fraction*100)}%。共享资金回放不代表已有两个合格策略；容量仍为冻结情景。</p>}
 {assessment?.portfolio.add&&<Chart title="共享账户 · 加入与替换" rows={assessment.portfolio.add.equity.map((r,i)=>({label:stamp(r.time,true),time:r.time,add:Number(r.equity),replace:Number(assessment.portfolio.replace?.equity[i]?.equity)}))} series={[{key:'add',label:'等权预测组合'},{key:'replace',label:'替换模型'}]} unit="USDC" note="各情景初始资金均为 1,500 USDC；所有结果为后选择技术诊断。"/>}
 </>}
 {tab===4&&<>
 <Panel title="三折滚动 · 完整研究过程对照" note="每折重新训练、内层选择后再看该折外层；比较固定规格、随机、单次 AI 与三角色；相同候选、成本和风险预算" action={<button className="s-button" disabled={busy||!complete||data?.comparison?.schema==='QUANTOS_AI_FACTORY_PROCESS_COMPARISON_V3'} onClick={()=>action('compare')}>运行过程对照</button>}>
 <div className="s-table-wrap" role="region" aria-label="完整过程对照" tabIndex={0}><table className="s-table"><thead><tr><th>时间折</th><th>搜索组</th><th>尝试次数</th><th>模型调用 / 秒 / Token</th><th>所选模型</th><th>单模型外层净损益</th><th>组合尝试</th><th>组合外层净损益</th><th>状态</th></tr></thead><tbody>{data?.comparison?.rows.map(r=><tr key={`${r.fold}-${r.group}`}><td>{r.fold+1}</td><td>{({baseline:'固定顺序',random:'随机搜索',ai:'单次 AI',committee:'三角色'} as Record<string,string>)[r.group]??r.group}</td><td>{r.trials.length}</td><td>{r.model_calls??'—'} / {r.model_elapsed_ms==null?'—':fmt(r.model_elapsed_ms/1000,1)} / {r.model_total_tokens??'—'}</td><td>{r.selected?.model??'—'}</td><td>{fmt(r.outer?.net_pnl)}</td><td>{r.portfolio?.inner_trials_attempted??'旧版未运行'}</td><td>{fmt(r.portfolio?.comparisons?.[0]?.portfolio_net_pnl)}</td><td>{r.status}</td></tr>)}</tbody></table></div><p className="f-message">AI 组：{data?.comparison?.ai_group_status??'等待运行'}。历史过程对照不会恢复已使用样本的独立性。</p>
 </Panel>
 </>}
 {tab===5&&<><CausalInformationEvidence data={data?.profitability?.causal} busy={busy} complete={complete&&data?.profitability?.p0?.decision==='P1_BOUNDED_INNER_COMPARISON_ALLOWED'} action={()=>action('information')}/><ExecutionCalibrationEvidence data={data?.profitability?.p5_calibration}/>{importInput&&<ProfitabilityInputUpload busy={busy} complete={complete} versions={data?.profitability?.inputs} onImport={importInput}/>}</>}
 {tab===6&&<><PortfolioForward data={data?.portfolio_research} busy={busy} action={action}/><div className="f-toolbar"><button className="s-button" disabled={busy||!complete} onClick={()=>action('forward_cost')}>登记 / 更新前向账户</button><span>已登记的策略、窗口和成本规则保持冻结。</span></div><ForwardCostEvidence data={data?.profitability?.p6}/></>}
 </div>
 </>;
}
