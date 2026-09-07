import {fireEvent,render,screen} from '@testing-library/react';
import {it,expect} from 'vitest';
import {Chart} from './Charts';
it('keeps legend controls reachable after hiding every series',()=>{
 render(<Chart title="测试曲线" rows={[{label:'day1',value:1},{label:'day2',value:2}]} series={[{key:'value',label:'真实系列'}]}/>);
 fireEvent.click(screen.getByRole('button',{name:'真实系列'}));
 fireEvent.click(screen.getByRole('button',{name:'真实系列'}));
 expect(screen.getByRole('img')).toBeInTheDocument();
});
it('lets keyboard users inspect data and switch to its table',()=>{
 render(<Chart title="测试曲线" rows={[{label:'day1',value:1},{label:'day2',value:2}]} series={[{key:'value',label:'真实系列'}]}/>);
 fireEvent.change(screen.getByRole('slider'),{target:{value:'0'}});
 expect(screen.getByText('day1',{selector:'strong'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'数据表'}));
 expect(screen.getByRole('table')).toBeInTheDocument();
});
it('returns from expanded chart with Escape',()=>{
 render(<Chart title="测试曲线" rows={[{label:'day1',value:1}]} series={[{key:'value',label:'真实系列'}]}/>);
 fireEvent.click(screen.getByRole('button',{name:'放大测试曲线'}));
 fireEvent.keyDown(screen.getByRole('button',{name:'收起测试曲线'}),{key:'Escape'});
 expect(screen.getByRole('button',{name:'放大测试曲线'})).toBeInTheDocument();
});
