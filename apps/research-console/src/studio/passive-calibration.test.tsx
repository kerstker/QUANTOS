import {it,expect} from 'vitest';
import {render,screen,fireEvent} from '@testing-library/react';
import {PassiveCalibrationEvidence} from './PassiveCalibrationEvidence';
import type {PassiveReceiptCalibration} from '@/lib/types/ai-factory';

const report={start_ms:1000,end_ms:2000,paired_orders:1,submitted_orders:1,observed_events:2,simulated_events:2,
 matched_events:2,unmatched_events:0,unmatched_orders:[],unobserved_orders:[],coverage_gaps:[],
 final_quantity_absolute_error:'0',matched_event_quantity_absolute_error:'0.02',quantity_path_error_btc_ms:'2',
 increment_quantity_absolute_error:'0',carry_quantity_absolute_error:'0',venue_time_absolute_error_ms:10,
 receive_time_absolute_error_ms:20,state_mismatch_duration_ms:10,order_rows_total:1,
 rows:[{order_id:'fixed-a',side:'BUY',quantity:'0.1',observed_state:'FILLED',simulated_state:'FILLED',
  observed_censored:false,simulated_censored:false,final_quantity_absolute_error:'0',quantity_path_error_btc_ms:'2',
  venue_time_absolute_error_ms:10,receive_time_absolute_error_ms:20,path_points_total:2,
  path:[{received_ms:1000,observed_filled:'0.05',simulated_filled:'0.03'},{received_ms:2000,observed_filled:'0.1',simulated_filled:'0.1'}]}]};
const data:PassiveReceiptCalibration={status:'HELDOUT_ERRORS_RECORDED_NOT_VENUE_ACCEPTANCE',selected_profile_ms:25,
 equally_best_profiles_ms:[25],cutoff_ms:1500,window_start_ms:1000,window_end_ms:2000,
 partition_clock:'ACTUAL_CLIENT_RECEIPT_TIME',trials_attempted:3,
 observed_provenance:{kind:'SYNTHETIC_TEST_FIXTURE',declared_source_sha256:'a'.repeat(64)},
 trials:[{profile_ms:0,status:'COMPLETED',fit:{...report,rows:[]}},{profile_ms:25,status:'COMPLETED',fit:{...report,rows:[]}},
 {profile_ms:100,status:'FAILED',reason:'ValueError'}],heldout:report,calibration_complete:false,venue_receipts_verified:false,queue_model_calibrated:false};

it('shows separate receipt and venue errors plus heldout fill paths with honest provenance',()=>{
 render(<PassiveCalibrationEvidence data={data}/>);
 expect(screen.getByText(/合成测试轨迹/)).toBeInTheDocument();
 expect(screen.getByRole('region',{name:'被动校准段参数比较'})).toHaveTextContent('25 ms');
 expect(screen.getByRole('region',{name:'被动留出段误差'})).toHaveTextContent('2');
 expect(screen.getByText(/已冻结 25 ms/)).toBeInTheDocument();
 expect(screen.getByLabelText('留出订单')).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('留出订单'),{target:{value:'fixed-a'}});
 expect(screen.getByRole('heading',{name:'累计成交数量 · 回执与模型'})).toBeInTheDocument();
 expect(screen.getByText(/真实场所校准尚未验收/)).toBeInTheDocument();
});

it('does not turn ambiguous fits or missing heldout into a selected model',()=>{
 render(<PassiveCalibrationEvidence data={{...data,status:'AMBIGUOUS_PREFIX_FIT',selected_profile_ms:null,
  equally_best_profiles_ms:[0,25],heldout:null}}/>);
 expect(screen.getByText(/多组参数无法区分/)).toBeInTheDocument();
 expect(screen.queryByRole('region',{name:'被动留出段误差'})).not.toBeInTheDocument();
 expect(screen.queryByText(/已冻结 25 ms/)).not.toBeInTheDocument();
});

it('keeps a frozen selection visible when its heldout replay failed',()=>{
 render(<PassiveCalibrationEvidence data={{...data,status:'HELDOUT_REPLAY_FAILED',heldout:null}}/>);
 expect(screen.getByText(/后段回放失败/)).toBeInTheDocument();
 expect(screen.getByText(/已冻结 25 ms/)).toBeInTheDocument();
});
