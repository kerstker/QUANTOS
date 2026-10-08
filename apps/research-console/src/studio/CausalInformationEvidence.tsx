import type {CausalInformationEvidence as Evidence} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {fmt,stamp} from './data';

const states:Record<string,string>={WAITING_WINDOW:'等待冻结窗口开始',COLLECTING:'按自然时间采集',WINDOW_FINISHED:'窗口已结束',ERROR:'采集异常',READY_FOR_REGISTERED_RESEARCH:'完整面板已就绪',STOP_INCOMPLETE_CAUSAL_WINDOW:'窗口有缺口，停止实验',COMPLETED_TECHNICAL_ONLY:'技术比较已完成',STOP_STUDY_FAILED:'实验失败，记录已保留',STOP_PREVIOUS_STUDY_FAILED_OR_INTERRUPTED:'上次实验失败或中断，已停止'};
const sources:Record<string,string>={hourly:'小时行情',spot:'现货',perpetual:'永续',index:'指数',funding:'资金费',oi:'持仓量'};
const collectionStates:Record<string,string>={COMPLETE:'本小时采集完整',PARTIAL:'本小时部分采集成功',FAILED:'本小时采集失败',INCOMPLETE:'采集中断或未结束'};
const errors:Record<string,string>={TIMEOUT:'请求超时',TRANSPORT_ERROR:'网络连接失败',TLS_VERIFICATION_FAILED:'连接证书校验失败',DEADLINE_EXCEEDED:'已到本小时采集截止时间',INVALID_RESPONSE:'数据格式不符合要求',ATTEMPTS_EXHAUSTED:'有限尝试已用完',CHILD_PROCESS_FAILED:'采集进程异常',RESPONSE_TOO_LARGE:'响应超出数据上限',INVALID_CHILD_RESPONSE:'采集进程返回异常'};

export function CausalInformationEvidence({data,busy,complete,action}:{data?:Evidence|null;busy:boolean;complete:boolean;action:()=>void}) {
 const stale=!!data&&data.status!=='WINDOW_FINISHED'&&Date.now()-data.observed_at_ms>180000;
 return <Panel title="自然时间 · 信息采集与增量实验" note="固定 504 个小时样本与一个 OI 预热小时。输入在执行参考时点前留存，成熟后运行四组基差和六组资金费 / OI 对照。" action={<button className="s-button" disabled={busy||!complete} onClick={action}>{data?'更新信息窗口':'登记自然信息窗口'}</button>}>
 {!data?<p className="f-message">尚未登记。先在成本决策页完成 P0 对账，再登记未来信息窗口；需要研究工作者持续运行，过去缺失的小时不会补写。</p>:<>
 <p role="status">{states[data.status]??data.status} · 最近记录 {stamp(data.observed_at_ms,true)}{stale?' · 超过三分钟未更新，请检查研究工作者。':''}</p>
 <div className="f-metrics"><div><small>开始采集</small><strong>{stamp(data.start_ms,true)}</strong></div><div><small>标签最早全部成熟</small><strong>{stamp(data.mature_ms,true)}</strong></div><div><small>盈利结论</small><strong>尚未建立</strong></div></div>
 {data.reason&&<p className="f-message" role="alert">{data.reason}：{data.detail??'保留原窗口与失败回执，请检查运行记录。'}</p>}
 {data.collection&&<section aria-label="最近采集诊断"><p>{collectionStates[data.collection.status]??data.collection.status} · {stamp(data.collection.slot_ms,true)}</p>
 <div className="f-metrics"><div><small>请求尝试（含中断）</small><strong>{data.collection.request_attempts??'未记录'}</strong></div><div><small>重试后成功的数据源</small><strong>{data.collection.recovered_requests??'未记录'}</strong></div><div><small>本次采集耗时</small><strong>{data.collection.elapsed_ms==null?'未记录':`${fmt(data.collection.elapsed_ms/1000,2)} 秒`}</strong></div></div>
 {data.collection.failures.length>0&&<ul>{data.collection.failures.map((f,i)=><li key={`${f.quote}-${f.kind}-${i}`}>{f.quote} · {sources[f.kind??'']??'信息组装'}：{f.http_status===429?'交易所限流，已按等待时间限制后续请求':f.category==='HTTP_ERROR'?'交易所返回错误':errors[f.category??'']??f.reason??'原因未记录'}{f.http_status!=null?`（HTTP ${f.http_status}）`:''}{f.retry_after_seconds!=null?` · 要求等待 ${f.retry_after_seconds} 秒`:''}</li>)}</ul>}
 </section>}
 <div className="s-table-wrap" role="region" aria-label="分币种因果信息采集" tabIndex={0}><table className="s-table"><thead><tr><th>计价</th><th>可研究小时</th><th>已错过小时</th><th>状态</th></tr></thead><tbody>{data.quotes?.map(q=><tr key={q.quote}><td>{q.quote}</td><td><progress aria-label={`${q.quote} 采集进度`} value={q.ready_rows} max={q.required_rows}/><span> {q.ready_rows} / {q.required_rows}</span></td><td>{q.missing_slots.length? <details><summary>{q.missing_slots.length} 个缺口</summary>{q.missing_slots.slice(-24).map(t=><p key={t}>{stamp(t,true)}</p>)}{q.missing_slots.length>24&&<p>显示最近 24 个，完整列表在原始记录中。</p>}</details>:0}</td><td>{states[q.status]??q.status}</td></tr>)}</tbody></table></div>
 {([['USDC',data.study],['USDT',data.usdt_study]] as const).map(([quote,study])=>study&&<div key={quote}><p>{quote} · {states[study.status]??study.status} {study.reason}</p>{Object.entries(study.results??{}).map(([stage,result])=><div key={stage}><p>{stage} · 已记录 {result.trials_attempted} 次尝试 · 选择 #{result.selected_id} · {result.decision}</p><div className="s-table-wrap" role="region" aria-label={`${stage} 自然采集成本对照`} tabIndex={0}><table className="s-table"><thead><tr><th>候选</th><th>手续费 / 滑点 bp</th><th>净损益 {quote}</th><th>最大回撤</th><th>成交</th></tr></thead><tbody>{result.cases.map(c=><tr key={`${c.id}-${c.fee_bps}-${c.slippage_bps}`}><td>#{c.id}</td><td>{c.fee_bps} / {c.slippage_bps}</td><td>{fmt(c.metrics.net_pnl,4)}</td><td>{fmt(c.metrics.drawdown===undefined?undefined:c.metrics.drawdown*100)}%</td><td>{c.metrics.fills??'未知'}</td></tr>)}</tbody></table></div></div>)}</div>)}
 <p className="f-message">这是提前留存的特征，模型在标签成熟后研究拟合，不算提前发出的预测。USDC 与 USDT 分别使用对应计价的独立研究账户；不换算、不拼接收益，费用与场所规则均为固定研究情景。</p>
 </>}
 </Panel>;
}
