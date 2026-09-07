import {useId, useRef, useState} from 'react';
import {Empty, Panel} from './components';
import {fmt} from './data';
export interface ChartRow {label:string; time?:number; [key:string]:string|number|undefined}
export interface ChartSeries {key:string; label:string; color?:string}
const COLORS=['#28755f','#406dba','#ba7442','#885bb0','#b74963','#4b818b','#738638','#655c4d'];
function save(name:string,body:string,type:string) {
 const url=URL.createObjectURL(new Blob([body],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
const csv=(value:unknown)=>'"'+String(typeof value==='number'?value:/^[=+@-]/.test(String(value))?"'"+value:value??'').replace(/"/g,'""')+'"';

/** Shared inspectable SVG plot: no synthetic values, keyboard cursor and raw table. */
export function Chart({title,rows,series,kind='line',unit='',note=''}:{title:string;rows:ChartRow[];series:ChartSeries[];kind?:'line'|'bar'|'scatter'|'candle';unit?:string;note?:string}) {
 const id=useId(),svg=useRef<SVGSVGElement>(null);
 const [hidden,setHidden]=useState<string[]>([]),[window,setWindow]=useState(0),[cursor,setCursor]=useState<number|null>(null),[table,setTable]=useState(false),[expanded,setExpanded]=useState(false);
 const data=window?rows.slice(-window):rows;const active=series.filter(s=>!hidden.includes(s.key));
 const numbers=data.flatMap(r=>kind==='candle'?['low','high'].map(k=>Number(r[k])):active.map(s=>typeof r[s.key]==='number'?r[s.key] as number:NaN)).filter(Number.isFinite);
 const lower=Math.min(...numbers,kind==='bar'?0:Infinity),upper=Math.max(...numbers,kind==='bar'?0:-Infinity);
 const pad=(upper-lower)*.06 || Math.abs(upper)*.01 || 1;
 const lo=lower-pad,hi=upper+pad;
 const x=(i:number)=>58+(i+.5)*720/Math.max(data.length,1),y=(v:number)=>240-(v-lo)/(hi-lo)*205;
 const selected=data[Math.min(cursor??data.length-1,data.length-1)];
 const exportCSV=()=>save(title+'.csv',[['label',...series.map(s=>s.key)].map(csv).join(','),...rows.map(r=>[r.label,...series.map(s=>r[s.key])].map(csv).join(','))].join('\n'),'text/csv;charset=utf-8');
 const exportSVG=()=>{if(svg.current){const copy=svg.current.cloneNode(true) as SVGSVGElement;copy.setAttribute('xmlns','http://www.w3.org/2000/svg');save(title+'.svg',copy.outerHTML,'image/svg+xml');}};
 return <div className={expanded?'s-plot-expanded':''} onKeyDown={e=>{if(expanded&&e.key==='Escape'){setExpanded(false);e.stopPropagation();}if(expanded&&e.key==='Tab'){const controls=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),select,input,[tabindex="0"]'));const first=controls[0],last=controls[controls.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}}}><Panel title={title} note={note} action={<div className="s-chart-actions"><select aria-label={title+'范围'} value={window} onChange={e=>{setWindow(Number(e.target.value));setCursor(null);}}><option value={0}>全部样本</option><option value={30}>最近30点</option><option value={90}>最近90点</option></select><button className="s-text-link" onClick={()=>setTable(v=>!v)}>{table?'图形':'数据表'}</button><button className="s-text-link" onClick={exportCSV}>CSV</button><button className="s-text-link" onClick={exportSVG}>SVG</button><button className="s-text-link" onClick={()=>setExpanded(v=>!v)} aria-label={expanded?'收起'+title:'放大'+title}>{expanded?'收起':'放大'}</button></div>}>
 <div className="s-plot-legend">{series.map((s,i)=><button key={s.key} disabled={kind==='candle'} aria-pressed={!hidden.includes(s.key)} onClick={()=>setHidden(h=>h.includes(s.key)?h.filter(k=>k!==s.key):[...h,s.key])}><i style={{background:s.color??COLORS[i%COLORS.length]}}/>{s.label}</button>)}<span>{unit}</span></div>
 {!data.length||!numbers.length?<Empty title="暂无可绘制的数据">需要对应的真实观测或验证结果。</Empty>:<>

 {table?<div className="s-table-wrap" tabIndex={0} role="region" aria-label={title+'数据表'}><table className="s-table"><thead><tr><th>时点 / 项目</th>{series.map(s=><th key={s.key}>{s.label}</th>)}</tr></thead><tbody>{data.map((r,i)=><tr key={i}><td>{r.label}</td>{series.map(s=><td key={s.key}>{fmt(r[s.key],4)}</td>)}</tr>)}</tbody></table></div>:<div className="s-plot">
 <svg ref={svg} viewBox="0 0 800 278" role="img" aria-labelledby={id} onMouseMove={e=>{const box=e.currentTarget.getBoundingClientRect();setCursor(Math.max(0,Math.min(data.length-1,Math.floor(((e.clientX-box.left)*800/box.width-58)/720*data.length))));}} onMouseLeave={()=>setCursor(null)}>
 <title id={id}>{title}，{data.length}个样本，{unit}。可用下方滑块检查每个样本。</title><rect width="800" height="278" fill="#ffffff"/>
 {[0,1,2,3,4].map(i=>{const value=lo+(hi-lo)*i/4;return <g key={i}><line x1="58" x2="778" y1={y(value)} y2={y(value)} stroke="#dce4e9" strokeDasharray="3 4"/><text x="50" y={y(value)+4} textAnchor="end" fill="#566875" fontSize="10">{fmt(value,Math.abs(value)>100?0:2)}</text></g>;})}
 {kind==='candle'?data.map((r,i)=>{const up=Number(r.close)>=Number(r.open),color=up?'#28755f':'#c34b55';return <g key={i}><line x1={x(i)} x2={x(i)} y1={y(Number(r.high))} y2={y(Number(r.low))} stroke={color}/><rect x={x(i)-Math.max(1,260/data.length)} width={Math.max(2,520/data.length)} y={y(Math.max(Number(r.open),Number(r.close)))} height={Math.max(1,Math.abs(y(Number(r.open))-y(Number(r.close))))} fill={color}/></g>;}) : active.map(s=>{const color=s.color??COLORS[series.indexOf(s)%COLORS.length];return <g key={s.key}>{kind==='line'?<path d={data.map((r,i)=>typeof r[s.key]==='number'&&Number.isFinite(r[s.key])?`${i&&typeof data[i-1][s.key]==='number'&&Number.isFinite(data[i-1][s.key])?'L':'M'}${x(i)},${y(r[s.key] as number)}`:'').join(' ')} fill="none" stroke={color} strokeWidth="2"/>:data.map((r,i)=>typeof r[s.key]==='number'&&Number.isFinite(r[s.key])?kind==='scatter'?<circle key={i} cx={x(i)} cy={y(r[s.key] as number)} r="3" fill={color}/>:<rect key={i} x={x(i)-250/data.length+active.indexOf(s)*500/data.length/active.length} width={Math.max(1,500/data.length/active.length)} y={y(Math.max(0,r[s.key] as number))} height={Math.max(1,Math.abs(y(r[s.key] as number)-y(0)))} fill={color}/>:null)}</g>;})}
 {cursor!==null&&<line x1={x(Math.min(cursor,data.length-1))} x2={x(Math.min(cursor,data.length-1))} y1="30" y2="240" stroke="#73818b" strokeDasharray="4 3"/>}
 {[0,Math.floor((data.length-1)/2),data.length-1].filter((v,i,a)=>a.indexOf(v)===i).map(i=><text key={i} x={x(i)} y="264" textAnchor={i===0?'start':i===data.length-1?'end':'middle'} fill="#566875" fontSize="10">{data[i].label}</text>)}
 </svg>
 <label className="s-chart-inspector">检查样本<input type="range" min={0} max={data.length-1} value={Math.min(cursor??data.length-1,data.length-1)} onChange={e=>setCursor(Number(e.target.value))} aria-label={title+'检查样本'}/></label>
 <div className="s-plot-reading" aria-live="polite"><strong>{selected?.label}</strong>{series.map(s=><span key={s.key}>{s.label} <b>{fmt(selected?.[s.key],4)}</b></span>)}</div>
 </div>}</>}
 </Panel></div>;
}

export function Heatmap({title,columns,rows,values,unit=''}:{title:string;columns:string[];rows:string[];values:(number|null)[][];unit?:string}) {
 const max=Math.max(1,...values.flat().filter((v):v is number=>v!==null&&Number.isFinite(v)).map(Math.abs));
 return <Panel title={title} note={unit}><div className="s-table-wrap" tabIndex={0} role="region" aria-label={title}><table className="s-table s-heatmap"><thead><tr><th>项目</th>{columns.map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={r}><th>{r}</th>{columns.map((c,j)=>{const v=values[i]?.[j];return <td key={c} style={{background:v==null?undefined:`rgba(${v>=0?'40,117,95':'195,75,85'},${.07+Math.abs(v)/max*.22})`}}>{fmt(v,2)}</td>;})}</tr>)}</tbody></table></div></Panel>;
}
