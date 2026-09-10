import type {FrozenAccountTerms} from '@/lib/types/ai-factory';
import './passive-execution.css';

const names:Record<string,string>={commission:'成交费率',configuration:'账户配置',brackets:'保证金档位'};
const time=(value:number)=>Number.isFinite(new Date(value).getTime())?new Date(value).toISOString():'未知';

export function FrozenAccountEvidence({terms}:{terms:FrozenAccountTerms}) {
 return <section className="s-account-terms" role="region" aria-label="冻结账户条件">
 <div><strong>冻结账户条件 · {terms.symbol}</strong><p className="f-message">{terms.provenance==='SYNTHETIC_TEST_FIXTURE'?'合成验证材料':'导入材料，来源未认证'}。真实已付费用未知；以下条件只用于本次隔离研究账户。</p></div>
 <div className="s-account-terms-summary">
 <div><span>已选杠杆</span><strong>{terms.selected_leverage} 倍</strong></div>
 <div><span>模拟开仓限制</span><strong>新增风险名义价值 &lt; {terms.new_risk_notional_cap} {terms.quote}</strong><small>账户配置上限 {terms.symbol_config_notional_cap} {terms.quote}；同时受杠杆许可档及现有风险预算约束。</small></div>
 <div><span>冻结普通成交费率</span><strong>maker {terms.maker_fee_rate} · taker {terms.taker_fee_rate}</strong><small>比例值；RPI 费率{terms.rpi_fee_rate_not_applied==null?'未知':` ${terms.rpi_fee_rate_not_applied}`}，不用于普通成交。</small></div>
 </div>
 <div className="s-account-tier-grid">{terms.margin_brackets.map((row,i)=><article key={row.notional_floor}>
 <strong>档位 {i+1} · [{row.notional_floor}, {row.notional_cap}) {terms.quote}</strong>
 <p>最大初始杠杆 {row.maximum_initial_leverage} 倍</p>
 <dl><dt>模拟初始保证金率</dt><dd>{row.initial_margin_rate}</dd><dt>维持保证金率</dt><dd>{row.maintenance_margin_rate}</dd><dt>维持保证金扣减额</dt><dd>{row.maintenance_amount} {terms.quote}</dd></dl>
 </article>)}</div>
 <p className="f-message">初始保证金采用已选杠杆和档位最大杠杆中的较低者，倒数向上保留 24 位小数。超过表尾后的保证金仅作风险估计；已有持仓继续估值和减仓，不能因此增加开仓额度。强平费用仍为 1% 模拟假设。</p>
 <details><summary>查看来源与接收时间</summary>
 <p>前后响应一致不证明中间未改变。摘要用于核对输入，不能认证账户来源。未核实其他资产、仓位、挂单或费用折扣。</p>
 <p>账户引用摘要：<code>{terms.account_ref_sha256}</code></p><p>条件输入摘要：<code>{terms.input_hash}</code></p>
 {(['before','after'] as const).map(phase=><div key={phase}><strong>{phase==='before'?'回放前':'回放后'}</strong>
 {Object.entries(terms.observations[phase]).map(([kind,row])=><p key={kind}>{names[kind]??kind} · 请求 {time(row.requested_at_ms)} · 接收 {time(row.received_at_ms)}<br/><code>{row.response_sha256}</code></p>)}</div>)}
 </details>
 </section>;
}
