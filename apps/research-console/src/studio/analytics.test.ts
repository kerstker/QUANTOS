import {describe,it,expect} from 'vitest';
import {analyzeEquity,correlation,histogram} from './analytics';
const day=86400000;
describe('technical result analytics',()=>{
 it('derives drawdown and compounded returns from observed equity',()=>{
  const r=analyzeEquity([100,110,88,99].map((value,i)=>({time:i*day,value})));
  expect(r.drawdown.map(p=>p.value)).toEqual([0,0,-20,-10]);
  expect(r.returns[1].value).toBeCloseTo(-20);
  expect(r.totalReturn).toBeCloseTo(-1);
 });
 it('does not manufacture annualized statistics across gaps or invalid equity',()=>{
  const r=analyzeEquity([{time:0,value:100},{time:2*day,value:90},{time:3*day,value:99}]);
  expect(r.sharpe).toBeNull();expect(r.annualVol).toBeNull();
  expect(analyzeEquity([{time:0,value:0},{time:day,value:100}]).totalReturn).toBeNull();
 });
 it('aligns correlation on common timestamps and rejects constant series',()=>{
  const a=[1,2,3].map((value,time)=>({time,value}));
  expect(correlation(a,a)).toBeCloseTo(1);
  expect(correlation(a,a.map(p=>({...p,value:1})))).toBeNull();
  expect(correlation(a,[{time:9,value:3}])).toBeNull();
 });
 it('keeps every observation in histogram including the maximum',()=>{
  expect(histogram([-3,-1,0,1,3],3).reduce((n,b)=>n+b.count,0)).toBe(5);
  expect(histogram([],3)).toEqual([]);
 });
});

it('does not bridge malformed equity or duplicate timestamps into returns',()=>{
 const report=analyzeEquity([{time:0,value:100},{time:86400000,value:NaN},{time:172800000,value:110}]);
 expect(report.totalReturn).toBeNull();expect(report.returns).toEqual([]);
 expect(correlation([{time:1,value:1},{time:1,value:2},{time:2,value:3}],[{time:1,value:1},{time:2,value:2}])).toBeNull();
});

describe('intraday cadence, jitter tolerance, and annualization options',()=>{
 const hour=3600000;
 it('annualizes regular intraday (1h) series using inferred periodsPerYear',()=>{
  const r=analyzeEquity([100,101,100.5,102].map((value,i)=>({time:i*hour,value})));
  expect(r.regularCadence).toBe(true);
  expect(r.regularDaily).toBe(false);
  expect(r.periodsPerYear).toBeCloseTo(365*24);
  expect(r.sharpe).not.toBeNull();
  expect(r.annualVol).not.toBeNull();
 });
 it('tolerates minor <=1000ms clock jitter on daily bars while still rejecting gaps',()=>{
  const r=analyzeEquity([
   {time:0,value:100},
   {time:day+500,value:102},
   {time:2*day-400,value:101},
   {time:3*day+200,value:104},
  ]);
  expect(r.regularDaily).toBe(true);
  expect(r.sharpe).not.toBeNull();
 });
 it('supports explicit periodsPerYear override (e.g. 252 trading days)',()=>{
  const pts=[100,102,101,105].map((value,i)=>({time:i*day,value}));
  const crypto=analyzeEquity(pts);
  const equity=analyzeEquity(pts,{periodsPerYear:252});
  expect(equity.periodsPerYear).toBe(252);
  expect(crypto.sharpe).not.toBeNull();
  expect(equity.sharpe).not.toBeNull();
  expect((crypto.sharpe as number)/(equity.sharpe as number)).toBeCloseTo(Math.sqrt(365/252),6);
 });
});

