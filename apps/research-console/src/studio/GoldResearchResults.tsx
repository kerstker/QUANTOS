import type {GoldResearchResult} from '@/lib/types/ai-factory-lifecycle';
import {stamp} from './data';
import {Panel,Badge} from './components';

const outcomes:Record<string,string>={NO_SIGNAL:'未产生信号',OPEN_AT_END:'结束时仍有持仓或挂单',FLAT_TERMINAL:'实验结束时已平仓'};
const costs:Record<string,string>={initial_equity:'初始权益',ending_equity:'结束权益',attributed_change:'成本后权益变化',price_pnl:'价格损益',fee:'手续费',spread:'价差成本',impact:'冲击成本',funding:'资金费',liquidation_fee:'清算费用',insurance_adjustment:'保险调整',adl_adjustment:'自动减仓调整',residual:'对账残差'};
export function GoldResearchResults({rows}:{rows:GoldResearchResult[]}){
 if(!rows.length)return null;
 return <Panel title="黄金研究结果" note="最近研究记录 · 技术回放金额，不是账户实际收益；各轮使用其固定数据与成本规则">
 {rows.map(row=><details className="s-evidence" id={`gold-result-${row.attempt}`} key={row.attempt}>
 <summary>黄金研究 #{row.attempt} · <Badge value={row.state}/> · {row.outcome?(outcomes[row.outcome]??row.outcome):row.state==='FAILED'?'实验失败，未生成可用结果':row.report_status==='UNAVAILABLE'?'原报告不可用':'等待实验结果'}</summary>
 {row.candidate&&<p>候选 #{row.candidate.id}：卖出流量阈值 {row.candidate.sell_threshold}，连续 {row.candidate.confirm_windows} 个窗口确认吸收。</p>}
 {row.trial_id&&<p>试验记录：<code>{row.trial_id}</code></p>}
 {row.costs&&<dl className="s-chart-footer">{Object.entries(costs).filter(([key])=>row.costs?.[key]!==undefined).map(([key,label])=><div key={key}><dt>{label}</dt><dd>{row.costs![key]}</dd></div>)}</dl>}
 {row.latest_review&&<p>最新核验：{({INDEPENDENT_AUTHORITY_UNCONFIGURED:'独立核验权威未配置',VERIFIED_SIGNATURE_ENGINEERING_ONLY:'签名核对通过 · 仅工程证据',REJECTED:'核验未通过',PENDING_RECHECK:'收据或登记已变化，等待重新核验',UNAVAILABLE:'核验记录不可用'} as Record<string,string>)[row.latest_review.status]??row.latest_review.status} · {stamp(row.latest_review.checked_at_ms,true)}</p>}
 {row.recorded_verification_status&&<p>完成时记录的独立核验状态：<code>{row.recorded_verification_status}</code>。当前准入仍需重新核验。</p>}
 {row.execution_review&&<section aria-label={`研究 ${row.attempt} 的执行反馈`}>
 <h4>Demo 复盘 → 下一轮研究</h4>
 {row.execution_review.status==='NO_RECORD'&&<p>尚未收到与本试验绑定的执行复盘；无法确认 Demo 执行情况。</p>}
 {row.execution_review.status==='UNAVAILABLE'&&<p>执行复盘资料不可用，暂不显示闭环进度。</p>}
 {row.execution_review.records.map(record=><article key={record.feedback_sha256}>
 <p>{record.schema==='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1'?'整轮开平仓复盘':'单批执行复盘'} · {record.status==='EXECUTION_REVIEW_AVAILABLE'?'已达到研究触发时间':'等待研究触发时间'} · {stamp(record.available_at_ms,true)}</p>
 <p>反馈记录：<code>{record.feedback_sha256}</code></p>
 {record.next_attempts.length?<p>已关联后续研究：{record.next_attempts.map(next=><span key={next.attempt}><a href={`#gold-result-${next.attempt}`}>#{next.attempt}</a> <Badge value={next.state}/> </span>)}</p>:<p>尚无后续研究记录；仍需符合计划间隔和预算。</p>}
 <p className="f-muted">技术复盘资料，来源尚未认证，不能据此确认真实 Demo 成交或账户收益。</p>
 </article>)}
 </section>}
 {row.outcome==='NO_SIGNAL'&&<p>本轮没有产生交易信号，不能据此认定策略盈利或通过独立验证。</p>}
 <p className="f-muted">尚未获得经济准入；研究完成不会自动升级为交易许可。</p>
 </details>)}
 </Panel>;
}
