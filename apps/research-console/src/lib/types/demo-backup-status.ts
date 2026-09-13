export type DemoBackupNamespace='research'|'executor'|'issuer'|'controller';
export type DemoBackupTimer='ACTIVE'|'INACTIVE'|'NOT_INSTALLED'|'UNKNOWN';
export interface DemoBackupSummary {
  schema:'DEMO_CHECKPOINT_CYCLE_STATUS_V1'; namespace:DemoBackupNamespace; observed_at_ms:number;
  status:'CURRENT'|'STALE'|'NO_VERIFIED_BACKUP'; recovery_point_at_ms:number|null;
  last_verified_at_ms:number|null; rpo_seconds:number; source_capture_age_ms:number|null;
  capture_slots_remaining:number; verified_cycles:number; pending_cycles:number; failed_cycles:number;
  interrupted_cycles:number; seal_failures:number; archive_bytes_reverified:false; automatic_history_deletion:false;
  source_verified:false; submission_enabled:false; economic_authority:false; cross_namespace_atomic:false; production_restore_performed:false;
}
export interface DemoBackupComponent {
  namespace:DemoBackupNamespace;
  status:'OBSERVED'|'NO_SPOOL'|'NO_BACKUP_DIRECTORY'|'UNREADABLE'|'BACKUP_VOLUME_UNAVAILABLE';
  summary:DemoBackupSummary|null; capture_timer:DemoBackupTimer; seal_timer:DemoBackupTimer;
}
export interface DemoBackupReport {
  schema:'DEMO_BACKUP_STATUS_V1'; environment:'BINANCE_USDM_DEMO'; scope:'TECHNICAL_VALIDATION_ONLY';
  source_commit:string; observed_at_ms:number;
  phase:'ALL_RECOVERY_POINTS_CURRENT'|'PARTIAL_RECOVERY_POINTS'|'NO_CURRENT_RECOVERY_POINTS';
  components:DemoBackupComponent[]; source_verified:false; submission_enabled:false;
  economic_authority:false; cross_namespace_atomic:false; production_restore_performed:false;
}
export type DemoBackupSnapshot={status:'AVAILABLE'|'STALE';report:DemoBackupReport}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null};
