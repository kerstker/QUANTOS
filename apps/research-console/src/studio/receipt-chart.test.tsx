import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {Chart} from './Charts';

it('keeps cumulative fills constant until receipt and uses elapsed time for spacing and inspection',()=>{
 const {container}=render(<Chart title="回执路径" kind="step" xAxis="time" unit="BTC"
  rows={[{label:'0 ms',time:0,filled:0},{label:'1000 ms',time:1000,filled:1},{label:'10000 ms',time:10000,filled:2}]}
  series={[{key:'filled',label:'成交'}]}/>);
 const path=container.querySelector('svg path');
 expect(path).not.toBeNull();
 const d=path?.getAttribute('d')??'';
 expect(d).toMatch(/H130\b/);expect(d).toMatch(/H778\b/);expect(d).not.toContain('L');
 fireEvent.change(screen.getByRole('slider'),{target:{value:'1'}});
 expect(container.querySelector('.s-plot-reading')).toHaveTextContent('1000 ms');
});

it('exports the exact receipt clock even when display labels only contain the time of day',async()=>{
 const original=URL.createObjectURL;
 let exported:Blob|undefined;
 URL.createObjectURL=vi.fn((blob:Blob)=>{exported=blob;return 'blob:receipt-test';});
 const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
 try {
  render(<Chart title="回执导出" kind="step" xAxis="time"
   rows={[{label:'00:00:00.001',time:1789000000001,filled:0.001}]}
   series={[{key:'filled',label:'成交'}]}/>);
  fireEvent.click(screen.getByRole('button',{name:'CSV'}));
  const contents=await new Promise<string>((resolve,reject)=>{
   const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsText(exported!);
  });
  expect(contents).toBe('"label","time","filled"\n"00:00:00.001","1789000000001","0.001"');
 } finally {click.mockRestore();URL.createObjectURL=original;}
});
