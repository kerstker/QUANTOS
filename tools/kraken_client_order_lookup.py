"""Persistent client-ID discovery hints for the existing read-only order tracker."""
from copy import deepcopy
from pathlib import Path

from services.data import kraken_client_order_lookup as model
from services.data.kraken_account import MAX_BYTES, payload
from services.data.kraken_public import require
from tools.kraken_inventory_report import load, sealed
from tools.kraken_public_capture import atomic_json, digest


def collect(client, request, *, clock_ms):
    reads=[]
    for endpoint,params in model.requests(request):
        sent=clock_ms();body=client.read(endpoint,params);received=clock_ms()
        require(isinstance(body,bytes) and len(body)<=MAX_BYTES,'KRAKEN_CLIENT_LOOKUP_SIZE')
        text=body.decode('utf-8');payload(text)
        reads.append({'endpoint':endpoint,'params':params,'body':text,
                      'account_ref_sha256':client.account_ref_sha256,'requested_at_ms':sent,'received_at_ms':received})
    report=model.assess(reads,request,as_of_ms=clock_ms())
    evidence={'schema':'KRAKEN_CLIENT_LOOKUP_EVIDENCE_V1','request':request,'reads':reads,'report':report,
              'submission_enabled':False,'authoritative':False}
    evidence['result_hash']=digest(evidence)
    return evidence


def hints(client, root, *, requests=None, retired=(), clock_ms):
    """Caller owns .order-query.lock and has invalidated old recovery success."""
    root=Path(root);config_path=root/'client-order-requests.json'
    required=root/'client-order-recovery-required.json';cache=root/'client-order-resolutions'
    for path in (config_path,required,cache):
        require(not path.is_symlink(),'KRAKEN_CLIENT_LOOKUP_SYMLINK')
    require(config_path.exists() or not (required.exists() or cache.exists()),'KRAKEN_CLIENT_LOOKUP_CONFIG_MISSING')
    ref=client.account_ref_sha256
    if config_path.exists():
        config=sealed(load(config_path))
        require(config.get('schema')=='KRAKEN_CLIENT_LOOKUP_CONFIG_V1' and config['account_ref_sha256']==ref and
                isinstance(config['requests'],dict) and 1<=len(config['requests'])<=10000,'KRAKEN_CLIENT_LOOKUP_CONFIG')
        for key,request in config['requests'].items():
            model.requests(request);require(key==request['client_order_id'],'KRAKEN_CLIENT_LOOKUP_CONFIG_KEY')
    else:
        config={'schema':'KRAKEN_CLIENT_LOOKUP_CONFIG_V1','account_ref_sha256':ref,'requests':{},
                'submission_enabled':False,'authoritative':False}
    if requests is not None:
        require(isinstance(requests,list) and 1<=len(requests)<=32,'KRAKEN_CLIENT_LOOKUP_REQUEST_LIMIT')
        for request in deepcopy(requests):
            model.requests(request);key=request['client_order_id']
            require(key not in config['requests'] or config['requests'][key]==request,'KRAKEN_CLIENT_LOOKUP_REQUEST_CHANGED')
            config['requests'][key]=request
        require(len(config['requests'])<=10000,'KRAKEN_CLIENT_LOOKUP_HISTORY_LIMIT')
        config['result_hash']=digest({k:v for k,v in config.items() if k!='result_hash'})
        atomic_json(config_path,config)
    if not config['requests']:return
    binding={'account_ref_sha256':ref,'required':True}
    if required.exists():require(load(required)==binding,'KRAKEN_CLIENT_LOOKUP_BINDING')
    else:atomic_json(required,binding)
    cache.mkdir(mode=0o700,exist_ok=True)
    for request in config['requests'].values():
        path=cache/(digest(request)+'.json')
        require(not path.is_symlink(),'KRAKEN_CLIENT_LOOKUP_SYMLINK')
        if path.exists():
            evidence=sealed(load(path))
            require(evidence.get('schema')=='KRAKEN_CLIENT_LOOKUP_EVIDENCE_V1' and evidence['request']==request,
                    'KRAKEN_CLIENT_LOOKUP_CACHE_BINDING')
            report=model.assess(evidence['reads'],request,as_of_ms=evidence['report']['as_of_ms'])
            require(report==evidence['report'] and report['account_ref_sha256']==ref,'KRAKEN_CLIENT_LOOKUP_CACHE_REPLAY')
        else:
            evidence=collect(client,request,clock_ms=clock_ms);report=evidence['report']
            require(report['status']=='ORDER_ID_OBSERVED','KRAKEN_CLIENT_LOOKUP_UNRESOLVED')
            atomic_json(path,evidence)
        require(report['status']=='ORDER_ID_OBSERVED','KRAKEN_CLIENT_LOOKUP_UNRESOLVED')
        if report['order_id'] not in retired:yield {report['order_id']}
