import type {DemoIssuerSnapshot} from '@/lib/types/demo-issuer-status';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';

const phases={POLICY_INACTIVE:'当前政策未生效',LIMIT_REACHED:'部分签发额度已用尽',PENDING_SIGNATURE:'存在未完成的签名预留',POLICY_OBSERVED:'已读取签发政策'};
export function DemoIssuerStatus({snapshot,asOf,refreshFailed=false}:{snapshot?:DemoIssuerSnapshot;asOf?:number;refreshFailed?:boolean}) {
  const r=snapshot?.report;
  const historical=refreshFailed || snapshot?.status==='STALE';
  return <Panel title="独立签发与额度" note="签发日志的只读摘要 · Demo 专用">
    {!r ? <Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE'?'签发记录无法验证':snapshot?.status==='DATA_BLOCKED'?'签发记录时间异常':'尚无独立签发记录'}>
      等待独立签发进程发布政策与额度记录。当前页面无法确认签发配置是否可用。
    </Empty> : <div className="s-demo-replay">
      {historical && <div className="s-demo-notice" role="status">{refreshFailed?'刷新失败，保留上次签发记录。':'签发展示已过期，以下为历史记录。'}</div>}
      <p className="s-demo-description"><strong>{phases[r.phase]}</strong> · {r.symbol} · 更新于 {stamp(r.observed_at_ms,true)}</p>
      {asOf!=null && asOf>=r.valid_until_ms && <p className="s-demo-notice">这份政策的有效期已结束，历史记录与已占额度继续保留。</p>}
      <div className="s-metrics s-demo-metrics s-demo-issuer-metrics">
        <Metric label="已预留签发" value={r.budgets.reduce((n,b)=>n+b.reserved_grants,0)} note="预留会占用额度，中断后也不会自动退回"/>
        <Metric label="已记录签名" value={r.budgets.reduce((n,b)=>n+b.signed_grants,0)} note="签名记录不能证明订单已发送或成交"/>
        <Metric label="开单名义额度剩余" value={`${r.budgets[0].remaining_notional} ${r.symbol.slice(3)}`} note="签发预算，与账户余额和持仓风险分别核算"/>
      </div>
      <div className="s-table-wrap s-demo-table" role="region" aria-label="签发额度明细" tabIndex={0}>
        <table className="s-table" aria-label="Demo 签发额度"><thead><tr><th>用途</th><th>已预留 / 上限</th><th>已签名</th><th>订单数 / 上限</th><th>待核查预留</th></tr></thead>
          <tbody>{r.budgets.map(b=><tr key={b.purpose}><td>{b.purpose==='submission'?'普通订单':'原编号撤单'}</td>
            <td>{b.reserved_grants} / {b.max_grants}</td><td>{b.signed_grants}</td><td>{b.reserved_orders} / {b.max_orders}</td>
            <td>{b.reserved_grants-b.signed_grants}</td></tr>)}</tbody>
        </table>
      </div>
      <p className="s-demo-description">尚未完成签名的预留需要核查原日志。订单结果见订单状态，资金变化见现金对账。</p>
      <details className="s-demo-identities"><summary>查看政策与记录来源</summary>
        <p>政策：{r.policy_id} · 有效期 {stamp(r.valid_from_ms,true)} 至 {stamp(r.valid_until_ms,true)}</p>
        <p>公钥注册：{r.key_status==='ACTIVE'?'已登记':r.key_status==='REVOKED'?'已撤销':'缺失'}。展示进程不读取签名私钥，因此不确认私钥可用性。</p>
        <p>政策摘要：<code>{r.policy_sha256}</code></p><p>日志末条摘要：<code>{r.journal_sha256 ?? '尚无记录'}</code></p>
        <p>采集源码：<code>{r.source_commit}</code></p><p>账户引用：<code>{r.account_ref_sha256}</code></p>
      </details>
    </div>}
  </Panel>;
}
