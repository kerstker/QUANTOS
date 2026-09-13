import type {DemoBackupComponent,DemoBackupNamespace,DemoBackupSnapshot,DemoBackupTimer} from '@/lib/types/demo-backup-status';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';
import './demo-backup-status.css';

const names:Record<DemoBackupNamespace,string>={research:'研究数据',executor:'执行记录',issuer:'签发记录',controller:'工作流状态'};
const missing={NO_SPOOL:'尚无周期捕获目录',NO_BACKUP_DIRECTORY:'尚无加密归档目录',UNREADABLE:'备份记录无法读取',BACKUP_VOLUME_UNAVAILABLE:'备份存储不可用'};
function duration(ms:number) {
  if (ms<60000) return `${Math.floor(ms/1000)} 秒`;
  if (ms<3600000) return `${Math.floor(ms/60000)} 分钟`;
  return `${(ms/3600000).toFixed(ms%3600000?1:0)} 小时`;
}
function timer(state:DemoBackupTimer,role:string) {
  return `${role}${state==='ACTIVE'?'运行中':state==='INACTIVE'?'未运行':state==='NOT_INSTALLED'?'未安装':'状态未知'}`;
}
function current(row:DemoBackupComponent,at:number) {
  const point=row.summary?.recovery_point_at_ms;
  return point!=null&&at>=point&&at-point<=row.summary!.rpo_seconds*1000;
}
export function DemoBackupStatus({snapshot,asOf,refreshFailed=false}:{snapshot?:DemoBackupSnapshot;asOf?:number;refreshFailed?:boolean}) {
  const r=snapshot?.report;
  const historical=refreshFailed||snapshot?.status==='STALE';
  const at=asOf??r?.observed_at_ms??0;
  return <section aria-label="备份与恢复点"><Panel title="备份与恢复点" note="研究、执行、签发与工作流分别核对 · 只读状态">
    {!r?<Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'备份摘要无法验证':snapshot?.status==='DATA_BLOCKED'?'备份摘要时间异常':'尚无周期备份记录'}>
      等待备份进程发布恢复点、失败和调度状态。缺失记录保持未知。
    </Empty>:<div className="s-demo-replay s-demo-backup">
      {historical&&<div className="s-demo-notice" role="status">{refreshFailed?'刷新失败，保留上次备份摘要。':'备份摘要已过期，以下为历史记录。'}</div>}
      <div className="s-metrics s-demo-metrics">
        <Metric label="有效恢复点覆盖" value={`${r.components.filter(row=>current(row,at)).length} / 4`} note="按恢复点年龄与各自恢复点目标核对"/>
        <Metric label="待封装周期" value={r.components.reduce((n,row)=>n+(row.summary?.pending_cycles??0),0)} note="仅汇总已提供记录的目录；缺失目录仍未知"/>
        <Metric label="自动调度运行中" value={`${r.components.filter(row=>row.capture_timer==='ACTIVE'&&row.seal_timer==='ACTIVE').length} / 4`} note="捕获与封装两个定时器当前均在运行"/>
      </div>
      <div className="s-demo-backup-grid">
        {r.components.map(row=>{
          const s=row.summary;const age=s?.recovery_point_at_ms==null?null:Math.max(0,at-s.recovery_point_at_ms);
          const valid=current(row,at);const budget=(s?.rpo_seconds??86400)*1000;
          return <article className="s-demo-backup-card" key={row.namespace}>
            <div className="s-demo-backup-heading"><h3>{names[row.namespace]}</h3>
              <span className={`s-demo-backup-badge ${age===null?'unknown':valid&&!historical?'current':'attention'}`}>
                {age===null?'恢复点未知':valid?'恢复点在有效期内':'恢复点已过期'}</span></div>
            <p className="s-demo-backup-schedule"><span>{timer(row.capture_timer,'捕获')}</span><span>{timer(row.seal_timer,'封装')}</span></p>
            {s?<>
              <div className="s-demo-backup-age"><strong>{age===null?'—':duration(age)}</strong><span>恢复点目标 {duration(budget)}</span></div>
              {age!==null&&<div className={`s-demo-backup-meter ${valid?'':'overdue'}`} role="meter" aria-label={`${names[row.namespace]}恢复点年龄`}
                aria-valuemin={0} aria-valuemax={budget} aria-valuenow={Math.min(age,budget)} aria-valuetext={`${duration(age)}，目标 ${duration(budget)}`}>
                <i style={{width:`${Math.min(100,age/budget*100)}%`}}/></div>}
              <dl><div><dt>可恢复至</dt><dd>{stamp(s.recovery_point_at_ms,true)}</dd></div>
                <div><dt>完整恢复核验</dt><dd>{stamp(s.last_verified_at_ms,true)}</dd></div>
                <div><dt>剩余捕获槽位</dt><dd>{s.capture_slots_remaining} / 512</dd></div></dl>
              {s.capture_slots_remaining<52&&<p className="s-demo-backup-capacity">剩余槽位不足 10%，需要安排归档保留。</p>}
            </>:<p className="s-demo-backup-missing">{row.status==='OBSERVED'?'恢复点未知':missing[row.status]}</p>}
          </article>;
        })}
      </div>
      <div className="s-table-wrap s-demo-table" role="region" aria-label="备份周期明细" tabIndex={0}>
        <table className="s-table" aria-label="备份周期明细"><thead><tr><th>范围</th><th>已核验</th><th>待封装</th><th>捕获失败</th><th>捕获中断</th><th>封装失败次数</th></tr></thead>
          <tbody>{r.components.map(row=><tr key={row.namespace}><td>{names[row.namespace]}</td><td>{row.summary?.verified_cycles??'—'}</td>
            <td>{row.summary?.pending_cycles??'—'}</td><td>{row.summary?.failed_cycles??'—'}</td><td>{row.summary?.interrupted_cycles??'—'}</td><td>{row.summary?.seal_failures??'—'}</td></tr>)}</tbody></table>
      </div>
      <p className="s-demo-description">中断和失败继续保留；恢复成功不会自动启用调度。各目录分别捕获，不能视为同一时刻的系统快照。</p>
      <details className="s-demo-identities"><summary>查看记录口径</summary>
        <p>恢复点年龄从捕获开始计算。每次封装包含完整隔离恢复；本页刷新读取摘要，不重新解密验证归档。</p>
        <p>更新时间：{stamp(r.observed_at_ms,true)} · 来源：<code>{r.source_commit}</code></p>
        <p>当前不自动删除历史；恢复点不表示实盘执行或盈利条件已经满足。</p>
      </details>
    </div>}
  </Panel></section>;
}
