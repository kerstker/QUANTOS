import type {ExecutionCalibration} from '@/lib/types/ai-factory';
import {Panel} from './components';

export function ExecutionCalibrationEvidence({data}:{data?:ExecutionCalibration|null}) {
 return <Panel title="固定动作 · 成交校准" note="三组固定延迟，仅根据截止时点之前的数量、时间与状态误差选择；后段误差不回传选择。">
 {!data?<p className="f-message">导入固定动作校准数据后运行。未导入真实回执时，场所校准保持未验收。</p>:<>
 <p>已记录 {data.trials_attempted} 次尝试 · 冻结候选 #{data.selected_profile}。来源尚未独立验真，不能据此采用 maker 低费率盈利假设。</p>
 <div className="s-table-wrap" role="region" aria-label="执行校准候选对照" tabIndex={0}><table className="s-table"><thead><tr><th>候选</th><th>订单延迟 ms</th><th>撤单延迟 ms</th><th>状态</th><th>数量误差</th><th>状态时间误差 ms</th><th>未配对事件</th></tr></thead><tbody>{data.trials.map(t=><tr key={t.profile}><td>#{t.profile}</td><td>{t.order_latency_ms}</td><td>{t.cancel_latency_ms}</td><td>{t.status==='COMPLETED'?'计算完成':t.reason??t.status}</td><td>{t.fit?.quantity_absolute_error??'未知'}</td><td>{t.fit?.state_time_absolute_error_ms??'未知'}</td><td>{t.fit?.unmatched_events??'未知'}</td></tr>)}</tbody></table></div>
 <p className="f-message">{data.heldout.paired_orders?`后段配对订单 ${data.heldout.paired_orders} · 数量误差 ${data.heldout.quantity_absolute_error} · 状态时间误差 ${data.heldout.state_time_absolute_error_ms} ms。`:'后段没有可比较的订单，不能把零误差当成校准通过。'} 公开盘口采样不建立队列位置或真实费用证据。</p>
 </>}
 </Panel>;
}
