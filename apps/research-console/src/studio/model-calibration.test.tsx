import {it,expect} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {ModelCalibration} from './ModelCalibration';
import type {LifecycleModel} from '@/lib/types/ai-factory-lifecycle';
it('keeps missing observations empty',()=>{
 render(<ModelCalibration model={undefined}/>);
 expect(screen.getByText('等待真实成熟标签')).toBeInTheDocument();
 expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
it('shows calibrated units and exports inspectable real observations',()=>{
 const model={attempt:1,labels:20,diagnostics:{count:1,mae:.01,bias:-.01,directional_count:1,directional_accuracy:1,
  observations:[{time:0,prediction:.01,actual:.02,residual:.01}],
  calibration:[{lower:.005,upper:null,count:1,prediction:.01,actual:.02,rmse:.01}]}} as LifecycleModel;
 render(<ModelCalibration model={model}/>);
 expect(screen.getByText('100.0%')).toBeInTheDocument();
 expect(screen.getByText(/最近 1 条成熟标签，总成熟标签 20 条/)).toBeInTheDocument();
 expect(screen.getAllByRole('img')).toHaveLength(3);
 fireEvent.click(screen.getAllByRole('button',{name:'数据表'})[0]);
 expect(screen.getByRole('region',{name:'预测与实际四小时收益数据表'})).toHaveTextContent('200.0000');
});
