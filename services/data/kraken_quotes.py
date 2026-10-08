"""Receipt-time indicative quotes. REST ticker has no source event timestamp."""
from decimal import Context, localcontext

from services.data.kraken_public import decimal, require, result
from services.data.kraken_units import canonical


def parse(payload, *, spec, received_at_ms, as_of_ms):
    require(type(received_at_ms) is int and type(as_of_ms) is int and
            0<received_at_ms<=as_of_ms<=received_at_ms+120000,'KRAKEN_QUOTE_TIME')
    rows=result(payload)
    require(set(rows)=={spec['pair_key']},'KRAKEN_QUOTE_IDENTITY')
    row=rows[spec['pair_key']]
    require(isinstance(row,dict),'KRAKEN_QUOTE_SHAPE')
    values={}
    for key in ('a','b'):
        require(isinstance(row.get(key),list) and len(row[key])==3,'KRAKEN_QUOTE_SHAPE')
        values[key]=decimal(row[key][0])
        decimal(row[key][1],positive=False);decimal(row[key][2])
    require(values['b']<=values['a'],'KRAKEN_QUOTE_CROSSED')
    require(spec['asset_class'] in ('tokenized_asset','currency'),'KRAKEN_QUOTE_CLASS')
    tokenized=spec['asset_class']=='tokenized_asset'
    with localcontext(Context(prec=100)):
        mid=(values['a']+values['b'])/2
    return {'instrument_id':spec['instrument_id'],'base':spec['base'],'quote':spec['quote'],
            'bid':canonical(values['b']),'ask':canonical(values['a']),'mid':canonical(mid),
            'price_quantity_basis':'UNDERLYING_EQUITY' if tokenized else 'NATIVE_ASSET',
            'basis_evidence':'INFERRED_FROM_KRAKEN_XSTOCKS_FAQ' if tokenized else 'SPOT_PAIR_BASE',
            'received_at_ms':received_at_ms,'source_event_time_ms':None,'as_of_ms':as_of_ms,
            'scope':'INDICATIVE_QUOTE_ONLY','execution_eligible':False,'authoritative':False}
