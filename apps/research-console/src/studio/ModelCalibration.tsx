import type {LifecycleModel} from '@/lib/types/ai-factory-lifecycle';
import {Chart} from './Charts';
import {Panel,Empty} from './components';
import {fmt,stamp} from './data';

export function ModelCalibration({model}:{model:LifecycleModel|undefined}) {
 const d=model?.diagnostics;
 if(!d?.observations?.length)return <Empty title="等待真实成熟标签">预测终点行情实际到达后显示校准，不用历史回填或零值替代。</Empty>;
 const bins=d.calibration.map(b=>({label:`${b.lower===null?'−∞':fmt(b.lower*10000,0)} 至 ${b.upper===null?'+∞':fmt(b.upper*10000,0)} bp · ${b.count} 条`,prediction:b.prediction===null?undefined:b.prediction*10000,actual:b.actual===null?undefined:b.actual*10000}));
 return <>
 <Panel title="预测校准与误差" note={`诊断使用最近 ${d.count} 条成熟标签，总成熟标签 ${model?.labels} 条。四小时标签重叠；方向一致率不是交易胜率。`}>
 <dl className="lf-diagnostics"><div><dt>平均绝对误差</dt><dd>{fmt(d.mae===null?null:d.mae*10000,2)} bp</dd></div><div><dt>平均偏差（预测 − 实际）</dt><dd>{fmt(d.bias===null?null:d.bias*10000,2)} bp</dd></div><div><dt>方向一致率</dt><dd>{fmt(d.directional_accuracy===null?null:d.directional_accuracy*100,1)}{d.directional_accuracy===null?'':'%'}</dd><small>排除预测或实际为零 · {d.directional_count} 条</small></div></dl>
 </Panel>
 <Chart title="预测与实际四小时收益" rows={d.observations.map(r=>({label:stamp(r.time,true),time:r.time,prediction:r.prediction*10000,actual:r.actual*10000}))} series={[{key:'prediction',label:'冻结预测'},{key:'actual',label:'实际标签'}]} unit="bp" note="按原始预测时点排列，仅展示标签已成熟的窗口。"/>
 <Chart title="预测分组校准" rows={bins} series={[{key:'prediction',label:'组内平均预测'},{key:'actual',label:'组内平均实际'}]} kind="bar" unit="bp" note="固定分组，左闭右开；空组保留为空，不参与拟合，也不自动修改模型。"/>
 <Chart title="预测残差" rows={d.observations.map(r=>({label:stamp(r.time,true),time:r.time,residual:r.residual*10000}))} series={[{key:'residual',label:'实际 − 预测'}]} kind="bar" unit="bp" note="正值表示低估，负值表示高估；这是预测误差，不是账户损益。"/>
 </>;
}
