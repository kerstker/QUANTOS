import {useState} from 'react';
import {fetchConsoleModuleSnapshot} from '@/lib/api/research-console-api';
import type {GoldObservationPayload} from '@/lib/types/research';
import {Chart} from './Charts';
import {ErrorNote} from './components';
import {stamp,useResource} from './data';
import './gold-dashboard.css';
const endpoint='/api/v1/console/gold-observation';
const kinds=['futures','spot','funding'] as const;
const names={futures:'合约账户',spot:'现货账户',funding:'资金账户'};
const money=(value:string|null|undefined)=>value==null?'—':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:8});
export function BinanceWallets(){
 const [asset,setAsset]=useState<'USDT'|'USDC'>('USDT');
 const resource=useResource('binance-wallets',signal=>fetchConsoleModuleSnapshot<GoldObservationPayload>(endpoint,[{endpoint}],signal),15000);
 const data=resource.error?null:resource.data;const wallets=data?.wallets;
 const row=(kind:typeof kinds[number])=>wallets?.accounts[kind].balances.find(v=>v.asset===asset);
 const rows=kinds.map(kind=>({label:names[kind],value:row(kind)?.wallet==null||wallets?.accounts[kind].status!=='OBSERVED'?undefined:Number(row(kind)!.wallet)}));
 return <div className="g-dashboard"><ErrorNote error={resource.error} retry={resource.refresh}/>
  <section className="g-hero"><div><span className="g-eyebrow">BINANCE / MY WALLETS</span><h2>我的 U</h2><p>同一账户 · 合约 / 现货 / 资金</p></div><div className="g-last"><span>三类账户余额合计 <small>按币种分别统计</small></span><strong>{money(wallets?.totals[asset])}<small> {asset}</small></strong><p>{wallets?stamp(wallets.observed_at_ms,true):'等待账户资金资料'}</p></div><div className="g-segments" role="group" aria-label="资金币种">{(['USDT','USDC'] as const).map(a=><button key={a} aria-pressed={asset===a} onClick={()=>setAsset(a)}>{a}</button>)}</div></section>
  <p className="g-wallet-note">{data?.wallet_status==='DATA_BLOCKED'?'资金资料校验失败。':wallets?.status==='STALE'?'资金资料已过期，以下为历史观察值。':wallets?.status==='PARTIAL'?'部分账户暂不可用，合计暂不计算。':wallets?'账户分次查询，约每分钟采集，页面每15秒刷新。':'尚未读到账户资金资料。'}</p>
  <div className="g-wallet-grid">{kinds.map(kind=>{const balance=row(kind),account=wallets?.accounts[kind];return <section className="g-card" key={kind}><div className="g-card-title"><h3>{names[kind]}</h3><span>{account?.status==='OBSERVED'?'已读取':account?.status==='STALE'?'已过期':'暂不可用'}</span></div><div className="g-wallet-balance"><span>钱包余额 · {asset}</span><strong>{money(balance?.wallet)}</strong><dl>
   <div><dt>可用余额</dt><dd>{money(balance?.available)}</dd></div>
   {kind==='futures'?<><div><dt>浮动盈亏</dt><dd>{money(balance?.unrealized)}</dd></div><div><dt>保证金余额</dt><dd>{money(balance?.margin_balance)}</dd></div><div><dt>初始保证金占用</dt><dd>{money(balance?.margin_used)}</dd></div></>:<><div><dt>锁定</dt><dd>{money(balance?.locked)}</dd></div>{kind==='funding'&&<><div><dt>冻结</dt><dd>{money(balance?.frozen)}</dd></div><div><dt>提现中</dt><dd>{money(balance?.withdrawing)}</dd></div></>}</>}
   </dl><small>{account?stamp(account.observed_at_ms,true):'尚无更新时间'}</small></div></section>})}</div>
  <Chart title={asset+' 账户资金分布'} rows={rows} series={[{key:'value',label:'钱包余额',color:'var(--s-accent)'}]} kind="bar" unit={asset} note="读取失败或过期的账户不绘制为零；仅比较同一币种。"/>
  <p className="g-wallet-note">合计使用钱包余额，不加浮动盈亏；资金账户余额为可用、锁定、冻结与提现中字段合计。USDT 与 USDC 不按 1:1 合并。当前范围不含理财、杠杆、币本位及子账户。</p>
 </div>;
}
