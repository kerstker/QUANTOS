"""Exact balance bridge over a complete bounded ledger query, not trading authority."""
from decimal import Context, Decimal, localcontext
import re

from services.data.kraken_account import payload, number, canonical
from services.data.kraken_public import require


def reconcile(opening, closing, pages, *, as_of_ms):
    require(type(as_of_ms) is int and as_of_ms>0,'KRAKEN_CASH_TIME')
    for account in (opening,closing):
        require(account.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1' and
                account.get('quantity_basis')=='base' and account.get('status')=='OBSERVED_STABLE' and
                not account['issues'] and account.get('submission_enabled') is False and
                account.get('authoritative') is False,'KRAKEN_CASH_ACCOUNT')
        require(len(account['reads'])==4 and type(account['observed_at_ms']) is int and
                type(account['reads'][0]['requested_at_ms']) is int and
                0<account['reads'][0]['requested_at_ms']<=account['observed_at_ms'],'KRAKEN_CASH_ACCOUNT_TIME')
    ref=opening['account_ref_sha256']
    require(ref==closing['account_ref_sha256'],'KRAKEN_CASH_ACCOUNT_CHANGED')
    first=opening['reads'][0]['requested_at_ms'];last=closing['observed_at_ms']
    require(first<=opening['observed_at_ms']<closing['reads'][0]['requested_at_ms']<=last<=as_of_ms and
            last-first<=86400000,'KRAKEN_CASH_WINDOW')
    start=first//1000;end=(last+999)//1000
    require(isinstance(pages,list) and 2<=len(pages)<=200,'KRAKEN_CASH_PAGES')
    counts={};offsets={};rows={};previous=end*1000
    with localcontext(Context(prec=160)):
        for page in pages:
            p=page['params'];cls=p['aclass']
            require(cls in ('currency','tokenized_asset') and p=={'asset':'all','aclass':cls,'type':'all',
                    'start':start,'end':end,'ofs':offsets.get(cls,0),'rebase_multiplier':'base'} and
                    page['endpoint']=='Ledgers' and page['account_ref_sha256']==ref,'KRAKEN_CASH_QUERY_SCOPE')
            sent,received=page['requested_at_ms'],page['received_at_ms']
            require(type(sent) is int and type(received) is int and previous<=sent<=received<=as_of_ms and
                    received-sent<=30000,'KRAKEN_CASH_QUERY_TIME');previous=received
            value=payload(page['body']);count=value.get('count');entries=value.get('ledger')
            require(type(count) is int and 0<=count<=5000 and isinstance(entries,dict),'KRAKEN_CASH_PAGE_SHAPE')
            require(cls not in counts or counts[cls]==count,'KRAKEN_CASH_COUNT_CHANGED')
            require(cls not in counts or offsets[cls]<count,'KRAKEN_CASH_EXTRA_PAGE')
            require(len(entries)==min(50,count-offsets.get(cls,0)),'KRAKEN_CASH_SHORT_PAGE')
            counts[cls]=count;offsets[cls]=offsets.get(cls,0)+len(entries)
            for lid,row in entries.items():
                require(isinstance(lid,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}',lid) and lid not in rows,'KRAKEN_CASH_LEDGER_ID')
                require(row['aclass']==cls and isinstance(row['asset'],str) and
                        re.fullmatch(r'[A-Za-z0-9._]{1,48}',row['asset']),'KRAKEN_CASH_ASSET')
                require(isinstance(row['refid'],str) and re.fullmatch(r'[A-Za-z0-9_-]{0,80}',row['refid']) and
                        isinstance(row['type'],str) and re.fullmatch(r'[a-z_]{1,40}',row['type']),'KRAKEN_CASH_REFERENCE')
                ts=number(row['time']);require(start<ts<=end,'KRAKEN_CASH_EVENT_WINDOW')
                rows[lid]={'ledger_id':lid,'refid':row['refid'],'asset':row['asset'],'asset_class':cls,
                    'type':row['type'],'time_seconds':canonical(ts),'amount':canonical(number(row['amount'],signed=True)),
                    'fee':canonical(number(row['fee'],signed=True)),'balance':canonical(number(row['balance'],signed=True))}
        require(set(counts)=={'currency','tokenized_asset'} and offsets==counts,'KRAKEN_CASH_INCOMPLETE_PAGES')
        issues=[];assets={};grouped={}
        for row in rows.values():
            at=number(row['time_seconds'])*1000
            if first<=at<=opening['observed_at_ms'] or closing['reads'][0]['requested_at_ms']<=at<=last:
                issues.append('ACTIVITY_DURING_ACCOUNT_SNAPSHOT:'+row['ledger_id'])
            if opening['observed_at_ms']<at<closing['reads'][0]['requested_at_ms']:
                grouped.setdefault(row['asset'],[]).append(row)
        for asset in sorted(set(opening['balances'])|set(closing['balances'])|set(grouped)):
            begin=opening['balances'].get(asset,{});finish=closing['balances'].get(asset,{})
            for account_balance in (begin,finish):
                require(number(account_balance.get('credit_used','0'))==0,'KRAKEN_CASH_CREDIT')
            initial=number(begin.get('balance','0'),signed=True);actual=number(finish.get('balance','0'),signed=True)
            expected=initial;fees=Decimal(0);prior_time=None
            for row in sorted(grouped.get(asset,[]),key=lambda r:(number(r['time_seconds']),r['ledger_id'])):
                at=number(row['time_seconds'])
                if at==prior_time:issues.append('AMBIGUOUS_LEDGER_ORDER:'+asset)
                prior_time=at;fee=number(row['fee'],signed=True);fees+=fee
                expected+=number(row['amount'],signed=True)-fee
                if expected!=number(row['balance'],signed=True):issues.append('LEDGER_BALANCE_CHAIN_MISMATCH:'+row['ledger_id'])
            drift=actual-expected
            if drift!=0:issues.append('BALANCE_DRIFT:'+asset)
            assets[asset]={'opening':canonical(initial),'expected_closing':canonical(expected),'observed_closing':canonical(actual),
                           'movement':canonical(expected-initial),'fees':canonical(fees),'drift':canonical(drift)}
    return {'schema':'KRAKEN_BALANCE_BRIDGE_V1','status':'UNRESOLVED' if issues else 'BALANCE_MOVEMENTS_MATCHED',
        'movement_start_exclusive_ms':opening['observed_at_ms'],
        'movement_end_exclusive_ms':closing['reads'][0]['requested_at_ms'],
        'account_ref_sha256':ref,'start_seconds':start,'end_seconds':end,'as_of_ms':as_of_ms,
        'quantity_basis':'base','assets':assets,'ledger_entries':[rows[k] for k in sorted(rows)],'issues':sorted(set(issues)),
        'scope':'OBSERVED_DEFAULT_WALLET_BALANCE_MOVEMENTS','full_account_scope_verified':False,
        'trade_linkage_verified':False,'next_cycle_allowed':False,'submission_enabled':False,'authoritative':False}
