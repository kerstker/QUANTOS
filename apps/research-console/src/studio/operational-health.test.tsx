import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {OperationalHealth} from './OperationalHealth';
import type {ServiceHealth} from '@/lib/types/research';
it('shows failed recovery timings without treating overlapping stages as successful recovery',()=>{
 const service:ServiceHealth={service:'l2-backup-restore',status:'down',latency_ms:null,error_rate:null,
 reason_code:'RESTORE_RTO_EXCEEDED',restore_diagnostics:{scope:'RESTORE_PERFORMANCE_DIAGNOSTIC_ONLY',
 total_ms:310000,completed:false,stages:[{stage:'ARTIFACT_RESTORE',elapsed_ms:310000,completed:false}]}};
 const view=render(<OperationalHealth services={[service]}/>);
 expect(screen.getByText('查看恢复耗时')).toBeInTheDocument();
 expect(screen.getByText(/本次总耗时 310\.00 秒/)).toBeInTheDocument();
 expect(screen.getByText(/并行阶段有重叠/)).toBeInTheDocument();
 expect(screen.getByText('恢复未完成')).toBeInTheDocument();
 view.rerender(<OperationalHealth services={[{...service,restore_diagnostics:null}]}/>);
 expect(screen.queryByText('查看恢复耗时')).not.toBeInTheDocument();
 expect(screen.getByText('本次没有可用的分阶段耗时')).toBeInTheDocument();
});
