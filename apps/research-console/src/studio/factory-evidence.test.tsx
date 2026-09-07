import {it,expect,vi} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {FactoryEvidence} from './FactoryEvidence';
import type {FactoryPayload} from '@/lib/types/ai-factory';
it('activates observations only for a completed model and routes each action',()=>{
 const action=vi.fn();const data={selected:{state:'COMPLETED',result:{}},trials:[],forward:null,assessment:null,verification:null,pipeline:null} as unknown as FactoryPayload;
 render(<FactoryEvidence data={data} busy={false} action={action}/>);
 fireEvent.click(screen.getByRole('tab',{name:'前向观察'}));
 fireEvent.click(screen.getByRole('button',{name:'启用观察'}));expect(action).toHaveBeenCalledWith('observe');
 fireEvent.click(screen.getByRole('tab',{name:'压力与组合'}));
 fireEvent.click(screen.getByRole('button',{name:'运行诊断'}));expect(action).toHaveBeenCalledWith('assess');
 fireEvent.click(screen.getByRole('tab',{name:'运行与证据'}));
 fireEvent.click(screen.getByRole('button',{name:'准备 / 检查验证包'}));expect(action).toHaveBeenCalledWith('verify');
});
it('does not fabricate predictions or enable an unfinished model',()=>{
 render(<FactoryEvidence data={null} busy={false} action={vi.fn()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'前向观察'}));
 expect(screen.getByRole('button',{name:'启用观察'})).toBeDisabled();
 expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
