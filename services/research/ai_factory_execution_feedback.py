"""Expose canonical replay receipts without turning simulations into venue fills."""
from collections import Counter


def execution_feedback(result):
    reconciliation=result['reconciliation']
    if result.get('cash_model')!='CANONICAL_LINEAR_USDC_PERPETUAL' or reconciliation.get('residual')!='0':
        raise ValueError('canonical reconciled replay required')
    return {'schema':'QUANTOS_FACTORY_EXECUTION_FEEDBACK_V1','evidence_type':'TECHNICAL_REPLAY',
            'venue_receipts_verified':False,'economic_eligible':False,
            'reconciliation':dict(reconciliation),'order_counts':dict(Counter(r['status'] for r in result['orders'])),
            'fill_count':len(result['fills']),'order_count':len(result['orders']),
            'recent_fills':result['fills'][-32:],'recent_orders':result['orders'][-32:],
            'halted_at':result.get('halted_at'),'average_exposure':result.get('average_exposure'),
            'limitations':['SIMULATED_EXECUTION_NOT_VENUE_TCA','SLIPPAGE_EMBEDDED_IN_TRADE_PRICE_NOT_DEDUCTED_TWICE']}
