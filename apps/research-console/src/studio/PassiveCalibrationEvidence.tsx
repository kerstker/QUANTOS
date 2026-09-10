import {useState} from 'react';
import type {PassiveReceiptCalibration} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {Chart} from './Charts';
import './passive-execution.css';

const labels:Record<string,string>={INCOMPLETE_PREFIX_FITS:'校准段计算未完整完成',
 INCOMPLETE_PREFIX_RECEIPT_COVERAGE:'校准段有回执缺口',INSUFFICIENT_PREFIX_RECEIPTS:'校准段没有足够回执',
 AMBIGUOUS_PREFIX_FIT:'多组参数无法区分',INSUFFICIENT_MATCHED_PREFIX_EVENTS:'校准段没有足够配对事件',
 PROFILE_FROZEN_BEFORE_HELDOUT:'参数已按校准段冻结',HELDOUT_REPLAY_FAILED:'后段回放失败，原选择保留',
 HELDOUT_COMPARISON_FAILED:'后段比较失败，原选择保留',INCOMPLETE_HELDOUT_RECEIPT_COVERAGE:'留出段有回执缺口',
 INSUFFICIENT_HELDOUT_RECEIPTS:'留出段没有足够配对回执',HELDOUT_ERRORS_RECORDED_NOT_VENUE_ACCEPTANCE:'留出段误差已记录，场所校准尚未验收'};
const time=(ms:number)=>new Date(ms).toISOString();
const state:Record<string,string>={SENT:'等待回执',ACK:'已确认',PARTIALLY_FILLED:'部分成交',FILLED:'全部成交',
 REJECTED:'拒绝',CANCELLED:'撤销',EXPIRED:'到期'};

export function PassiveCalibrationEvidence({data}:{data?:PassiveReceiptCalibration|null}) {
 const [order,setOrder]=useState('');
 if(!data)return null;
 const tail=data.heldout;const chosen=tail?.rows.find(r=>r.order_id===order)??tail?.rows[0];
 const rows=chosen?.path.map(p=>({label:time(p.received_ms).slice(11,23),time:p.received_ms,observed:Number(p.observed_filled),simulated:Number(p.simulated_filled)}))??[];
 return <Panel title="被动成交 · 回执校准与留出比较" note="按实际接收时间划分窗口。先冻结参数，再读取后段误差；费用和盈利不参与参数选择。">
 <div className="s-execution-body">
 <p><strong>{labels[data.status]??data.status}</strong> · {data.selected_profile_ms===null?'当前未选择参数':`已冻结 ${data.selected_profile_ms} ms`}</p>
 <p className="f-message">{data.observed_provenance.kind==='SYNTHETIC_TEST_FIXTURE'?'合成测试轨迹，仅用于验证软件。':'导入订单回执，来源声明尚未独立验证。'} 真实场所校准尚未验收，不能据此采用低费率或宣称盈利。</p>
 <p>校准段 {time(data.window_start_ms)} — {time(data.cutoff_ms)}；留出段至 {time(data.window_end_ms)}。截止点之后才收到的回执只进入后段，较早的场所时间不会把它移回校准段。</p>
 <p className="f-message">{data.trials_attempted} 次校准尝试。订单、撤单与回执使用同一组固定延迟，无法据此识别三个独立延迟分布。多组同分时保持未选择。</p>
 <div className="s-table-wrap" role="region" aria-label="被动校准段参数比较" tabIndex={0}><table className="s-table"><thead><tr><th>延迟</th><th>状态</th><th>末端数量差 BTC</th><th>数量路径差 BTC·ms</th><th>配对事件数量差 BTC</th><th>场所时间差 ms</th><th>接收时间差 ms</th><th>未配对事件</th></tr></thead><tbody>{data.trials.map(t=><tr key={t.profile_ms}>
 <td>{t.profile_ms} ms{t.profile_ms===data.selected_profile_ms?' · 冻结':''}</td><td>{t.status==='COMPLETED'?'计算完成':`失败 · ${t.reason??'未知'}`}</td>
 <td>{t.fit?.final_quantity_absolute_error??'未知'}</td><td>{t.fit?.quantity_path_error_btc_ms??'未知'}</td><td>{t.fit?.matched_event_quantity_absolute_error??'未知'}</td>
 <td>{t.fit?.venue_time_absolute_error_ms??'未知'}</td><td>{t.fit?.receive_time_absolute_error_ms??'未知'}</td><td>{t.fit?.unmatched_events??'未知'}</td></tr>)}</tbody></table></div>
 <p className="f-message">数量路径差累计「成交数量差 × 持续毫秒数」。最终数量相同仍可能有执行误差；宽表可左右滚动查看。</p>
 {tail&&<>
 <div className="s-table-wrap s-account-costs" role="region" aria-label="被动留出段误差" tabIndex={0}><table className="s-table"><thead><tr><th>留出段指标</th><th>结果</th></tr></thead><tbody>
 <tr><td>配对订单 / 提交订单</td><td>{tail.paired_orders} / {tail.submitted_orders}</td></tr>
 <tr><td>末端数量差 BTC</td><td>{tail.final_quantity_absolute_error}</td></tr><tr><td>本段新增数量差 BTC</td><td>{tail.increment_quantity_absolute_error}</td></tr>
 <tr><td>继承的数量差 BTC</td><td>{tail.carry_quantity_absolute_error}</td></tr><tr><td>数量路径差 BTC·ms</td><td>{tail.quantity_path_error_btc_ms}</td></tr>
 <tr><td>场所时间差 ms</td><td>{tail.venue_time_absolute_error_ms}</td></tr><tr><td>接收时间差 ms</td><td>{tail.receive_time_absolute_error_ms}</td></tr>
 <tr><td>未配对事件</td><td>{tail.unmatched_events}</td></tr><tr><td>回执缺口</td><td>{tail.coverage_gaps.length}</td></tr></tbody></table></div>
 <p className="f-message">后段延续原订单与账户状态，继承误差单列。这是已导入历史的留出比较，不是新的自然前向样本；没有配对回执时，零误差不表示通过。</p>
 {!!tail.unmatched_orders.length&&<p>未配对订单：{tail.unmatched_orders.join('、')}</p>}
 {!!tail.unobserved_orders.length&&<p>未收到回执的订单：{tail.unobserved_orders.join('、')}</p>}
 {chosen&&<><label>留出订单 <select value={chosen.order_id} onChange={e=>setOrder(e.target.value)}>{tail.rows.map(r=><option key={r.order_id} value={r.order_id}>{r.order_id}</option>)}</select></label>
 <p>观测：{state[chosen.observed_state]??chosen.observed_state}{chosen.observed_censored?' · 窗口截尾':''}；模型：{state[chosen.simulated_state]??chosen.simulated_state}{chosen.simulated_censored?' · 窗口截尾':''}。</p>
 <Chart title="累计成交数量 · 回执与模型" kind="step" xAxis="time" rows={rows} series={[{key:'observed',label:'导入回执'},{key:'simulated',label:'模型回执'}]} unit="BTC"
  note={`按实际接收时间显示最近 ${rows.length} 个事件检查点，共 ${chosen.path_points_total} 个；点间成交数量保持不变。完整轨迹在研究材料中。`}/>
 <p className="f-message">展示前 {tail.rows.length} 个订单，共 {tail.order_rows_total} 个。全部订单都参与误差核算。</p></>}
 </>}
 </div></Panel>;
}
