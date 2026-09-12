import {useState} from 'react';
import type {DemoCashWindowSnapshot} from '@/lib/types/demo-cash-window';
import {Empty, Panel} from './components';
import {stamp} from './data';

const issues: Record<string,string> = {
  CAPTURE_INCOMPLETE:'采集尚未完成，成功观察与失败均已保留。',
  INCOME_PAGE_LIMIT:'流水分页达到预算，不能确认查询范围已读全。',
  INCOME_SWEEP_MISSING:'缺少完整的两轮流水查询。',
  INCOME_SWEEPS_DIFFER:'两轮流水结果不同，可能仍有延迟入账或分页变化。',
  CONFLICTING_FLOW_IDENTITY:'同一流水编号对应冲突内容，需要核查原始观察。',
  ENDPOINT_EVENT_AMBIGUITY:'采样边界附近存在流水，无法确定归属期初还是期末。',
  WALLET_OBSERVATION_MISSING:'缺少期初或期末钱包观察。',
  WALLET_ASSET_MISSING:'某币种缺少钱包观察，不能把缺失余额设为零。',
};
const types: Record<string,string> = {TRANSFER:'转入转出',COMMISSION:'手续费',FUNDING_FEE:'资金费',REALIZED_PNL:'已实现盈亏'};
const titles = {MATCHED_REPORTED_WINDOW:'窗口报告数值相符',DIFFERENCE_FOUND:'发现未解释差额',UNRESOLVED:'核对条件未齐'};
const readNames: Record<string,string> = {clock_before:'期初时钟',wallet_before:'期初钱包',wallet_after:'期末钱包',clock_after:'期末时钟',income:'资金流水'};
const failureNames: Record<string,string> = {REQUEST_FAILED:'请求暂时失败',RATE_LIMITED:'交易所限流',AUTHENTICATION_REJECTED:'访问被拒绝',
  RESPONSE_TOO_LARGE:'响应超出预算',RESPONSE_INVALID:'请求或响应不符合约定',CLOCK_UNRELIABLE:'时钟检查未通过',CAPTURE_BUDGET_EXHAUSTED:'采集预算耗尽'};

export function DemoCashWindow({snapshot,refreshFailed}: {snapshot?: DemoCashWindowSnapshot; refreshFailed: boolean}) {
  const r=snapshot?.report;
  const [asset,setAsset]=useState('ALL');
  const selected=r?.assets.some(a=>a.asset===asset) ? asset : 'ALL';
  const rows=r?.assets.filter(a => selected==='ALL' || a.asset===selected) ?? [];
  return <Panel title="前向现金窗口核对" note="期初钱包 + 一次计入的流水净额 → 期末钱包">
    {!r ? <Empty title={snapshot?.status==='CORRUPT_OR_UNREADABLE' ? '现金观察无法验证' : snapshot?.status==='DATA_BLOCKED' ? '现金观察时钟待核查' : '等待前向现金观察'}>
      保存同一账户的期初、期末钱包，并复查窗口内全部类型的资金流水后展示结果。
    </Empty> : <>
      <h3>{titles[r.status]}</h3>
      <p className="s-demo-description">固定观察：{stamp(r.observed_at_ms,true)} → {stamp(r.completed_at_ms,true)}。
        观察预算 {r.observe_seconds} 秒，{r.read_count} 次读取，{r.income_count} 条去重流水。</p>
      {refreshFailed && <p role="status">刷新失败，以下为上次成功读取的固定窗口记录。</p>}
      <p>这里只比较窗口内报告数值，不代表策略盈利、完整历史或交易账本已闭合。固定窗口余额不代表当前余额。</p>
      {!!r.issues.length && <div className="s-demo-notice" role="status"><ul>{r.issues.map(i => <li key={i}>{issues[i] ?? i}</li>)}</ul></div>}
      {r.attempts && <details className="s-demo-identities"><summary>查看读取与重试记录（{r.attempts.length} 次尝试，{r.attempts.filter(a=>a.outcome==='FAILED').length} 次失败）</summary>
        <p>临时请求失败最多尝试三次。每次失败均保留；时钟、限流和访问拒绝会终止本窗口。</p>
        <ol>{r.attempts.map((a,i)=><li key={i}>
          {readNames[a.kind]}{a.sweep>0 && ` · 第 ${a.sweep} 轮 / 第 ${a.page} 页`} · 尝试 {a.attempt} ·
          {a.outcome==='SUCCESS' ? '读取成功' : failureNames[a.reason ?? ''] ?? a.reason} · {stamp(a.received_at_ms,true)}
          <p className="s-demo-description">耗时 {a.received_at_ms-a.requested_at_ms} 毫秒
            {a.server_time_ms!==null && `；服务端与请求中点相差 ${a.server_time_ms-Math.floor((a.requested_at_ms+a.received_at_ms)/2)} 毫秒${a.outcome==='FAILED' ? '（未通过检查，仅供诊断）' : ''}`}。</p>
        </li>)}</ol>
      </details>}
      <div className="s-demo-stream-filter">
        <label htmlFor="cash-window-asset">核对资产</label>
        <select className="s-input" id="cash-window-asset" value={selected} onChange={e=>setAsset(e.target.value)}>
          <option value="ALL">全部币种</option>{r.assets.map(a=><option key={a.asset} value={a.asset}>{a.asset}</option>)}
        </select>
        <span>{r.sweeps_match ? '两轮流水内容相同' : '两轮流水尚未一致'} · 保留 {r.duplicate_rows} 条重复输入</span>
      </div>
      <div className="s-table-wrap s-demo-table s-demo-cash-wallet" role="region" aria-label="前向现金窗口比较表" tabIndex={0}>
        <table className="s-table"><thead><tr><th>资产</th><th>未解释差额</th><th>期初钱包</th><th>期末钱包</th><th>钱包变化</th><th>报告流水净额</th></tr></thead>
          <tbody>{rows.map(a=><tr key={a.asset}><td data-label="资产">{a.asset}</td><td data-label="未解释差额">{a.residual ?? '待核对'}</td>
            <td data-label="期初钱包">{a.opening_wallet ?? '未知'}</td><td data-label="期末钱包">{a.closing_wallet ?? '未知'}</td>
            <td data-label="钱包变化">{a.wallet_change ?? '未知'}</td><td data-label="报告流水净额">{a.reported_flow ?? '未知'}</td></tr>)}</tbody>
        </table>
      </div>
      {!rows.length && <p>尚无可比较的资产观察，不能据此认定余额或收益为零。</p>}
      <p className="s-demo-description">差额 = 期末钱包 − 期初钱包 − 窗口流水净额。手续费使用流水中的有符号金额，核对过程不再扣费。
        转账、资金费和已实现盈亏分别列示，各币种不互相抵销。</p>
      {!!rows.length && <div className="s-table-wrap s-demo-table" role="region" aria-label="窗口资金类型分解" tabIndex={0}>
        <table className="s-table"><thead><tr><th>资产</th><th>转入转出</th><th>手续费</th><th>资金费</th><th>已实现盈亏</th></tr></thead>
          <tbody>{rows.map(a=><tr key={a.asset}><td>{a.asset}</td><td>{a.transfers ?? '未知'}</td><td>{a.commissions ?? '未知'}</td>
            <td>{a.funding ?? '未知'}</td><td>{a.realized_pnl ?? '未知'}</td></tr>)}</tbody>
        </table>
      </div>}
      <details className="s-demo-identities"><summary>查看窗口流水明细</summary>
        <p>显示最近 {r.recent_flows.length} / {r.income_count} 条。{r.endpoint_ambiguous_rows} 条在采样边界附近；边界不明确时差额保持待核对。</p>
        <div className="s-table-wrap s-demo-table" role="region" aria-label="窗口逐笔资金流水" tabIndex={0}>
          <table className="s-table"><thead><tr><th>时间</th><th>资产</th><th>类型</th><th>金额</th><th>流水编号</th></tr></thead>
            <tbody>{r.recent_flows.filter(f=>selected==='ALL' || f.asset===selected).map(f=><tr key={`${f.type}:${f.transaction_id}`}>
              <td>{stamp(f.time_ms,true)}</td><td>{f.asset}</td><td>{types[f.type] ?? f.type}</td><td>{f.amount}</td><td>{f.transaction_id}</td></tr>)}</tbody>
          </table>
        </div>
        {!!r.failures.length && <ul>{r.failures.map((f,i)=><li key={i}>{f.kind} · {f.reason} · {stamp(f.received_at_ms,true)}</li>)}</ul>}
        <dl><div><dt>账户命名空间</dt><dd>{r.account_ref_sha256 ?? '尚未读取'}</dd></div></dl>
        <p className="s-demo-description">该标识绑定本次凭证命名空间，不是独立来源认证。</p>
      </details>
    </>}
  </Panel>;
}
