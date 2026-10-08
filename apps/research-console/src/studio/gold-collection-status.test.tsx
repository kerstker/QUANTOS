import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {GoldCollectionStatus} from './GoldCollectionStatus';
import type {FactoryLifecyclePayload} from '@/lib/types/ai-factory-lifecycle';
const rows=[{campaign_id:1,observation:{status:'FAILED',reason:'GOLD_SIGNAL_INPUT_INCOMPLETE',run_id:'run_failed',research_created:false,authoritative:false}}] as FactoryLifecyclePayload['collection_requests'];
it('shows failed capture before a campaign or result exists',()=>{
 render(<GoldCollectionStatus rows={rows}/>);
 expect(screen.getByText('采集失败')).toBeInTheDocument();
 expect(screen.getByText('预定决策时刻的数据覆盖不完整，未发布研究输入。')).toBeInTheDocument();
 expect(screen.getByText('本次尚未进入 AI 研究、回测或执行。')).toBeInTheDocument();
 expect(screen.getByText('任务 run_failed')).toBeInTheDocument();
});
it('keeps absent or unrecognized observations unknown',()=>{
 render(<GoldCollectionStatus rows={[{...rows[0],observation:undefined}]}/>);
 expect(screen.getByText('采集任务状态尚未确认')).toBeInTheDocument();
 expect(screen.queryByText('正在采集公开数据')).not.toBeInTheDocument();
});
