import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import {Empty,ErrorNote,Panel} from './components';
import {stamp,useResource} from './data';

export type KrakenPortfolioPayload = {
 snapshot_status:string;
 submission_enabled:false;
 authoritative:false;
 observed_at_ms:number|null;
 session_status:'REFRESH_IN_PROGRESS'|'FAILED'|'OBSERVATION_COMPLETE'|null;
 portfolio_status:string|null;
 stages:{id:string;status:string}[];
 blockers:string[];
};
const endpoint='/api/v1/console/kraken-portfolio';
const names:Record<string,string>={market:'公开行情',orders:'订单恢复',cash:'现金对账',fees:'账户费用',portfolio:'组合规划'};
const labels:Record<string,string>={
 CAPTURED:'已采集',NO_TRACKED_ORDERS:'无待跟踪订单',ORDERS_OBSERVED:'已观察订单',
 BALANCE_MOVEMENTS_MATCHED:'余额变动已核对',BASELINE_ONLY:'已建立基线，等待下轮对账',
 OBSERVED_FEE_SCHEDULES:'费率已更新',WAITING_POLICY:'等待共同确定策略',
 REVIEW_ONLY:'规划可审阅',BLOCKED:'存在未解决事项',UNRESOLVED:'未解决',
 NOT_OBSERVED:'尚未观察',NOT_CONNECTED:'尚未连接',INPUT_REJECTED:'输入未通过校验',
 CASH_RECONCILIATION_UNRESOLVED:'现金对账尚未完成',ORDER_QUERY_UNRESOLVED:'订单状态尚未确认',
};
export function KrakenPortfolio() {
 const resource=useResource('kraken-portfolio',signal=>fetchConsoleModuleSnapshot<KrakenPortfolioPayload>(endpoint,[{endpoint}],signal),15000);
 const data=resource.error?null:resource.data;
 const fresh=data?.snapshot_status==='REAL';
 const complete=fresh&&data?.session_status==='OBSERVATION_COMPLETE';
 return <Panel title="Kraken · xStocks / XAUT" note="账户观察与组合规划；每15秒读取状态。自动交易尚未接通。">
  <ErrorNote error={resource.error} retry={resource.refresh}/>
  {!fresh?<Empty title={data?.snapshot_status==='STALE'?'会话记录已过期':data?.snapshot_status==='DATA_BLOCKED'?'会话记录未通过校验':'尚无可用账户组合会话'}/>:<>
   <p className="s-pad">最近记录：{stamp(data.observed_at_ms,true)} · {data.session_status==='REFRESH_IN_PROGRESS'?'正在刷新，本轮尚未完成':data.session_status==='FAILED'?'本轮观察失败':'本轮观察已完成'}</p>
   {complete&&<ol aria-label="Kraken运行流程">{data.stages.map(row=><li key={row.id}><strong>{names[row.id]??row.id}</strong>：{labels[row.status]??row.status}</li>)}</ol>}
   {complete&&data.blockers.length>0&&<ul aria-label="Kraken待解决事项">{data.blockers.map((item,index)=><li key={index}>{labels[item]??item}</li>)}</ul>}
  </>}
 </Panel>;
}
