import type {ProfitabilityAttribution as Attribution} from '@/lib/types/ai-factory';
import {Panel,Empty} from './components';
import {Chart} from './Charts';

export function ProfitabilityAttribution({data}:{data?:Attribution}) {
 const diagnosis:Record<string,string>={NO_TRADING_EVIDENCE:'没有成交，无法检验交易盈利能力',NO_POSITIVE_GROSS_EDGE:'本段毛收益未显示正优势',POSITIVE_GROSS_CONSUMED_BY_COSTS_OR_FUNDING:'毛收益为正，被成本或资金费消耗',POSITIVE_NET_TECHNICAL_RESULT:'本段技术回放净收益为正，仍需稳定性与前向验证'};
 if(!data||data.status!=='RECONCILED_TECHNICAL_ONLY')return <Panel title="盈利能力验证 · 亏损归因"><Empty title="完整成本归因尚不可用">需要完整成交与期末持仓回执。旧冻结结果不回写，不从末尾成交推测全部成本。{data?.reason&&` 原因：${data.reason}`}</Empty></Panel>;
 const rows=[{label:'参考毛收益',value:Number(data.gross_reference_pnl)},{label:'嵌入滑点',value:-Number(data.embedded_slippage)},{label:'手续费',value:-Number(data.fees)},{label:'显式点差/冲击',value:-Number(data.explicit_spread)-Number(data.explicit_impact)},{label:'资金费',value:Number(data.funding)},{label:'净收益',value:Number(data.net_pnl)}];
 return <>
 <Panel title="盈利能力验证 · 亏损归因" note="基于同一批成交数量的解释；没有重算仓位，也没有再次扣除滑点">
 <p className="f-message">{diagnosis[data.diagnosis]??'证据不足'}。预测因果优势：未知；真实场所费用：未知。</p>
 <div className="f-metrics">{[['参考毛收益',data.gross_reference_pnl],['净收益',data.net_pnl],['多头价格贡献',data.position_contribution?.long],['空头价格贡献',data.position_contribution?.short]].map(([name,value])=><div key={name}><small>{name} · USDC</small><strong>{value??'—'}</strong></div>)}</div>
 <p className="f-message">参考成交额 {data.turnover_notional} USDC · 换手 / 初始权益 {data.turnover_initial_equity} · 对账残差 {data.reconciliation_residual}。滑点包含价格取整，无法独立识别点差。有效独立样本量尚未建立。</p>
 </Panel>
 <Chart kind="bar" title="从毛收益到成本后结果" rows={rows} series={[{key:'value',label:'贡献'}]} unit="USDC" note="各项为独立贡献柱，最后一项是净收益总计；仅为技术回放，不能替代真实费用或盈利证据。"/>
 </>;
}
