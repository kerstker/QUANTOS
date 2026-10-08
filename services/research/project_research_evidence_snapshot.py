"""Fixed complete-project/source/trust reads for independent research review.

Construct only at the controlled application boundary. Material files select no
filesystem paths or trust keys. This reader never installs trust or opens an
original SQLite store for writing.
"""
import copy
import re
from contextlib import contextmanager
from pathlib import Path

from quantos.contracts.research_evidence_json import canonical, digest, _decode
from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader, _ProjectReads
from services.research.research_evidence_reader import _path, _source
from services.validation import project_research_evidence_authority as authority


class ProjectResearchEvidenceSnapshotReader:
    def __init__(self, material_directory, *, project_root, registry_database,
                 source_directory, source_commit, producer_id, trust_registry,
                 expected_registry_sha256, minimum_generation):
        self.material = _path(material_directory)
        self.project = ProjectResearchEvidenceReader(project_root, registry_database=registry_database)
        self.source = _path(source_directory)
        self.trust = _path(trust_registry)
        authority.require(not self.trust.is_relative_to(self.material)
            and not self.material.is_relative_to(self.trust.parent)
            and not self.trust.is_relative_to(self.project.project), 'TRUST_LOCATION')
        authority.require(type(source_commit) is str and re.fullmatch('[0-9a-f]{40}', source_commit), 'SOURCE_COMMIT')
        authority.shape._sha(expected_registry_sha256)
        authority.shape._text(producer_id)
        authority.shape._integer(minimum_generation, minimum=1)
        self.source_commit = source_commit
        self.producer_id = producer_id
        self.expected_registry_sha256 = expected_registry_sha256
        self.minimum_generation = minimum_generation

    def __call__(self):
        return self._read()['snapshot']

    def read_retained(self, retained_cut, *, read_review_rows, registry_view_for=None):
        """Read current originals against a separately retained signed cut.

        The database reader supplies original review rows with their audit and
        no-activity-link checks. Neither a payload path nor a historical trust
        registry can select the current source or authority files.
        """
        return self._read(retained_cut=retained_cut, read_review_rows=read_review_rows,
                          registry_view_for=registry_view_for)

    def _read(self, *, retained_cut=None, read_review_rows=None, registry_view_for=None):
        with self._evidence(retained_cut=retained_cut, read_review_rows=read_review_rows,
                registry_view_for=registry_view_for) as (observed, _):
            return observed

    @contextmanager
    def evidence_retained(self, retained_cut, *, read_review_rows, registry_view_for=None):
        """Original snapshots and all report views held through consumption."""
        with self._evidence(retained_cut=retained_cut, read_review_rows=read_review_rows,
                registry_view_for=registry_view_for) as value:
            yield value

    @contextmanager
    def _evidence(self, *, retained_cut=None, read_review_rows=None, registry_view_for=None):
        reads = _ProjectReads()
        with self.project._evidence(reads, registry_view_for=registry_view_for) as (cut, reports):
            observed = self._assemble(reads, cut, reports, retained_cut=retained_cut,
                read_review_rows=read_review_rows, registry_view_for=registry_view_for)
            yield observed, reports

    def _assemble(self, reads, cut, reports, *, retained_cut, read_review_rows, registry_view_for):
        expected = {'subject.json', 'source_manifest.json', 'source_envelope.json', 'result_envelope.json'}
        members = set(reads.directory(self.material))
        statistical = 'statistical_evidence.json' in members
        if statistical:
            expected.add('statistical_evidence.json')
        authority.require(members == expected, 'PROJECT_MATERIAL_MEMBERS')
        raw = {name: reads.read(self.material/name,
            16_000_000 if name in ('source_manifest.json', 'statistical_evidence.json') else 65_536)
               for name in sorted(expected)}
        docs = {name[:-5]: _decode(value) for name, value in raw.items()}
        subject = docs['subject']
        reviewed = cut
        if retained_cut is not None:
            from services.validation import project_research_evidence_artifact as artifact
            ids = artifact.added_review_ids(retained_cut, cut)
            artifact.verify_current_cut(retained_cut, cut, review_rows=read_review_rows(ids), reports=reports)
            reviewed = copy.deepcopy(retained_cut)
        authority._subject(subject, reviewed)
        authority.require(subject['producer_id'] == self.producer_id, 'PRODUCER')
        identity = cut['runs'][subject['invocation_id']]['plan']['source_identity']
        _source(reads, self.source, dict(source_identity=canonical(identity),
            source_manifest=raw['source_manifest.json']), self.source_commit)
        reads.directory(self.trust.parent)
        trust_raw = reads.read(self.trust, 65_536)
        authority.require(digest(trust_raw) == self.expected_registry_sha256, 'TRUST_PIN')
        registry = _decode(trust_raw)
        authority.shape._fields(registry, ('schema', 'generation', 'producer_ids', 'keys'), 'TRUST_FIELDS')
        authority.require(registry['schema'] == 'QUANTOS_RESEARCH_EVIDENCE_TRUST_V1'
            and type(registry['generation']) is int and registry['generation'] >= self.minimum_generation,
            'TRUST_GENERATION')
        selected_run = cut['runs'][subject['invocation_id']]
        from quantos.contracts.registered_research_run import RISK_EVALUATED_SCHEMA
        if selected_run['plan']['schema'] == RISK_EVALUATED_SCHEMA:
            from services.execution import research_risk_replay as replay
            from services.execution import authoritative_perpetual_backtest as native
            # Validate the code actually executing this fixed adapter. A signed
            # source tree at a different release path cannot select new code.
            authority.require(all(Path(module.__file__).resolve().parents[2] == self.source
                for module in (replay, native)), 'RISK_EXECUTING_SOURCE')
            if 'archive/risk-replay.json' in selected_run['originals']:
                retained = authority._original(selected_run, 'archive/risk-replay.json')
                recomputed = replay.replay_technical_risk(
                    document=authority._original(selected_run, 'input.json'),
                    original_result=authority._original(selected_run, 'archive/report.json'))
                authority.require(canonical(recomputed) == canonical(retained['replay']), 'RISK_REPLAY_CHANGED')
        if statistical:
            from services.validation import project_research_statistics as statistics
            from services.validation import project_research_statistical_evidence as statistical_evidence
            from services.validation import research_result_projection, catalog_evidence
            from services.validation import cpcv, deflated_sharpe, performance
            from quantos.contracts import research_statistics, research_statistical_review, decimal_arithmetic
            authority.require(all(Path(module.__file__).resolve().parents[2] == self.source for module in (
                statistics, statistical_evidence, research_result_projection, catalog_evidence, cpcv,
                deflated_sharpe, performance, research_statistics, research_statistical_review, decimal_arithmetic)),
                'STATISTICAL_EXECUTING_SOURCE')
        # This final recheck covers the original project DB/WAL, every run,
        # complete source tree and independent trust files in one read set.
        if registry_view_for is not None:
            authority.require(canonical(registry_view_for(self.project.registry)) == canonical(
                dict(population=cut['registry_population'], history=cut['registry_history'])), 'REGISTRY_LEASE_CHANGED')
        identity_hash = reads.recheck()
        cut['read_identity_sha256'] = identity_hash
        reviewed['read_identity_sha256'] = identity_hash
        snapshot = dict(schema=authority.STATISTICAL_SNAPSHOT_SCHEMA if statistical else authority.SNAPSHOT_SCHEMA,
            subject=subject, project_cut=reviewed,
            source_manifest=docs['source_manifest'], source_envelope=docs['source_envelope'],
            result_envelope=docs['result_envelope'], trust_registry=registry,
            expected_registry_sha256=digest(registry), read_identity_sha256=identity_hash)
        if statistical:
            snapshot['statistical_evidence'] = docs['statistical_evidence']
        return dict(snapshot=snapshot, current_cut=cut)
