"""Lossless append-only observations with bounded file growth and honest recovery."""
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,sqlite3,zlib
import pytest
from services.research import demo_observation_store as store

NOW=1800000000000
def report(number=0):
    return dict(observed_at_ms=NOW+number,status='FAILED' if number%3==0 else 'CAPTURED',
                response='a'*8000,attempt=number,submission_enabled=False)
def validate(raw):
    assert raw['submission_enabled'] is False
    return raw

def test_many_observations_preserve_exact_failed_and_successful_history_with_bounded_files(tmp_path):
    records=[report(n) for n in range(240)]
    for raw in records:store.save(tmp_path,raw,validator=validate)
    files=list(tmp_path.rglob('*'))
    assert len([p for p in files if p.is_file()])<=3
    assert not list(tmp_path.glob('[a-f0-9]'*64+'.json'))
    assert store.load_latest(tmp_path,validator=validate)==records[-1]
    segments=list((tmp_path/'archive').glob('*.sqlite3'));assert len(segments)==1
    checked=store.verify_segment(segments[0],validator=validate)
    assert checked['records']==len(records)
    with sqlite3.connect(segments[0]) as db:
        rows=db.execute('SELECT sha256,raw_size,payload_zlib FROM observations ORDER BY sequence').fetchall()
    for expected,(digest,size,packed) in zip(records,rows):
        body=zlib.decompress(packed)
        assert json.loads(body)==expected and len(body)==size and hashlib.sha256(body).hexdigest()==digest
    assert sum(len(packed) for _,_,packed in rows)<sum(size for _,size,_ in rows)/4

def test_committed_record_survives_pointer_failure_and_same_attempt_recovers_without_duplicate(tmp_path,monkeypatch):
    raw=report();original=store._publish_pointer
    monkeypatch.setattr(store,'_publish_pointer',lambda *a:(_ for _ in ()).throw(OSError('interrupted pointer')))
    with pytest.raises(OSError,match='interrupted pointer'):store.save(tmp_path,raw,validator=validate)
    path=next((tmp_path/'archive').glob('*.sqlite3'))
    assert store.verify_segment(path,validator=validate)['records']==1
    assert not (tmp_path/'latest.json').exists()
    monkeypatch.setattr(store,'_publish_pointer',original)
    store.save(tmp_path,raw,validator=validate)
    assert store.load_latest(tmp_path,validator=validate)==raw
    assert store.verify_segment(path,validator=validate)['records']==1

def test_legacy_history_remains_readable_and_day_boundary_adds_only_one_segment(tmp_path):
    from services.research import demo_display_store
    old=report();old_path=demo_display_store.save(tmp_path,old,validator=validate);old_bytes=old_path.read_bytes()
    assert store.load_latest(tmp_path,validator=validate)==old
    current=report(1);store.save(tmp_path,current,validator=validate)
    tomorrow=report(86400001);store.save(tmp_path,tomorrow,validator=validate)
    assert len(list((tmp_path/'archive').glob('*.sqlite3')))==2
    assert old_path.read_bytes()==old_bytes
    assert store.load_latest(tmp_path,validator=validate)==tomorrow

def test_archive_reader_never_creates_missing_database_or_writes_existing_files(tmp_path):
    store.save(tmp_path,report(),validator=validate)
    before={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert store.load_latest(tmp_path,validator=validate)==report()
    assert before=={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    path=next((tmp_path/'archive').glob('*.sqlite3'));path.rename(path.with_suffix('.saved'))
    with pytest.raises((ValueError,OSError,sqlite3.Error)):store.load_latest(tmp_path,validator=validate)
    assert not path.exists()

@pytest.mark.parametrize('kind',['path_escape','wrong_segment','digest','symlink','hardlink','payload','schema'])
def test_bad_pointer_or_stored_content_is_rejected_without_fallback(tmp_path,kind):
    store.save(tmp_path,report(),validator=validate)
    pointer=json.loads((tmp_path/'latest.json').read_text());path=tmp_path/'archive'/pointer['segment']
    if kind=='path_escape':pointer['segment']='../../other.sqlite3'
    elif kind=='wrong_segment':pointer['segment']='day-99999999.sqlite3'
    elif kind=='digest':pointer['sha256']='f'*64
    elif kind=='symlink':
        saved=path.with_suffix('.saved');path.rename(saved);path.symlink_to(saved)
    elif kind=='hardlink':os.link(path,path.with_suffix('.saved'))
    else:
        with sqlite3.connect(path) as db:
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():db.execute('DROP TRIGGER '+name)
            if kind=='payload':db.execute("UPDATE observations SET payload_zlib=?",(zlib.compress(b'{"submission_enabled":true}'),))
            else:db.execute("UPDATE metadata SET schema='OTHER'")
    (tmp_path/'latest.json').write_text(json.dumps(pointer))
    with pytest.raises((ValueError,OSError,sqlite3.Error)):store.load_latest(tmp_path,validator=validate)

def test_segment_capacity_keeps_history_and_last_pointer_without_widening_budget(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'MAX_RECORDS',2)
    store.save(tmp_path,report(),validator=validate);store.save(tmp_path,report(1),validator=validate)
    pointer=(tmp_path/'latest.json').read_bytes()
    with pytest.raises(ValueError,match='CAPACITY'):store.save(tmp_path,report(2),validator=validate)
    assert (tmp_path/'latest.json').read_bytes()==pointer
    # Duplicate delivery of the last complete attempt remains idempotent at the bound.
    store.save(tmp_path,report(1),validator=validate)
    assert store.verify_segment(next((tmp_path/'archive').glob('*.sqlite3')),validator=validate)['records']==2

def test_existing_record_cannot_be_updated_or_deleted_by_normal_sql(tmp_path):
    store.save(tmp_path,report(),validator=validate)
    with sqlite3.connect(next((tmp_path/'archive').glob('*.sqlite3'))) as db:
        for sql in ('DELETE FROM observations','UPDATE observations SET raw_size=0'):
            with pytest.raises(sqlite3.IntegrityError):db.execute(sql)


def test_delayed_retry_of_an_older_attempt_cannot_roll_back_the_published_head(tmp_path):
    store.save(tmp_path,report(),validator=validate)
    store.save(tmp_path,report(1),validator=validate)
    pointer=(tmp_path/'latest.json').read_bytes()
    store.save(tmp_path,report(),validator=validate)
    assert store.load_latest(tmp_path,validator=validate)==report(1)
    assert (tmp_path/'latest.json').read_bytes()==pointer


def test_concurrent_out_of_order_attempts_keep_every_record_and_the_newest_head(tmp_path):
    values=[report(n) for n in range(59,-1,-1)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda raw:store.save(tmp_path,raw,validator=validate),values))
    assert len(results)==60 and store.load_latest(tmp_path,validator=validate)==report(59)
    assert store.verify_segment(results[0],validator=validate)['records']==60


@pytest.mark.parametrize('kind',['oversized','trailing','digest'])
def test_compressed_record_limits_are_checked_even_with_an_intact_database_schema(tmp_path,kind):
    store.save(tmp_path,report(),validator=validate)
    path=next((tmp_path/'archive').glob('*.sqlite3'))
    with sqlite3.connect(path) as db:
        packed=db.execute('SELECT payload_zlib FROM observations').fetchone()[0]
        if kind=='oversized':packed=zlib.compress(b'x'*(store.MAX_RAW_BYTES+10000))
        elif kind=='trailing':packed+=zlib.compress(b'extra')
        else:packed=zlib.compress(b'{}')
        db.execute('DROP TRIGGER observations_no_update')
        db.execute('UPDATE observations SET payload_zlib=?',(packed,))
        db.execute(store.TRIGGERS['observations_no_update'])
    with pytest.raises(ValueError,match='RECORD_DIGEST'):store.load_latest(tmp_path,validator=validate)


def test_rollover_capacity_and_disk_reserve_fail_without_erasing_the_previous_head(tmp_path,monkeypatch):
    store.save(tmp_path,report(),validator=validate)
    pointer=(tmp_path/'latest.json').read_bytes()
    monkeypatch.setattr(store,'MAX_SEGMENTS',1)
    with pytest.raises(ValueError,match='SEGMENT_CAPACITY'):store.save(tmp_path,report(86400000),validator=validate)
    from types import SimpleNamespace
    monkeypatch.setattr(store.shutil,'disk_usage',lambda path:SimpleNamespace(free=0))
    with pytest.raises(ValueError,match='DISK_RESERVE'):store.save(tmp_path,report(1),validator=validate)
    assert (tmp_path/'latest.json').read_bytes()==pointer


def test_legacy_reading_does_not_require_posix_uid_support(tmp_path,monkeypatch):
    from services.research import demo_display_store
    demo_display_store.save(tmp_path,report(),validator=validate)
    monkeypatch.delattr(store.os,'getuid')
    assert store.load_latest(tmp_path,validator=validate)==report()
