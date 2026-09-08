import type {ServiceHealth} from '@/lib/types/research';
import {Badge, Empty, Panel} from './components';
import {stamp} from './data';

const names: Record<string,string> = {
 'l2-backup-restore':'备份恢复',
 'binance-book-depth-archive':'Binance 日度订单簿归档',
 'binance-rpi-depth':'Binance RPI 深度',
 'binance-public-market-stream':'Binance 实时行情',
 'bybit-public-market-stream':'Bybit 实时行情',
 'l2-scheduler-watchdog':'调度健康检查',
 'domain-event-authority':'事件记录',
 'research-console-api':'研究控制台',
};
const reasons: Record<string,{title:string;next:string}> = {
 RESTORE_RTO_EXCEEDED:{title:'恢复耗时超过目标',next:'检查完整恢复耗时与大文件处理；完成优化后重新演练。'},
 QUALITY_REJECTED:{title:'原始数据未通过质量检查',next:'核对归档日期与质量明细；保留拒收记录，等待合格数据。'},
 FETCH_FAILED:{title:'本轮采集失败',next:'检查网络、发布状态与采集日志，确认原因后重试。'},
 STALE_RECEIPT:{title:'回执已过期',next:'检查定时任务和最近一次执行；旧成功记录不能证明当前健康。'},
 INVALID_RECEIPT_TIMESTAMP:{title:'回执时间无效',next:'核对采集时间与系统时钟，未来时间不作为新鲜数据。'},
 RECEIPT_MISSING:{title:'尚无可读回执',next:'确认此环境是否配置该任务，等待真实运行记录。'},
};

export function OperationalHealth({services}:{services:ServiceHealth[]}) {
 return <Panel title="服务与任务健康" note="采集与恢复行反映最近一次回执；进程存活不能替代任务验收。">
  {!services.length?<Empty title="没有可读服务状态"/>:<div className="s-table-wrap" tabIndex={0} role="region" aria-label="服务健康">
   <table className="s-table"><thead><tr><th>服务 / 任务</th><th>状态</th><th>最近回执</th><th>原因与处理</th></tr></thead><tbody>
    {services.map(row=>{
     const reason=row.reason_code?reasons[row.reason_code]:undefined;
     return <tr key={row.service}><td>{names[row.service]??row.service}</td><td><Badge value={row.status}/></td>
      <td>{row.observed_at_ms==null?'—':stamp(row.observed_at_ms,true)}</td>
      <td>{reason&&<><strong>{reason.title}</strong><p>{reason.next}</p></>}
       {row.reason_code&&<code>{row.reason_code}</code>}
       {row.probe?<details><summary>查看探针</summary><code>{row.probe}</code></details>:!reason&&!row.reason_code?'未提供原因':null}
      </td></tr>;
    })}
   </tbody></table>
  </div>}
 </Panel>;
}
