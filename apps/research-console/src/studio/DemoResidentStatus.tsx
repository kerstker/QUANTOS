import type {DemoResidentStatusSnapshot} from '@/lib/types/demo-resident-status';
import {Empty, Metric} from './components';

const titles = {
  NO_RESERVATIONS:'尚无系统预留批次', INPUTS_UNRESOLVED:'恢复输入待处理', WAITING_QUERY_INTERVAL:'等待下一次原编号查询',
  RESIDENT_BUSY:'已有恢复检查正在执行', QUERY_RECOVERY_REQUIRED:'原编号查询尚未完成',
  QUERY_OBSERVED_RECEIPTS_REQUIRED:'查询已取得，等待私有回执配对', OMS_TERMINAL_CASH_SEPARATE:'原订单状态已收敛，现金另行核对',
};
const reasons:Record<string,string> = {
  QUERY_CLOCK_UNRELIABLE:'时钟依据未通过', QUERY_UNRESOLVED:'仍有原订单未查明', QUERY_BUDGET_EXHAUSTED:'本次查询时间预算已用尽',
  RESIDENT_ORIGINAL_PROOF_REQUIRED:'原始批次提案缺失、冲突或无法验证', RESIDENT_SCHEDULE_INVALID:'查询节奏记录无法验证',
  RESIDENT_CLOCK_REGRESSION:'本地时钟早于已保存的查询时间', RESIDENT_ORIGINAL_STORES_INVALID:'原始订单日志无法验证',
  RESIDENT_ATTEMPT_BUDGET_EXHAUSTED:'已达到本次恢复记录预算，等待处理', RESIDENT_RECEIPT_STORES_REQUIRED:'等待执行侧私有回执日志',
  RESIDENT_RECEIPT_WINDOW_UNRESOLVED:'现有回执时钟窗口不足以完成配对', RESIDENT_SEND_CLAIM_MISSING:'没有原始发送标记，订单仍保留待查',
  RESIDENT_QUERY_OR_PROJECTION_STOPPED:'查询或订单投影中断，原编号保持待恢复',
};
const utc = (v:number|null) => v===null ? '尚无记录' : new Date(v).toISOString();
export function DemoResidentStatus({snapshot, asOf, refreshFailed}: {
  snapshot?:DemoResidentStatusSnapshot; asOf:number; refreshFailed:boolean;
}) {
  const r = snapshot?.report; const last = snapshot?.last_result;
  const stale = !!r && asOf-r.observed_at_ms>120000;
  return <section className="s-demo-resident" aria-label="驻留恢复工作者">
    {!r ? <Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'驻留恢复记录无法验证':
      snapshot?.status==='DATA_BLOCKED'?'驻留恢复时间待核查':'等待驻留恢复记录'}>
      执行侧安装后，这里显示工作者心跳、原编号查询节奏和上次结果。
    </Empty> : <>
      <div className="s-demo-replay-heading"><h3>{stale?'恢复工作者心跳已过期':titles[r.phase]}</h3><span className="s-demo-source">原编号自动恢复</span></div>
      <p className="s-demo-description">记录截至 {utc(r.observed_at_ms)}。查询与回执配对后恢复原订单状态，现金仍由唯一账本核对。</p>
      {refreshFailed&&<p role="status">刷新失败，当前显示上次读取的历史状态。</p>}
      {r.reason&&<p className="s-demo-notice" role="status">{reasons[r.reason]??r.reason}</p>}
      <div className="s-metrics">
        <Metric label="已登记查询尝试" value={r.attempt_count===null?'未知':`${r.attempt_count} 次`}/>
        <Metric label="上次尝试" value={utc(r.last_attempt_at_ms)}/>
        <Metric label="最早可再次查询" value={utc(r.next_query_at_ms)}/>
      </div>
      {last&&<p className="s-demo-description">上次查询：{last.query_reason?reasons[last.query_reason]??last.query_reason:
        last.query_status==='ORDER_SNAPSHOTS_OBSERVED'?'原订单快照已取得':'查询或投影仍待恢复'} · {utc(last.observed_at_ms)}。该结果保留原观察时间。</p>}
      <details className="s-demo-identities"><summary>查看恢复来源与未解决项</summary>
        {!!last?.resolution_issues.length&&<ul>{last.resolution_issues.map(v=><li key={v}>{v}</li>)}</ul>}
        <dl>{[['当前批次',r.reservation_sha256??'尚无选中批次'],['进程身份',r.executor_boot_sha256],['源码',r.source_commit],
          ['当前报告',r.report_sha256],['上次结果',last?.report_sha256??'尚无记录']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        <p>缺记录、查询超时或重启不会触发补单；过期心跳不表示工作者仍在运行。现金闭合及盈利需另有证据。</p>
      </details>
    </>}
  </section>;
}
