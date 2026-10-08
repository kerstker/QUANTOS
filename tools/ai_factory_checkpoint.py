"""Verified research-only checkpoint and isolated restore; no production restore path."""
from contextlib import closing
from pathlib import Path,PurePosixPath
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile

_SOURCE_SCOPES=('ai_factory','profitability','profitability_inputs')


def _sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def _copy_synced(source,destination):
    with source.open('rb') as src,destination.open('xb') as dst:
        os.chmod(destination,0o600)
        for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
        dst.flush();os.fsync(dst.fileno())


def publish_checkpoint(archive,destination):
    """Verify the bytes to be downloaded before atomically replacing the latest copy."""
    archive=Path(archive);destination=Path(destination)
    if archive.is_symlink() or destination.is_symlink() or archive.resolve()==destination.resolve():
        raise ValueError('distinct regular checkpoint archive and download required')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.checkpoint-',dir=destination.parent) as tmp:
        stage=Path(tmp);copy=stage/'archive.zip'
        _copy_synced(archive,copy)
        restored=restore_checkpoint(copy,stage/'verification')
        checksum=_sha(copy)
        if _sha(archive)!=checksum:raise ValueError('checkpoint archive changed during publication')
        replaced=not destination.exists() or _sha(destination)!=checksum
        if replaced:os.replace(copy,destination)
    return {'status':'VERIFIED_RESEARCH_DOWNLOAD','sha256':checksum,'files':restored['files'],
            'layout':restored['layout'],'replaced':replaced,'production_restore_performed':False}


def _files(base):
    paths=sorted(p for p in base.rglob('*') if p.suffix in ('.db','.json'))
    if len(paths)>100000:raise ValueError('checkpoint inventory exceeds bound')
    for path in paths:
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p!=base.parent):raise ValueError('unsafe checkpoint source')
        if not path.is_file():raise ValueError('checkpoint source not regular')
    return paths


def _inventory(base,schema):
    if schema==1:return _files(base)
    paths=[]
    for name in _SOURCE_SCOPES:
        paths.extend(_files(base/name))
    if len(paths)>100000:raise ValueError('checkpoint inventory exceeds bound')
    return sorted(paths)


def _verify_bundle(base,schema):
    _verify_state(base if schema==1 else base/'ai_factory')
    if schema==2:
        from services.research.trial_ledger import TrialLedger
        for path in (base/'profitability').rglob('trials.db'):
            with TrialLedger(str(path),create=False) as ledger:ledger.verify()


def create_checkpoint(root,destination):
    cache=Path(root)/'data/cache';destination=Path(destination)
    schema=2 if any((cache/name).exists() for name in ('profitability','profitability_inputs')) else 1
    base=cache if schema==2 else cache/'ai_factory'
    layout='CACHE_ROOT_V2' if schema==2 else 'FACTORY_ROOT_V1'
    source_roots=[base] if schema==1 else [base/name for name in _SOURCE_SCOPES]
    if destination.exists() or any(destination.resolve().is_relative_to(p.resolve()) for p in source_roots):
        raise ValueError('new destination outside checkpoint source scopes required')
    files=_inventory(base,schema)
    if not files:raise ValueError('no research checkpoint sources')
    before={str(p.relative_to(base)):_sha(p) for p in files if p.suffix=='.json'}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='quantos-research-checkpoint-') as tmp:
        stage=Path(tmp);entries={}
        for path in files:
            name=path.relative_to(base).as_posix();copy=stage/name;copy.parent.mkdir(parents=True,exist_ok=True)
            if path.suffix=='.db':
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(copy)) as dst:
                    src.backup(dst,pages=256,sleep=.01)
                    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('checkpoint database integrity failed')
            else:copy.write_bytes(path.read_bytes())
            entries[name]={'sha256':_sha(copy),'size':copy.stat().st_size}
        after_files=_inventory(base,schema)
        after={str(p.relative_to(base)):_sha(p) for p in after_files if p.suffix=='.json'}
        if before!=after:raise ValueError('research artifacts changed during checkpoint; retry')
        _verify_bundle(stage,schema)
        archive=stage/'checkpoint.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            for name in entries:z.write(stage/name,name)
            z.writestr('checkpoint-manifest.json',json.dumps({'schema':schema,'layout':layout,'entries':entries},sort_keys=True))
        # Publish only after a complete archive can be verified and restored elsewhere.
        restore_checkpoint(archive,stage/'verification')
        # Same-filesystem hard link publishes complete synced bytes without overwriting
        # an immutable archive created by another worker while this one was copying.
        with tempfile.TemporaryDirectory(prefix='.checkpoint-new-',dir=destination.parent) as tmp:
            copy=Path(tmp)/'archive.zip';_copy_synced(archive,copy)
            if _sha(copy)!=_sha(archive):raise ValueError('checkpoint publication content mismatch')
            os.link(copy,destination)
    return {'status':'VERIFIED_RESEARCH_CHECKPOINT','layout':layout,'files':len(entries),'sha256':_sha(destination),
            'credential_paths_included':False,'encrypted':False,'production_restore_performed':False}


def _verify_state(base):
    from services.research.ai_factory_store import FactoryStore
    from tools.btc_factory_research import verified
    path=base/'factory.db'
    if not path.exists():raise ValueError('factory database missing')
    obj=object.__new__(FactoryStore);obj.c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);obj.c.row_factory=sqlite3.Row
    try:
        sessions=[obj.get(row[0]) for row in obj.c.execute('SELECT attempt FROM sessions ORDER BY attempt')]
        receipts={row['attempt']:obj.trials(row['attempt']) for row in sessions}
        obj.verify_campaigns()
    finally:obj.c.close()
    from tools.ai_factory_portfolio_verify import verify_portfolio
    from tools.ai_factory_sleeve_process import verify_comparison
    for directory in base.glob('*/process-sleeves-v1'):
        if (directory/'comparison-plan.json').exists():verify_comparison(directory)
    for directory in list(base.glob('*/portfolio'))+list(base.glob('*/portfolio-sleeves-v1'))+list(base.glob('*/process-v2/portfolios/*'))+list(base.glob('*/process-v3/portfolios/*')):
        verify_portfolio(directory)
    from services.research.trial_ledger import TrialLedger
    for path in list(base.glob('*/process-v2/comparison-trials.db'))+list(base.glob('*/process-v3/comparison-trials.db')):
        with TrialLedger(str(path),create=False) as ledger:ledger.verify()
    from services.research.ai_factory_episodes import ResearchEpisodes
    for path in base.glob('*/research-episodes.db'):
        with ResearchEpisodes(path,read_only=True) as episodes:episodes.verify()
    from tools.ai_factory_research_records import verify_records
    for session in sessions:verify_records(base/str(session['attempt']),receipts[session['attempt']])
    from tools.ai_factory_collaboration import verify_collaboration
    for path in base.glob('**/research-roles/*/plan.json'):verify_collaboration(path.parent)
    for directory in base.glob('*/process-v3/research/*'):
        fold,group=directory.name.split('-',1);cases=[]
        result_path=directory.parents[1]/'process-comparison.json'
        if result_path.exists():
            report=verified(result_path)
            cases=[t for r in report['rows'] if str(r['fold'])==fold and r['group']==group for t in r['trials']]
        else:
            # Incomplete comparisons still retain evidence, matched by episode trial identity below.
            cases=[verified(p) for p in (directory.parents[1]/'comparison-cases').glob('*.json')]
        verify_records(directory,cases)
    for session in sessions:
        if session['state']=='COMPLETED':
            dest=base/str(session['attempt']);model=verified(dest/'model.json');result=verified(dest/'result.json')
            if result['model_hash']!=model['result_hash'] or session['result']['model_hash']!=model['result_hash']:
                raise ValueError('checkpoint model/result identity conflict')
    if (base/'trials.db').exists():
        from services.research.trial_ledger import TrialLedger
        with TrialLedger(str(base/'trials.db'),create=False) as ledger:
            ledger.verify()
            for session in sessions:
                for receipt in receipts[session['attempt']]:
                    if ledger.load(receipt['trial_id']).status!=receipt['status']:raise ValueError('checkpoint trial status conflict')


def restore_checkpoint(archive,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('restore requires new isolated directory')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.quantos-restore-',dir=destination.parent) as tmp:
        stage=Path(tmp)
        with zipfile.ZipFile(archive) as z:
            names=z.namelist()
            if len(names)!=len(set(names)) or len(names)>100001:raise ValueError('invalid checkpoint inventory')
            if any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts or '\\' in n for n in names):raise ValueError('unsafe checkpoint member')
            if z.getinfo('checkpoint-manifest.json').file_size>32*1024*1024:raise ValueError('checkpoint manifest too large')
            manifest=json.loads(z.read('checkpoint-manifest.json'));entries=manifest['entries']
            schema=manifest.get('schema')
            if type(schema) is not int or schema not in (1,2):raise ValueError('unsupported checkpoint schema')
            layout='CACHE_ROOT_V2' if schema==2 else 'FACTORY_ROOT_V1'
            if schema==2 and any(PurePosixPath(n).parts[0] not in ('ai_factory','profitability','profitability_inputs') for n in entries):
                raise ValueError('unsupported checkpoint scope')
            if set(names)!=set(entries)|{'checkpoint-manifest.json'}:raise ValueError('checkpoint inventory mismatch')
            if sum(z.getinfo(n).file_size for n in names)>20*1024**3:raise ValueError('checkpoint size exceeds bound')
            for name,info in entries.items():
                target=stage/name;target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as src,target.open('wb') as dst:
                    for chunk in iter(lambda:src.read(1024*1024),b''):dst.write(chunk)
                if target.stat().st_size!=info['size'] or _sha(target)!=info['sha256']:raise ValueError('checkpoint content mismatch')
        _verify_bundle(stage,schema)
        os.rename(stage,destination)
    return {'status':'RESTORED_ISOLATED','layout':layout,'files':len(entries)}
