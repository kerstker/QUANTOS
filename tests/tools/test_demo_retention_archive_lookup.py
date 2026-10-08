"""Historical reads across explicit archive batches never silently pick a winner."""
import pytest
from tools import demo_retention_transaction as retention
from tests.tools.test_demo_retention_transaction import fixture


def batches(tmp_path):
    root,names=fixture(tmp_path)
    archives=[tmp_path/'first',tmp_path/'second']
    original={name:(root/name).read_bytes() for name in names}
    for archive,name in zip(archives,names):
        retention.prepare(root,archive,[name]);retention.apply(root,archive)
    return root,names,archives,original


def test_each_batch_and_current_record_are_read_exactly(tmp_path):
    root,names,archives,original=batches(tmp_path)
    for name in names:
        assert retention.lookup_many(root,list(reversed(archives)),name)==original[name]
    retention.rollback(root,archives[0])
    assert retention.lookup_many(root,archives,names[0])==original[names[0]]


def test_current_only_without_archives(tmp_path):
    root,names=fixture(tmp_path)
    assert retention.lookup_many(root,[],names[0])==(root/names[0]).read_bytes()


def test_bad_other_manifest_cannot_be_ignored(tmp_path):
    root,names,archives,_=batches(tmp_path)
    (archives[1]/'manifest.sha256').write_text('0'*64)
    with pytest.raises(ValueError,match='MANIFEST_DIGEST'):
        retention.lookup_many(root,archives,names[0])


def test_duplicate_archive_input_is_rejected(tmp_path):
    root,names,archives,_=batches(tmp_path)
    with pytest.raises(ValueError,match='ARCHIVE_SET'):
        retention.lookup_many(root,[archives[0],archives[0]],names[0])


def test_record_claimed_by_two_batches_is_ambiguous(tmp_path):
    root,names=fixture(tmp_path)
    archives=[tmp_path/'first',tmp_path/'second']
    for archive in archives:retention.prepare(root,archive,[names[0]])
    with pytest.raises(ValueError,match='ARCHIVE_RECORD_AMBIGUOUS'):
        retention.lookup_many(root,archives,names[0])


def test_duplicate_current_and_archived_record_is_not_hidden(tmp_path):
    root,names,archives,original=batches(tmp_path)
    (root/names[0]).write_bytes(original[names[0]]);(root/names[0]).chmod(0o600)
    with pytest.raises(ValueError,match='PLACEMENT_CONFLICT_OR_MISSING'):
        retention.lookup_many(root,archives,names[0])


def test_missing_record_is_not_replaced_by_latest(tmp_path):
    root,names,archives,_=batches(tmp_path)
    with pytest.raises(FileNotFoundError):
        retention.lookup_many(root,archives,'observation-views/'+'f'*64+'.json')


def test_protected_namespace_and_excessive_archive_count_are_rejected(tmp_path):
    root,names=fixture(tmp_path)
    with pytest.raises(ValueError,match='ISOLATED_ONLY'):
        retention.lookup_many('/var/lib/quantos-demo-executor',[],names[0])
    with pytest.raises(ValueError,match='ARCHIVE_SET'):
        retention.lookup_many(root,[tmp_path/str(i) for i in range(33)],names[0])
