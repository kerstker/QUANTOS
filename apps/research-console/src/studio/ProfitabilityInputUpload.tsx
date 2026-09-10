import {useState} from 'react';
import type {ProfitabilityInputVersion} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {stamp} from './data';

const NAMES:Record<string,string>={basis:'P3 基差信息',information:'P4 资金费与 OI',trajectory:'P5 成交轨迹对照',calibration:'P5 固定动作校准',passive:'P5 原始盘口与队列'};
const FIELDS:Record<string,string>={passive:'tape、actions；tape 为原始逐笔采集 JSON，actions 为最多 200 条固定 ORDER / CANCEL。价格与数量使用字符串，时间使用 Unix 毫秒。空动作只校验输入。可加 account 核算账户；有冻结账户响应时使用 account_terms，替代手工 fee_rate / liquidity_fee_rates，保留回放前后费率、杠杆配置和档位响应（不含凭证）。再加 calibration 导入双时钟回执、覆盖范围、缺口及 cutoff_ms；只按截止点前已收到的回执校准。',basis:'quote、observations；每行包含 slot_ms、source_hash、previous、current。',information:'quote、observations；包含实际可用时间、资金费结算时点与 OI 观测区间。',trajectory:'observed、simulated；两份规范化订单轨迹必须引用相同的冻结动作。',calibration:'observed、capture、actions、calibration_cutoff_ms；capture 使用公开回放 V2 格式，当前仅 BTCUSDT。'};
export function ProfitabilityInputUpload({busy,complete,versions,onImport}:{busy:boolean;complete:boolean;versions?:ProfitabilityInputVersion[];onImport:(kind:string,content:string)=>Promise<void>}) {
 const [kind,setKind]=useState('basis'),[content,setContent]=useState(''),[error,setError]=useState(''),[fileName,setFileName]=useState('');
 const submit=async()=>{setError('');try {if(new TextEncoder().encode(content).length>4000000)throw new Error('文件不能超过 4 MB');JSON.parse(content);await onImport(kind,content);setContent('');setFileName('');}catch(e){setError(e instanceof Error?e.message:'导入失败');}};
 return <Panel title="研究数据导入" note="每类输入最多保留三个不可变版本。相同内容重试不增加版本；修正数据会生成新实验，旧失败记录继续保留。">
 <div className="f-form"><div className="f-import-fields"><label>输入类型 <select value={kind} disabled={busy} onChange={e=>setKind(e.target.value)}>{Object.entries(NAMES).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></label>
 <label>选择 JSON 文件 <input type="file" accept=".json,application/json" disabled={busy} onChange={async e=>{const file=e.target.files?.[0];if(!file)return;setError('');if(file.size>4000000){setError('文件不能超过 4 MB');return;}try{setContent(await file.text());setFileName(file.name);}catch{setError('无法读取文件');}}}/></label></div>
 <p className="f-message">{FIELDS[kind]} {fileName&&`已选择：${fileName}`}</p>
 {kind==='passive'&&<details><summary>导入币安原始订单回执</summary><p>将公开盘口、固定动作与离线订单消息放入同一输入。calibration 使用 PASSIVE_BINANCE_ORDER_RECEIPTS_V1，提供 cutoff_ms 和 raw_observed。原始消息按本机接收顺序保存 raw_utf8、response_sha256、received_at_ms；bindings 中 venue_order_id 使用字符串，避免大整数编号失真。</p><p>只接收普通 LIMIT / GTX、单向持仓的固定订单。覆盖范围、缺口、匿名账户摘要与动作摘要必须明确。输入不包含 API 密钥、listenKey、请求头或签名地址；没有原始回执时，继续使用已有规范化轨迹。</p></details>}
 <label>研究 JSON 内容<textarea spellCheck={false} rows={5} value={content} disabled={busy} onChange={e=>setContent(e.target.value)} placeholder="选择文件，或粘贴规范化研究数据"/></label>
 <div className="f-toolbar"><button className="s-button" disabled={busy||!complete||!content.trim()} onClick={()=>void submit()}>登记输入并运行</button><span>仅针对当前已完成的冻结实验；不会改变模型或风险预算。</span></div>
 {error&&<p role="alert">{error}</p>}
 {!!versions?.length&&<div className="s-table-wrap" role="region" aria-label="研究输入版本记录" tabIndex={0}><table className="s-table"><thead><tr><th>输入</th><th>版本</th><th>登记时间</th><th>操作者</th><th>结果状态</th><th>尝试数</th><th>内容身份</th></tr></thead><tbody>{versions.map(v=><tr key={`${v.kind}/${v.revision}`}><td>{NAMES[v.kind]??v.kind}</td><td>{v.revision}</td><td>{stamp(v.created_ms,true)}</td><td>{v.operator}</td><td>{v.result_status==='NOT_RUN'?'已登记，等待运行':v.result_status??'未知'}</td><td>{v.trials_attempted??'—'}</td><td title={v.input_hash}>{v.input_hash.slice(0,12)}</td></tr>)}</tbody></table></div>}
 </div></Panel>;
}
