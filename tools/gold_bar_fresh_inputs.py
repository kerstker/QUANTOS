"""One protected first-close episode; existing attempts are never recaptured."""
import asyncio
import hashlib
from pathlib import Path
import re

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.demo_resident_recovery import _check_path
from tools.gold_bar_definition import PreparedBarDefinition
from tools.gold_bar_demo_entry import restore_engine
from tools.gold_bar_forward import run as observe
from tools.gold_bar_worker_inputs import OBSERVATION_FILES, JOURNAL_FILES, TRIAL_FILES, protected_tree
from tools.gold_cost_review import _read
from tools import gold_bar_pilot_capacity as pilot_capacity


def snapshot(directory, uid):
    rows = []
    for area, names in ((directory, OBSERVATION_FILES), (directory/'journal', JOURNAL_FILES)):
        _check_path(area, uid, directory=True)
        for name in names:
            _check_path(area/name, uid, directory=False)
            rows.append((str((area/name).relative_to(directory)), _read(area, name)))
    return tuple(rows)


class FreshEpisode:
    def __init__(self, root, bundle, *, source_commit, uid, cash_account=None, read_only=False):
        require(type(read_only) is bool and (not read_only or cash_account is None), 'BAR_FRESH_READ_MODE')
        automatic = bundle.get('schema') == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
        history_key = 'episode_id' if automatic else 'history_sha256'
        _fields(bundle, 'schema source_commit trial_manifest_sha256 archive_source_commit candidate seconds '+history_key)
        require(bundle['schema'] in ('GOLD_BAR_FRESH_EPISODE_INPUT_V1', 'GOLD_BAR_AUTO_EPISODE_INPUT_V1')
            and bundle['source_commit'] == source_commit, 'BAR_FRESH_SOURCE')
        require(bundle['candidate'] in ('channel', 'trend', 'reversion')
            and type(bundle['seconds']) is int and 1 <= bundle['seconds'] <= 900, 'BAR_FRESH_PLAN')
        for key, length in (('trial_manifest_sha256', 64), (history_key, 64), ('archive_source_commit', 40)):
            require(type(bundle[key]) is str and re.fullmatch('[a-f0-9]{'+str(length)+'}', bundle[key]) is not None,
                'BAR_FRESH_INPUT_ID')
        root = Path(root).absolute()
        require(root.resolve() == root, 'BAR_FRESH_ROOT')
        original = pilot_capacity.context(root, bundle, uid=uid, account=cash_account)
        self.uid = uid
        self.trial = root/'strategy-bar-trials'/bundle['trial_manifest_sha256']
        self.source = root/'strategy-bar-sources'/bundle['archive_source_commit']
        for path in (root, self.trial.parent, self.trial, self.source.parent):
            _check_path(path, uid, directory=True)
        for area, names in ((self.trial, TRIAL_FILES),):
            for name in names:
                _check_path(area/name, uid, directory=False)
        manifest = _read(self.trial, 'manifest.json')
        require(hashlib.sha256(manifest).hexdigest() == bundle['trial_manifest_sha256']
            and decode(manifest)['source']['commit'] == bundle['archive_source_commit'], 'BAR_FRESH_BINDING')
        self.source_facts = protected_tree(self.source, uid)
        self.prepared = PreparedBarDefinition(self.trial, candidate=bundle['candidate'], source_directory=self.source)
        definition = self.prepared.definition(self.trial, candidate=bundle['candidate'], source_directory=self.source)
        require(definition['archive_files']['manifest.json'] == bundle['trial_manifest_sha256']
            and definition['source']['commit'] == bundle['archive_source_commit'], 'BAR_FRESH_DEFINITION_BINDING')
        if automatic:
            from tools.gold_bar_auto_history import load
            history = load(root, bundle, uid=uid, **({'read_only': True} if read_only else {}))
        else:
            history = root/'strategy-bar-history'/bundle['history_sha256']
        for path in (history.parent, history):
            _check_path(path, uid, directory=True)
        for name in ('receipt.json', 'response.json'):
            _check_path(history/name, uid, directory=False)
        receipt = _read(history, 'receipt.json')
        require(automatic or hashlib.sha256(receipt).hexdigest() == bundle['history_sha256'], 'BAR_FRESH_HISTORY_BINDING')
        self.candidate = bundle['candidate']
        capacity = None
        if original is not None:
            area, capacity = pilot_capacity.storage(root, 'observations', original, bundle, uid=uid, read_only=read_only)
        else:
            area = root/'strategy-bar-observation-work'
            if not read_only:
                area.mkdir(mode=0o700, exist_ok=True)
        _check_path(area, uid, directory=True)
        if capacity is not None:
            entries = pilot_capacity.usage(area, capacity, 'observations', uid)
        else:
            entries = list(area.iterdir())
            require(len(entries) <= 32, 'BAR_FRESH_STORAGE_BUDGET')
            size = 0
            for item in entries:
                facts = protected_tree(item, uid)
                size += sum(row[2] for row in facts)
            require(size <= 256*1024*1024, 'BAR_FRESH_STORAGE_BUDGET')
        self.directory = area/_digest(bundle)
        self.cash_account = cash_account
        self.account_opening = None
        self.observation_area = area
        if cash_account is not None:
            from tools.gold_bar_cash import open_window, open_account_origin
            self.account_opening = open_account_origin(root, bundle=bundle, account=cash_account,
                source_commit=source_commit, observation_exists=self.directory.exists() or self.directory.is_symlink(),
                observation_area=area, capacity=capacity)
            open_window(root, bundle=bundle, account=cash_account, source_commit=source_commit,
                observation_exists=self.directory.exists() or self.directory.is_symlink())
        if not self.directory.exists() and not self.directory.is_symlink():
            require(not read_only, 'BAR_FRESH_NOT_RETAINED')
            if capacity is not None:
                pilot_capacity.allocate_capture(area, capacity, 'observations', uid, bundle)
            else:
                require(len(entries) < 32, 'BAR_FRESH_STORAGE_BUDGET')
            asyncio.run(observe(self.trial, candidate=self.candidate, source_directory=self.source,
                history=history, output=self.directory, seconds=bundle['seconds'], first_close=True,
                prepared_definition=self.prepared, protected_network=True))
        if capacity is not None:
            pilot_capacity.usage(area, capacity, 'observations', uid)
            require(pilot_capacity.context(root, bundle, uid=uid, account=cash_account) == original,
                'BAR_PILOT_ORIGINAL_CHANGED')
        self.pilot_original = original
        self.pilot_capacity = capacity
        self.pilot_bundle = bundle
        self.pilot_root = root
        # Partial attempts fail here and remain available for diagnosis.
        self.files = snapshot(self.directory, uid)
        registration = decode(_read(self.directory, 'registration.json'))
        require(registration.get('first_close') is True
            and registration.get('network_mode') == 'PROTECTED_DEMO_NETWORK_V1'
            and registration.get('seconds') == bundle['seconds'], 'BAR_FRESH_REGISTRATION')
        require(_read(self.directory/'journal', 'warmup-receipt.json') == receipt
            and _read(self.directory/'journal', 'warmup.json') == _read(history, 'response.json'), 'BAR_FRESH_HISTORY')
        self.engine = restore_engine(self.directory, archive=self.trial, source_directory=self.source,
            prepared_definition=self.prepared)
        rows = [decode(line) for line in _read(self.directory, 'selected-decisions.jsonl').splitlines()]
        require(len(rows) == 1, 'BAR_FRESH_NO_NEW_CLOSE')
        self.entry_decision_ms = rows[0]['received_at_ms']
        definition = self.prepared.definition(self.trial, candidate=self.candidate, source_directory=self.source)
        self.checked = dict(definition_hash=definition['definition_hash'],
            observation_sha256=hashlib.sha256(_read(self.directory, 'result.json')).hexdigest(),
            trial_manifest_sha256=bundle['trial_manifest_sha256'], archive_source_commit=bundle['archive_source_commit'])
        self.recheck()

    def recheck(self):
        if self.account_opening is not None:
            from tools.gold_bar_cash import load_account_opening, account_opening_usage
            require(load_account_opening(self.pilot_root, bundle=self.pilot_bundle, account=self.cash_account,
                source_commit=self.pilot_bundle['source_commit']) == self.account_opening, 'BAR_FRESH_OPENING_CHANGED')
            require(self.account_opening['origin']['completed_at_ms']+5000 < self.entry_decision_ms,
                'BAR_FRESH_OPENING_BEFORE_SIGNAL')
            account_opening_usage(self.pilot_root, observation_area=self.observation_area, capacity=self.pilot_capacity,
                bundle=self.pilot_bundle, account=self.cash_account)
        if self.pilot_original is not None:
            require(pilot_capacity.context(self.pilot_root, self.pilot_bundle, uid=self.uid) == self.pilot_original,
                'BAR_PILOT_ORIGINAL_CHANGED')
            _, current_capacity = pilot_capacity.storage(self.pilot_root, 'observations', self.pilot_original,
                self.pilot_bundle, uid=self.uid, read_only=True)
            require(current_capacity == self.pilot_capacity, 'BAR_PILOT_CAPACITY_CHANGED')
        definition = self.prepared.definition(self.trial, candidate=self.candidate, source_directory=self.source)
        require(snapshot(self.directory, self.uid) == self.files
            and protected_tree(self.source, self.uid) == self.source_facts
            and definition['definition_hash'] == self.checked['definition_hash'], 'BAR_FRESH_CHANGED')
        return dict(self.checked)
