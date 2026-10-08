import {afterEach,expect,it} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import {DemoBackupStatus} from './DemoBackupStatus';
import {backupStatusValid} from '@/lib/api/demo-backup-status-contract';
import type {DemoBackupReport} from '@/lib/types/demo-backup-status';
afterEach(cleanup);
const now=1800000000000;
function report():DemoBackupReport {
  return {schema:'DEMO_BACKUP_STATUS_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',
    source_commit:'c'.repeat(40),observed_at_ms:now,phase:'PARTIAL_RECOVERY_POINTS',
    source_verified:false,submission_enabled:false,economic_authority:false,cross_namespace_atomic:false,production_restore_performed:false,
    components:[{namespace:'research',status:'OBSERVED',capture_timer:'ACTIVE',seal_timer:'INACTIVE',summary:{
      schema:'DEMO_CHECKPOINT_CYCLE_STATUS_V1',namespace:'research',observed_at_ms:now,status:'CURRENT',
      recovery_point_at_ms:now-120000,last_verified_at_ms:now-60000,rpo_seconds:86400,source_capture_age_ms:120000,
      capture_slots_remaining:508,verified_cycles:1,pending_cycles:1,failed_cycles:1,interrupted_cycles:1,seal_failures:2,
      archive_bytes_reverified:false,automatic_history_deletion:false,source_verified:false,submission_enabled:false,
      economic_authority:false,cross_namespace_atomic:false,production_restore_performed:false}},
      ...(['executor','issuer','controller'] as const).map(namespace=>({namespace,status:'NO_SPOOL' as const,
        summary:null,capture_timer:'NOT_INSTALLED' as const,seal_timer:'NOT_INSTALLED' as const}))]};
}
it('keeps missing backup evidence unknown and exposes no restore or activation action',()=>{
  render(<DemoBackupStatus snapshot={{status:'NO_SNAPSHOT',report:null}}/>);
  expect(screen.getByText('尚无周期备份记录')).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
it('separates current recovery point, inactive sealing, and retained failed or interrupted cycles',()=>{
  const raw=report();expect(backupStatusValid({status:'AVAILABLE',report:raw},now)).toBe(true);
  render(<DemoBackupStatus snapshot={{status:'AVAILABLE',report:raw}} asOf={now}/>);
  expect(screen.getByText('1 / 4')).toBeInTheDocument();
  expect(screen.getByText('封装未运行')).toBeInTheDocument();
  expect(screen.getByRole('meter',{name:'研究数据恢复点年龄'})).toHaveAttribute('aria-valuenow','120000');
  expect(screen.getByRole('table',{name:'备份周期明细'})).toBeInTheDocument();
  expect(screen.getByText(/中断和失败继续保留/)).toBeInTheDocument();
});
it('recomputes recovery point expiry and preserves the snapshot on refresh failure',()=>{
  const raw=report();const view=render(<DemoBackupStatus snapshot={{status:'AVAILABLE',report:raw}} asOf={now+86400000}/>);
  expect(screen.getByText('恢复点已过期')).toBeInTheDocument();
  expect(screen.getByText('有效恢复点覆盖').parentElement).toHaveTextContent('0 / 4');
  view.rerender(<DemoBackupStatus snapshot={{status:'STALE',report:raw}} asOf={now+86400000} refreshFailed/>);
  expect(screen.getByRole('status')).toHaveTextContent('刷新失败');
  expect(screen.getByRole('table',{name:'备份周期明细'})).toBeInTheDocument();
});
it('rejects fabricated coverage, future points, unexpected fields and unsafe numbers',()=>{
  const raw=report();
  for (const patch of [{phase:'ALL_RECOVERY_POINTS_CURRENT'},{private_key:'DO_NOT_DISPLAY'},
    {economic_authority:true},{observed_at_ms:now+1}]) {
    expect(backupStatusValid({status:'AVAILABLE',report:{...raw,...patch}},now)).toBe(false);
  }
  for (const patch of [{capture_slots_remaining:512},{verified_cycles:0},{last_verified_at_ms:now+1},
    {source_capture_age_ms:0},{status:'STALE'},{seal_failures:Number.MAX_SAFE_INTEGER+1},{archive_bytes_reverified:true}]) {
    const changed={...raw,components:raw.components.map((row,i)=>i?row:{...row,summary:{...row.summary,...patch}})};
    expect(backupStatusValid({status:'AVAILABLE',report:changed},now)).toBe(false);
  }
  expect(backupStatusValid({status:'AVAILABLE',report:raw},now+1800001)).toBe(false);
  expect(backupStatusValid({status:'DATA_BLOCKED',report:raw},now)).toBe(false);
});
