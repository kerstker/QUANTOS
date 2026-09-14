import type {DemoExecutorObservationSnapshot,ExecutorComponent} from '@/lib/types/demo-executor-observation';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';

const names:Record<ExecutorComponent,string> = {private_stream:'执行侧私有流',origin_BTCUSDC:'BTCUSDC 账户起点',
  origin_BTCUSDT:'BTCUSDT 账户起点',resident:'原编号恢复',oms:'订单状态投影',cancellation:'原编号撤单'};
const phases:Record<string,string> = {CONNECTING:'连接建立中',CONNECTED:'已观察到连接',DISCONNECTED:'连接已中断',STOPPED:'采集已停止',STALE:'最近事件已过期',
  OBSERVED_FLAT_QUOTE:'已观察到同币种空仓',UNRESOLVED:'起点条件未齐',NO_RESERVATIONS:'尚无订单预留',INPUTS_UNRESOLVED:'输入尚未齐备',
  WAITING_QUERY_INTERVAL:'等待下一次查询',RESIDENT_BUSY:'恢复处理中',QUERY_RECOVERY_REQUIRED:'等待原编号查询',
  QUERY_OBSERVED_RECEIPTS_REQUIRED:'查询已返回，等待回执',OMS_TERMINAL_CASH_SEPARATE:'订单终态已投影，现金待核对',
  AWAITING_PROJECTION:'等待状态投影',PROJECTION_INTERRUPTED:'状态投影中断',OMS_TERMINAL_PROJECTED:'订单终态已投影',OMS_ACCOUNT_RECONCILED:'原订单已恢复，人工平仓账务已核对',
  RESERVED_AWAITING_ATTEMPTS:'撤单范围已预留',ORIGINAL_ID_QUERY_RECOVERY_REQUIRED:'撤单后等待原编号查询'};

export function DemoExecutorObservation({snapshot,asOf,refreshFailed=false}:{snapshot?:DemoExecutorObservationSnapshot;asOf?:number;refreshFailed?:boolean}) {
  const r=snapshot?.report;
  const old=refreshFailed || snapshot?.status==='STALE';
  const stream=r?.components[0].counts;
  return <Panel title="执行侧观测" note="来自执行侧固定状态目录的独立展示副本">
    {!r ? <Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'执行侧观测无法验证':snapshot?.status==='DATA_BLOCKED'?'执行侧观测时间异常':'尚无执行侧观测'}>
      研究端的数据采集仍可运行。此处等待执行侧原日志与恢复记录的展示副本。
    </Empty> : <div className="s-demo-replay">
      {old && <div className="s-demo-notice" role="status">{refreshFailed?'刷新失败，以下为上次执行侧观测。':'执行侧展示已过期，以下为历史记录。'}</div>}
      <p className="s-demo-description">{r.phase==='PARTIAL_RECORDS'?'部分环节尚无有效记录':'六个环节均有记录'} · 汇总时间 {stamp(r.observed_at_ms,true)}。记录存在不代表执行验收或现金对账完成。</p>
      {stream && <div className="s-metrics s-demo-metrics">
        <Metric label="原日志记录" value={stream.records} note="在同一读取事务内核对完整本地链"/>
        <Metric label="私有业务消息" value={stream.messages} note={`原文去重后 ${stream.unique_messages} 条`}/>
        <Metric label="重复投递" value={stream.messages-stream.unique_messages} note="原记录保留"/>
        <Metric label="已记录缺口" value={stream.gaps} note="重连不会抹去历史断档"/>
      </div>}
      <ul className="s-demo-executor-observations" aria-label="执行侧原始记录状态">
        {r.components.map(row=>{
          const age=row.observed_at_ms!=null && asOf!=null ? Math.max(0,asOf-row.observed_at_ms):null;
          const limit=row.name.startsWith('origin_')?30000:row.name==='private_stream'?120000:row.name==='resident'?60000:null;
          const expired=limit!=null && age!=null && age>limit;
          return <li key={row.name}>
            <div><strong>{names[row.name]}</strong><span className="s-demo-cell-note">{row.status==='NO_SNAPSHOT'?'尚无记录':row.status==='CORRUPT_OR_UNREADABLE'?'记录无法验证':phases[row.phase ?? ''] ?? row.phase}</span></div>
            <div className="s-demo-executor-time">{row.observed_at_ms==null?'—':<><time dateTime={new Date(row.observed_at_ms).toISOString()}>{stamp(row.observed_at_ms,true)}</time><small>{old?'历史展示':expired?'观察已过期':limit==null?'最近保存记录':'最近观察'}{age!=null?` · ${Math.floor(age/1000)} 秒前`:''}</small></>}</div>
          </li>;
        })}
      </ul>
      <details className="s-demo-identities"><summary>查看记录来源</summary>
        <p>只读摘要用于定位运行问题。来源认证、普通下单、现金闭合及盈利结论分别核验。</p>
        <p>采集源码：<code>{r.source_commit}</code></p><p>账户引用：<code>{r.account_ref_sha256}</code></p>
        {r.components.filter(row=>row.report_sha256).map(row=><p key={row.name}>{names[row.name]}：<code>{row.report_sha256}</code></p>)}
      </details>
    </div>}
  </Panel>;
}
