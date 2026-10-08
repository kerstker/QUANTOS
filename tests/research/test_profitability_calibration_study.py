import json
from decimal import Decimal
from tests.execution.test_perpetual_public_replay import capture_fixture,START
from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_simulation import SimulationCostModel
from services.research.profitability_calibration import decode_actions
from services.research.profitability_fixed_replay import run_fixed_actions
from tools.profitability_calibration_study import calibrate


def test_late_fill_error_cannot_change_calibration_profile(tmp_path):
    capture=capture_fixture();prepared=prepare_public_replay(capture)
    actions=[{'at_ms':START+offset+2,'kind':'ORDER','id':name,'symbol':'BTCUSDT','quantity':'0.1'}
             for offset,name in [(0,'first'),(2000,'later')]]
    simulation=run_fixed_actions(schedule=decode_actions(actions),release_identity={'source':'regression'},
        dataset_identity=prepared['manifest'],contract_rules=prepared['contract_rules'],
        funding_policies=prepared['funding_policies'],order_rule_profiles=prepared['order_rule_profiles'],
        frames=prepared['frames'],initial_collateral=Decimal('10000'),costs=SimulationCostModel(fee_rate=Decimal('.0005')))
    data={'capture':capture,'actions':actions,'calibration_cutoff_ms':START+1502,'observed':simulation['trajectory']}
    source=tmp_path/'source.json';source.write_text(json.dumps(data))
    first=calibrate(source,tmp_path/'first')
    assert first['trials_attempted']==3 and first['selected_profile']==0
    assert first['heldout']['paired_orders']==1
    assert first['venue_receipts_verified'] is False and first['calibration_complete'] is False
    data['observed']['orders'][1]['events'][-1].update(state='PARTIAL',filled='0.05')
    changed=tmp_path/'changed.json';changed.write_text(json.dumps(data))
    second=calibrate(changed,tmp_path/'second')
    assert second['selected_profile']==first['selected_profile']
    assert second['heldout']['quantity_absolute_error']=='0.05'
