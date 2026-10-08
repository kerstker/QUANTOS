import {useRef, useState} from 'react';
import type {DemoFunctionalSnapshot} from '@/lib/types/demo-functional-status';
import {activeFunctionalPhases} from '@/lib/api/demo-functional-status-contract';
import {Icon} from './components';
import {stamp} from './data';
import './demo-control-center.css';

const LOCAL_COMMAND='sudo /usr/local/sbin/quantos-demo-functional';
const REMOTE_COMMAND='ssh -t -o GSSAPIAuthentication=no kerstker@192.168.5.68 "sudo /usr/local/sbin/quantos-demo-functional"';

function StartGuide({recover=false,successor=false}:{recover?:boolean;successor?:boolean}) {
  const [remote,setRemote]=useState(false);
  const [copied,setCopied]=useState<{command:string;message:string}|null>(null);
  const field=useRef<HTMLTextAreaElement>(null);
  const option=successor?' --new-cycle':recover?' --recover':'';
  const command=remote?REMOTE_COMMAND.replace('quantos-demo-functional"',`quantos-demo-functional${option}"`):LOCAL_COMMAND+option;
  const id=successor?'demo-successor':recover?'demo-recovery':'demo-start';
  async function copy() {
    try {
      if(!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(command);
      setCopied({command,message:'命令已复制；请在终端执行。'});
    } catch {
      field.current?.focus();field.current?.select();
      setCopied({command,message:'命令已选中，请用系统复制操作复制，然后在终端粘贴。'});
    }
  }
  return <section className="s-demo-start" id={id} aria-labelledby={`${id}-title`} tabIndex={-1}>
    <div className="s-demo-start-heading"><span className="s-demo-section-index">01</span><div>
      <h3 id={`${id}-title`}>{successor?'下一轮有限测试 · 在终端登记':recover?'恢复本轮 · 查询与账务核对':'首次启动 · 在终端完成登记'}</h3>
      <p>{successor?'原账本、累计亏损和已用容量继续沿用。终端会展示新的有限测试配置，确认后才会开始下一轮。':recover?'查询原订单并重新核对已有账务；本命令不会发送开仓或减仓单，也不会续期授权。':'网页集中查看进度；首次登记在 DGX 终端完成。这里不需要填写 API 密钥。'}</p>
    </div></div>
    <div className="s-demo-start-grid">
      <div>
        <div className="s-demo-command-switch" role="group" aria-label="选择启动位置">
          <button type="button" aria-pressed={!remote} onClick={()=>setRemote(false)}>DGX 本机</button>
          <button type="button" aria-pressed={remote} onClick={()=>setRemote(true)}>另一台电脑</button>
        </div>
        <label className="s-sr-only" htmlFor={`${id}-command`}>{remote?'另一台电脑':'DGX 本机'}{successor?'下一轮测试命令':recover?'恢复命令':'启动命令'}</label>
        <textarea ref={field} id={`${id}-command`} className="s-demo-command" readOnly value={command} rows={remote?3:2} spellCheck={false}/>
        <div className="s-demo-command-actions"><button type="button" className="s-button" onClick={()=>void copy()}>{successor?'复制下一轮命令':recover?'复制恢复命令':'复制启动命令'}</button>
          <span>{remote?'在另一台电脑的 CMD、PowerShell 或终端中执行':'在 DGX 的终端中执行'}</span></div>
        <p className="s-demo-copy-status" role="status">{copied?.command===command?copied.message:''}</p>
      </div>
      {recover?<ol className="s-demo-start-instructions">
        <li><strong>先查看本轮持仓</strong><span>以最新账户观察为准；已人工平仓时，不根据旧账本数量再次减仓。</span></li>
        <li><strong>在终端执行恢复命令</strong><span>程序检查原记录，按原订单编号查询；已有失败和尝试记录全部保留。</span></li>
        <li><strong>查看恢复后的结果</strong><span>缺失原文、现金差额或仍有持仓会明确保留待处理状态，不会被重试掩盖。</span></li>
      </ol>:<ol className="s-demo-start-instructions">
        <li><strong>打开终端，执行命令</strong><span>{successor?'先检查上一轮已平仓并核对账务，再展示本次测试规格。':'首次启动会读取账户并展示本次测试规格。'}</span></li>
        <li><strong>在原终端输入本次确认行</strong><span>如果原终端已在等待登记，输入它当次显示的完整「DEMO + 摘要」并回车。无需重新启动命令；不要使用旧确认行。</span></li>
        <li><strong>回到本页查看执行结果</strong><span>登记后程序自动推进开仓、回执、减仓和现金核对。本页每 15 秒更新。</span></li>
      </ol>}
    </div>
    <p className="s-demo-start-note">{successor?'BTCUSDC · 单笔最多 250 USDC · 累计最多 750 USDC · 最多 3 笔 · 有效期 1 小时。旧授权、旧损益和人工平仓记录全部保留；沿用当前停机状态。':recover?'恢复原账务不等于新一轮测试。人工平仓保留人工属性；新的交易动作仍受原授权有效期、剩余额度和风控限制。':'首次入口的预设范围：BTCUSDC · 单笔最多 250 USDC · 累计最多 750 USDC · 最多 3 笔 · 有效期 1 小时。以终端本次展示的规格为准。没有收到周期记录时，不能据此判断登记是否已完成。'}</p>
  </section>;
}

export function DemoControlCenter({snapshot,connected,refreshFailed=false,enrollmentObserved=false,position}: {
  snapshot?:DemoFunctionalSnapshot;connected:boolean;refreshFailed?:boolean;enrollmentObserved?:boolean;
  position?:import('./demo-account-position').DemoAccountPosition|null;
}) {
  const report=snapshot?.report;
  const uncertain=refreshFailed || snapshot?.status==='STALE';
  const damaged=snapshot?.status==='CORRUPT_OR_UNREADABLE' || snapshot?.status==='DATA_BLOCKED';
  const complete=!uncertain && snapshot?.status==='AVAILABLE' && report?.phase==='DEMO_CYCLE_COMPLETED';
  const active=!uncertain && !!report && activeFunctionalPhases.includes(report.phase);
  const first=!report && !uncertain && !damaged && !enrollmentObserved;
  const [guideExpanded,setGuideExpanded]=useState(true);
  let title='从这里开始 DEMO 测试';
  let description='尚未收到交易周期记录';
  let action='查看运行记录',target='#demo-cycle';
  if(uncertain) {
    title='当前进度需要重新核查';description='保留已有记录；请刷新并检查采集状态。此时无法确认周期是否仍在运行。';
  } else if(damaged) {
    title='测试记录需要检查';description='记录读取或时间检查未通过。请先核查运行诊断，避免重复启动。';target='#demo-diagnostics';action='查看运行诊断';
  } else if(complete) {
    title='本轮 DEMO 闭环已完成';description='本轮成交、归零持仓和现金核对均有记录。可继续检查费用及逐单结果。';target='#demo-cash';action='查看本轮对账';
  } else if(active) {
    title='DEMO 测试正在执行';description='执行器正在推进本轮测试，进度与结果在下方更新。请保留当前终端。';
  } else if(report) {
    const next={
      RECOVERY_REQUIRED:['先恢复本轮订单结果','保留原订单编号，先核对未明回执，再决定后续处理。','#demo-orders','查看订单与恢复'],
      REDUCTION_AUTHORIZATION_REQUIRED:['持仓已核对，等待后续处理','本次仅恢复原订单与账务，没有发送新的减仓单。请在账户中核对 BTCUSDC 持仓与测试授权。','#demo-orders','查看订单与恢复'],
      EVIDENCE_REPAIR_REQUIRED:['原始回执缺失，重试已停止','原窗口存在私有流缺口，需要修复证据。单纯等待或重复查询无法补回丢失的原文；本轮保留未完成状态。','#demo-orders','查看缺失证据'],
      MANUAL_ACCOUNT_RECONCILED:['人工平仓账务已核对','原订单、人工平仓、费用及钱包已匹配。原私有流缺口和人工属性保留，自动往返仍需下一次有限测试。','#demo-cycle','查看恢复结果'],
      REDUCTION_INCOMPLETE:['仍有持仓需要减仓','本轮尚未归零。请查看已确认数量与减仓结果。','#demo-orders','查看订单与恢复'],
      CASH_RECONCILIATION_REQUIRED:['成交已结束，现金仍待核对','查看缺失流水、费用和差异。现金未匹配前，本轮闭环未完成。','#demo-cash','查看本轮对账'],
      NO_TRADING_EVIDENCE:['本轮未形成完整往返成交','查看实际订单与成交记录，无成交和单边成交都会保留。','#demo-orders','查看订单与恢复'],
      CYCLE_NOT_COMPLETED:['本轮测试需要处理','请查看原周期记录和终端结果，保留现场后再恢复。','#demo-cycle','查看运行记录'],
    };
    const nextAction=next[report.phase as keyof typeof next];
    if(nextAction) [title,description,target,action]=nextAction;
    if(position?.flat && (report.phase==='RECOVERY_REQUIRED' || report.phase==='REDUCTION_INCOMPLETE' || report.phase==='EVIDENCE_REPAIR_REQUIRED')) {
      title='BTCUSDC 已空仓，历史账务待恢复';
      description='最新账户观察已无 BTCUSDC 持仓。保留本轮原订单，继续核对回执与费用；无需依据旧数量再次减仓。';
      target='#demo-orders';action='查看订单与恢复';
    }
  } else if(enrollmentObserved) {
    title='已收到登记相关记录';description='等待交易周期发布。请检查原终端的执行结果，勿重复进行首次登记。';
  }
  const currentReport=!uncertain && !damaged?report:null;
  const workspaces=[
    ['#demo-cycle','交易周期',uncertain?'状态待核查':complete?'本轮完成':active?'正在执行':report?'需要处理':'等待记录','forward'],
    ['#demo-connection','连接与账户',connected&&!refreshFailed?'账户快照可读':'等待有效快照','data'],
    ['#demo-orders','订单与恢复',currentReport?.orders_attempted!=null?`${currentReport.orders_attempted} 次订单尝试`:'等待本轮订单','runs'],
    ['#demo-cash','现金与对账',complete?'本轮现金匹配':currentReport?.phase==='MANUAL_ACCOUNT_RECONCILED'?'人工恢复现金匹配':'等待本轮匹配','system'],
    ['#demo-diagnostics','运行诊断','行情 · 私有流 · 备份','system'],
  ];
  return <section id="demo-center" className="s-demo-center" aria-label="DEMO 综合入口">
    <div className={`s-demo-center-overview ${complete?'complete':''}`}>
      <div className="s-demo-center-copy"><p className="s-eyebrow">BINANCE DEMO / BTCUSDC</p>
        <h2>{title}</h2><p className="s-demo-center-description">{description}</p>
        <div className="s-demo-center-actions">{first?
          <button className="s-button primary" type="button" aria-expanded={guideExpanded} aria-controls="demo-start" onClick={()=>setGuideExpanded(!guideExpanded)}>{guideExpanded?'收起启动步骤':'查看启动步骤'}</button>:
          <a className="s-button primary" href={target}>{action}<Icon name="arrow"/></a>}
          <a className="s-text-link" href="#demo-cycle">定位本轮进度 ↓</a></div>
      </div>
      <div className="s-demo-center-scope"><span className="s-demo-center-badge">虚拟资金测试</span>
        <p>开仓 <span>→</span> 回执 <span>→</span> 减仓 <span>→</span> 对账</p>
        <small>{report?`本轮记录更新于 ${stamp(report.observed_at_ms,true)}`:'完成一次真实 Demo 往返，再核对成本与现金'}</small>
      </div>
    </div>
    {first && guideExpanded && <StartGuide/>}
    {!first && report && !complete && !active && !uncertain && !damaged && !['EVIDENCE_REPAIR_REQUIRED','MANUAL_ACCOUNT_RECONCILED'].includes(report.phase) && <StartGuide recover/>}
    {!uncertain && !damaged && report?.phase==='MANUAL_ACCOUNT_RECONCILED' && <StartGuide successor/>}
    <nav className="s-demo-workspaces" aria-label="DEMO 工作区">{workspaces.map(([href,label,state,icon])=><a key={href} href={href}>
      <Icon name={icon}/><span><strong>{label}</strong><small>{state}</small></span><span aria-hidden="true">↗</span>
    </a>)}</nav>
    {!first && <p className="s-demo-center-note">已有周期或状态不明时，先查看原终端与本轮记录；重复运行首次入口不会创建新的测试周期。</p>}
  </section>;
}
