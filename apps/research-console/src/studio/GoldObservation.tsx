import {GoldDashboard} from './GoldDashboard';
import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import type {GoldObservationPayload} from '@/lib/types/research';
import {Empty,ErrorNote,Panel} from './components';
import {stamp,useResource} from './data';

const endpoint='/api/v1/console/gold-observation';
const marketLabels:Record<string,string>={NOT_CONNECTED:'尚无公开行情资料',DATA_BLOCKED:'行情资料校验失败',
 NO_DATA:'采集没有收到成交',INTERRUPTED:'采集已中断',STALE:'行情记录已过期',OBSERVED_RECENTLY:'近期收到的行情记录'};

const cashLabels:Record<string,string>={NOT_CONNECTED:'尚无资金流水资料',DATA_BLOCKED:'资金流水校验失败',
 STALE:'流水覆盖时间已过期',OBSERVED:'已读取资金流水资料'};
const checkLabels:Record<string,string>={NOT_AVAILABLE:'尚无可核对的资金资料',WAITING_FOR_FILLS:'等待有效成交资料',
 SCOPE_MISMATCH:'账户或覆盖窗口不一致',TOTALS_DIFFER:'成交与流水金额不符',
 INCOMPLETE_CATEGORIES:'核对类别缺失或存在未比较币种',DECLARED_TOTALS_MATCH:'已比较的声明总额一致，完整性未知'};

const signalLabels:Record<string,string>={NOT_CONNECTED:'尚无历史信号输入',DATA_BLOCKED:'历史信号资料校验失败',
 HISTORICAL_REPLAY:'已重算历史研究信号',INPUT_INCOMPLETE:'历史回放输入不完整'};

const linkLabels:Record<string,string>={NOT_CONNECTED:'尚无成交关联标注',WAITING_FOR_EVIDENCE:'等待有效成交及信号资料',
 DATA_BLOCKED:'关联资料或绑定校验失败',DECLARED_ASSOCIATIONS_ONLY:'仅为事后声明关联'};
const fillStatusLabels:Record<string,string>={DECLARED_LINK:'声明关联成交',UNLINKED:'未关联成交',
 UNKNOWN_ENTRY_SIGNAL:'未识别入场信号',TRADE_PRECEDES_SIGNAL:'成交早于信号',
 SAME_MILLISECOND_UNRESOLVED:'同毫秒先后未确定',DIRECTION_CONFLICT:'方向冲突'};

const monitorLabels:Record<string,string>={NOT_CONNECTED:'尚未接入监控资料',DATA_BLOCKED:'监控资料校验失败',
 EMPTY:'尚无事件日志',CONNECTING:'正在连接',CONNECTED:'采集器报告已连接',DISCONNECTED:'连接中断',
 STOPPED:'采集已停止',DATA_CONFLICT:'事件存在冲突',STALE:'监控状态已过期'};
const recoveryLabels:Record<string,string>={NOT_CHECKED:'尚未补录核对',INCOMPLETE:'补录未完成',
 BOUNDARY_ACTIVITY:'快照边界有账户活动，暂不能确认一致',CHANGED_DURING_READ:'两次历史查询结果不同',
 MISMATCH:'核对发现差异',OTHER_MARKETS_UNCHECKED:'其他品种持仓尚未核对',
 WINDOW_CHECKED:'已查询窗口的金额和数量核对一致',SNAPSHOT_CHANGED:'快照期间账户发生变化'};

export function GoldObservation() {
 const resource=useResource('gold-observation',signal=>fetchConsoleModuleSnapshot<GoldObservationPayload>(endpoint,[{endpoint}],signal),15000);
 const data=resource.error?null:resource.data;
 const usable=data?.snapshot_status==='REAL'||data?.snapshot_status==='STALE';
 const fills=usable?data?.fills:null;
 return <><ErrorNote error={resource.error} retry={resource.refresh}/><GoldDashboard data={data}/><details className="g-evidence"><summary>数据来源、历史资料与核对详情</summary><Panel title="黄金 · Binance XAUUSDT" note={data?.monitor?"每15秒刷新账户观察记录；采集状态与历史完整性分别显示。":"每15秒读取已保存的成交资料；账户实时连接尚未接通。"}>
  <p className="s-pad">账户监控：{monitorLabels[data?.monitor_status??'NOT_CONNECTED']}。</p>
  {data?.monitor&&<>
   <dl className="s-facts">
    <div><dt>状态更新时间</dt><dd>{stamp(data.monitor.observed_at_ms,true)}</dd></div>
    <div><dt>收到的事件 / 完整成交</dt><dd>{data.monitor.event_count} / {data.monitor.unique_trades}</dd></div>
    <div><dt>保留的历史缺口</dt><dd>{data.monitor.gap_count}</dd></div>
    <div><dt>补录成交 / 资金流水</dt><dd>{data.monitor.recovered_fills??'—'} / {data.monitor.income_records??'—'}</dd></div>
   </dl>
   <p className="s-pad">补录核对：{recoveryLabels[data.monitor.recovery_status]??'未知'}。历史完整性仍未确认。</p>
   <dl className="s-facts">{data.monitor.positions.map(p=><div key={p.side}><dt>{p.side} 持仓观察值</dt>
    <dd>{p.quantity} · 均价 {p.entry_price} · {stamp(p.observed_at_ms,true)}</dd></div>)}</dl>
   <p className="s-pad">持仓显示最近观察值；局部更新和历史缺口可能影响完整性。账户活动不自动归因为手动交易。</p>
   {data.monitor.recent_trades.length>0&&<table><caption>最近记录的成交（含补录）</caption>
    <thead><tr><th>时间</th><th>方向</th><th>价格</th><th>数量</th><th>手续费</th></tr></thead>
    <tbody>{data.monitor.recent_trades.map((t,i)=><tr key={i}><td>{stamp(t.time_ms,true)}</td><td>{t.side}</td>
     <td>{t.price}</td><td>{t.quantity}</td><td>{t.commission??'—'} {t.commission_asset??''}</td></tr>)}</tbody></table>}
  </>}
  <p className="s-pad">公开行情：{marketLabels[data?.market_status??'NOT_CONNECTED']}。读取保存的采集结果，不表示持续连接。</p>
  {data?.market&&<dl className="s-facts">
   <div><dt>最近记录的成交价</dt><dd>{data.market.last_trade_price??'—'}</dd></div>
   <div><dt>最近成交时间</dt><dd>{stamp(data.market.last_event_ms,true)}</dd></div>
   <div><dt>记录内聚合成交数</dt><dd>{data.market.unique_aggregates}</dd></div>
  </dl>}
  {!fills?<Empty title={data?.snapshot_status==='DATA_BLOCKED'?'成交资料未通过校验':'尚未导入黄金成交资料'}/>:<>
   <p className="s-pad">{data?.snapshot_status==='STALE'?'成交覆盖时间已过期，仅供历史查看':'已读取导入资料，来源真实性尚未核验'}</p>
   <dl className="s-facts">
    <div><dt>成交窗口（起点含、终点不含）</dt><dd>{stamp(fills.start_ms,true)} — {stamp(fills.end_ms,true)}</dd></div>
    <div><dt>导出时间</dt><dd>{stamp(fills.exported_at_ms,true)}</dd></div>
    <div><dt>去重后成交记录</dt><dd>{fills.unique_fills}</dd></div>
    <div><dt>去除重复记录</dt><dd>{fills.duplicate_fills}</dd></div>
   </dl>
  </>}
  <p className="s-pad">资金资料：{cashLabels[data?.cash_status??'NOT_CONNECTED']}。分项核对：{checkLabels[data?.cash_reconciliation_status??'NOT_AVAILABLE']}。</p>
  {data?.cash&&<dl className="s-facts">
   <div><dt>流水覆盖窗口</dt><dd>{stamp(data.cash.start_ms,true)} — {stamp(data.cash.end_ms,true)}</dd></div>
   <div><dt>去重后资金记录</dt><dd>{data.cash.unique_events}</dd></div>
   <div><dt>记录中的资金费（未独立对账）</dt><dd>{Object.entries(data.cash.funding_by_asset).map(([asset,value])=>`${value} ${asset}`).join(' / ')||'缺少资金费记录'}</dd></div>
  </dl>}
  <p className="s-pad">研究信号：{signalLabels[data?.signal_status??'NOT_CONNECTED']}。仅为历史重算，不是当前入场建议，也不证明当时收到过信号。</p>
  {data?.signal&&<dl className="s-facts">
   <div><dt>已重算的历史决策</dt><dd>{data.signal.decision_count}</dd></div>
   <div><dt>历史做多研究信号数</dt><dd>{data.signal.entry_signal_count}</dd></div>
   <div><dt>最后历史决策时间</dt><dd>{stamp(data.signal.last_decision_ms,true)}</dd></div>
  </dl>}
  <p className="s-pad">成交关联：{linkLabels[data?.links_status??'NOT_CONNECTED']}。基于当前代码重算，不证明策略采纳或收益归属。</p>
  {data?.links&&<dl className="s-facts">
   {Object.entries(data.links.fills_by_status).map(([status,count])=><div key={status}><dt>{fillStatusLabels[status]??status}</dt><dd>{count}</dd></div>)}
   <div><dt>声明入场 / 退出成交</dt><dd>{data.links.declared_entry_fills} / {data.links.declared_exit_fills}</dd></div>
   <div><dt>未匹配成交的标注</dt><dd>{data.links.orphan_annotations}</dd></div>
   <div><dt>没有声明入场成交的信号</dt><dd>{data.links.signals_without_declared_entry}</dd></div>
  </dl>}
  <p className="s-pad">{data?.monitor?"账户净盈亏与完整历史仍待确认。":"当前持仓与账户净盈亏未知；尚未完成资金费对账。"}</p>
 </Panel></details></>;
}
