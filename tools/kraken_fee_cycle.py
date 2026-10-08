"""Persisted fee refresh configuration and raw replay for human account sessions."""
from pathlib import Path
import re

from services.data.kraken_public import require
from tools import kraken_fee_observation as observation
from tools import kraken_inventory_report as inventory
from tools import kraken_public_capture as capture


def _paths(root):
    root=Path(root).absolute()
    require(root.resolve()==root,'KRAKEN_FEE_STATE_SYMLINK')
    for name in ('fee-observation-config.json','fee-observation.json','fee-queries'):
        require(not (root/name).is_symlink(),'KRAKEN_FEE_STATE_SYMLINK')
    return root


def configure(root, account_ref, market_root=None):
    root=_paths(root);path=root/'fee-observation-config.json'
    prior=inventory.sealed(inventory.load(path)) if path.exists() else None
    if prior is None:
        require(not (root/'fee-observation.json').exists() and not (root/'fee-queries').exists(),
                'KRAKEN_FEE_CONFIG_MISSING')
        if market_root is None:return None
    else:
        require(prior['schema']=='KRAKEN_FEE_CONFIG_V1' and prior['account_ref_sha256']==account_ref,
                'KRAKEN_FEE_CONFIG_ACCOUNT')
        if market_root is None:market_root=prior['market_root']
    market=Path(market_root).absolute()
    require(market.resolve()==market and market.is_dir(),'KRAKEN_FEE_MARKET_ROOT')
    config={'schema':'KRAKEN_FEE_CONFIG_V1','account_ref_sha256':account_ref,'market_root':str(market),
            'submission_enabled':False,'authoritative':False}
    config['result_hash']=capture.digest(config)
    if prior is not None:require(prior==config,'KRAKEN_FEE_CONFIG_CHANGED')
    else:capture.atomic_json(path,config)
    return config


def pending(root, account_ref, *, as_of_ms):
    root=_paths(root)
    report={'schema':'KRAKEN_FEE_CYCLE_V1','status':'UNRESOLVED','account_ref_sha256':account_ref,
            'as_of_ms':as_of_ms,'evidence_hashes':{},'submission_enabled':False,'authoritative':False}
    report['result_hash']=capture.digest(report)
    capture.atomic_json(root/'fee-observation.json',report)


def _read_archive(root, key):
    require(isinstance(key,str) and re.fullmatch('[a-f0-9]{64}',key),'KRAKEN_FEE_ARCHIVE_KEY')
    path=root/'fee-queries'/(key+'.json')
    require(not path.is_symlink(),'KRAKEN_FEE_ARCHIVE_SYMLINK')
    value=inventory.load(path)
    require(capture.digest(value)==key,'KRAKEN_FEE_ARCHIVE_HASH')
    return value


def review(root, account, *, as_of_ms):
    root=_paths(root);inventory.sealed(account)
    config=configure(root,account['account_ref_sha256'])
    if config is None:return None
    report=inventory.sealed(inventory.load(root/'fee-observation.json'))
    require(report['schema']=='KRAKEN_FEE_CYCLE_V1' and report['status']=='OBSERVED_FEE_SCHEDULES' and
            report['account_ref_sha256']==account['account_ref_sha256'] and report['config_hash']==config['result_hash'] and
            report['account_source_hash']==account['result_hash'],'KRAKEN_FEE_CYCLE_BINDING')
    require(type(as_of_ms) is int and type(report['as_of_ms']) is int and
            0<=as_of_ms-report['as_of_ms']<=120000 and account['status']=='OBSERVED_STABLE' and
            not account['issues'] and 0<=as_of_ms-account['observed_at_ms']<=120000,'KRAKEN_FEE_CYCLE_STALE')
    require(isinstance(report['evidence_hashes'],dict) and 1<=len(report['evidence_hashes'])<=32,'KRAKEN_FEE_CYCLE_SIZE')
    for instrument,key in report['evidence_hashes'].items():
        raw=_read_archive(root,key)
        fee=observation.replay(raw['fee'],market=raw['market'],account_ref=account['account_ref_sha256'],as_of_ms=as_of_ms)
        require(fee['instrument_id']==instrument,'KRAKEN_FEE_CYCLE_PRODUCT')
    return report


def refresh(client, root, config, account, *, clock_ms):
    # The account-session caller owns .session.lock across refresh/publication.
    root=_paths(root);market=Path(config['market_root'])
    require(configure(root,client.account_ref_sha256)==config,'KRAKEN_FEE_CONFIG_CHANGED')
    pending(root,client.account_ref_sha256,as_of_ms=clock_ms())
    observer=inventory.sealed(inventory.load(market/'status.json'))
    require(observer['schema'] in ('KRAKEN_PORTFOLIO_OBSERVER_V2','KRAKEN_PORTFOLIO_OBSERVER_V3') and
            observer['status']=='CAPTURED' and 1<=len(observer['instruments'])<=32 and
            0<=clock_ms()-observer['observed_at_ms']<=120000,'KRAKEN_FEE_OBSERVER_UNRESOLVED')
    hashes={}
    for item in observer['instruments']:
        require(item['status']=='CAPTURED','KRAKEN_FEE_OBSERVER_PARTIAL')
        identity={k:item[k] for k in ('pair','asset_class')};key=item['result_hash']
        require(isinstance(key,str) and re.fullmatch('[a-f0-9]{64}',key),'KRAKEN_FEE_CAPTURE_KEY')
        path=market/'instruments'/capture.digest(identity)/'captures'/(key+'.json')
        require(path.resolve()==path,'KRAKEN_FEE_CAPTURE_SYMLINK')
        raw=capture.validate(inventory.load(path))
        require(raw['result_hash']==key and all(raw[k]==v for k,v in identity.items()) and
                raw['completed_at_ms']<=observer['observed_at_ms'],'KRAKEN_FEE_CAPTURE_BINDING')
        instrument=raw['instrument']['instrument_id']
        require(instrument not in hashes,'KRAKEN_FEE_DUPLICATE_PRODUCT')
        fee=observation.collect(client,raw,clock_ms=clock_ms)
        evidence={'market':raw,'fee':fee};key=capture.digest(evidence)
        directory=root/'fee-queries';directory.mkdir(mode=0o700,exist_ok=True)
        target=directory/(key+'.json');require(not target.is_symlink(),'KRAKEN_FEE_ARCHIVE_SYMLINK')
        if target.exists():require(_read_archive(root,key)==evidence,'KRAKEN_FEE_ARCHIVE_CONFLICT')
        else:capture.atomic_json(target,evidence)
        hashes[instrument]=key
    report={'schema':'KRAKEN_FEE_CYCLE_V1','status':'OBSERVED_FEE_SCHEDULES',
        'account_ref_sha256':client.account_ref_sha256,'account_source_hash':account['result_hash'],
        'config_hash':config['result_hash'],'observer_source_hash':observer['result_hash'],
        'as_of_ms':clock_ms(),'evidence_hashes':hashes,'submission_enabled':False,'authoritative':False}
    # Replay all accumulated reads at the final time before publishing success.
    require(account['status']=='OBSERVED_STABLE' and not account['issues'] and
            0<=report['as_of_ms']-account['observed_at_ms']<=120000,'KRAKEN_FEE_CYCLE_STALE')
    for key in hashes.values():
        raw=_read_archive(root,key)
        observation.replay(raw['fee'],market=raw['market'],account_ref=client.account_ref_sha256,as_of_ms=report['as_of_ms'])
    report['result_hash']=capture.digest(report)
    capture.atomic_json(root/'fee-observation.json',report)
    return report
