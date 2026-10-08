"""Generate hash locks for the reviewed CPython 3.12 Alpine ARM64 console.

Versions come from reviewed roots and the ARM64 CI lock, never an unbounded
latest-version search. Pip still checks the complete closure during the actual
hash-enforced image build; --check alone verifies structure/source bindings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.request import urlopen

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename
from packaging.version import Version

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts.ci_reproducibility import build_lock_document, verify_lock_document  # noqa: E402

BASE = 'public.ecr.aws/docker/library/python@sha256:900229622a576409d52f7a66b24cf441415a828d7e503b0107bf56452a4e44ac'
PLATFORM = 'alpine_musllinux_1_2_aarch64'
PYTHON = '3.12'
ENVIRONMENT = dict(implementation_name='cpython', implementation_version='3.12.13',
    os_name='posix', platform_machine='aarch64', platform_python_implementation='CPython',
    platform_system='Linux', python_full_version='3.12.13', python_version=PYTHON,
    sys_platform='linux', platform_release='', platform_version='')
MUSL_PLATFORMS = ['musllinux_1_2_aarch64', 'musllinux_1_1_aarch64', 'musllinux_1_0_aarch64']
TAGS = list(cpython_tags((3, 12), ['cp312'], MUSL_PLATFORMS)) + list(
    compatible_tags((3, 12), 'cp312', MUSL_PLATFORMS))
TAG_RANK = {tag: index for index, tag in reversed(list(enumerate(TAGS)))}
PROFILES = {
    'build': ('requirements-console-build.txt', 'requirements-console-build.lock'),
    'runtime': ('requirements.txt', 'requirements-console-runtime.lock'),
}
SOURCES = ('deploy/console.Dockerfile', 'requirements.txt', 'requirements-console-build.txt',
           'requirements-ci-linux-aarch64.lock', 'scripts/generate_console_dependency_lock.py')


class LockError(ValueError):
    pass


def _active(marker: Marker | None, extra: str) -> bool:
    if marker is None:
        return True
    if re.search(r'\b(platform_release|platform_version)\b', str(marker)):
        raise LockError('host-specific dependency marker is unsupported')
    return marker.evaluate(dict(ENVIRONMENT, extra=extra))


def _artifact(name, version, metadata):
    candidates = []
    for artifact in metadata['urls']:
        if artifact.get('yanked') is not False:
            continue
        if artifact.get('requires_python') and not SpecifierSet(artifact['requires_python']).contains(ENVIRONMENT['python_full_version']):
            continue
        filename = artifact['filename']
        if artifact['packagetype'] == 'bdist_wheel':
            artifact_name, artifact_version, _, tags = parse_wheel_filename(filename)
            ranks = [TAG_RANK[tag] for tag in tags if tag in TAG_RANK]
            if not ranks:
                continue
            rank = min(ranks)
        elif artifact['packagetype'] == 'sdist' and name == 'scikit-learn' and filename.endswith('.tar.gz'):
            artifact_name, artifact_version = parse_sdist_filename(filename)
            rank = len(TAGS) + 1
        else:
            continue
        if canonicalize_name(artifact_name) != name or artifact_version != Version(version):
            raise LockError('artifact identity mismatch')
        candidates.append((rank, filename, artifact))
    if not candidates:
        raise LockError(f'no reviewed compatible artifact for {name}=={version}')
    artifact = min(candidates, key=lambda row: row[:2])[2]
    return {'metadata': {'name': name, 'version': version},
            'download_info': {'url': artifact['url'], 'archive_info': {
                'hashes': {'sha256': artifact['digests']['sha256']}}}}


def resolve_profile(roots, pins, fetch):
    """Resolve only known versions; fetch returns public PyPI release metadata."""
    versions = dict(pins, **roots)
    pending = [(name, '') for name in roots]
    seen = set()
    metadata = {}
    selected = {}
    while pending:
        name, extra = pending.pop()
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        if len(seen) > 1000:
            raise LockError('dependency context budget exceeded')
        version = versions[name]
        if name not in metadata:
            data = fetch(name, version)
            info = data['info']
            if canonicalize_name(info['name']) != name or Version(info['version']) != Version(version):
                raise LockError('registry identity mismatch')
            if not SpecifierSet(info.get('requires_python') or '').contains(ENVIRONMENT['python_full_version']):
                raise LockError('incompatible Python requirement')
            metadata[name] = data
            selected[name] = _artifact(name, version, data)
        for raw in metadata[name]['info'].get('requires_dist') or []:
            requirement = Requirement(raw)
            if not _active(requirement.marker, extra):
                continue
            child = canonicalize_name(requirement.name)
            if requirement.url or child not in versions:
                raise LockError(f'unpinned dependency: {child}')
            if not requirement.specifier.contains(versions[child]):
                raise LockError(f'conflicting pinned dependency: {child}')
            pending.extend((child, context) for context in {'', *requirement.extras})
    return {'version': '1', 'install': [selected[name] for name in sorted(selected)]}


def _fetch(name, version):
    with urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=30) as response:
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise LockError('registry response exceeds budget')
    return json.loads(raw)


def _roots(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        requirement = Requirement(line)
        versions = list(requirement.specifier)
        if requirement.url or requirement.marker or requirement.extras or len(versions) != 1 or versions[0].operator != '==':
            raise LockError('root must have one exact version')
        name = canonicalize_name(requirement.name)
        if name in result:
            raise LockError('duplicate root requirement')
        result[name] = versions[0].version
    return result


def _versions(document):
    return dict(line.removeprefix('# package: ').split('==', 1)
                for line in document.splitlines() if line.startswith('# package: '))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--verify-resolution', action='store_true')
    args = parser.parse_args(argv)
    if args.verify_resolution and not args.check:
        parser.error('--verify-resolution requires --check')
    try:
        recipe = (REPO / 'deploy/console.Dockerfile').read_text()
        if f'ARG PYTHON_IMAGE={BASE}\n' not in recipe:
            raise LockError('base image differs from reviewed target')
        sources = {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in SOURCES}
        ci = (REPO / 'requirements-ci-linux-aarch64.lock').read_text()
        verify_lock_document(ci, expected_source_hashes={name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
            for name in ('requirements.txt', 'requirements-dev.txt')}, required_python_version=PYTHON,
            required_platform_tag='ubuntu_24_04_aarch64')
        pins = _versions(ci)
        fetched = {}
        def fetch(name, version):
            if (name, version) not in fetched:
                fetched[name, version] = _fetch(name, version)
            return fetched[name, version]
        documents = {}
        for profile, (root_name, output_name) in PROFILES.items():
            path = REPO / output_name
            roots = _roots(REPO / root_name)
            if profile == 'build' and f'ARG PIP_VERSION={roots["pip"]}\n' not in recipe:
                raise LockError('installer version differs from reviewed recipe')
            if args.check:
                document = path.read_text()
                verify_lock_document(document, expected_source_hashes=sources,
                    required_python_version=PYTHON, required_platform_tag=PLATFORM)
                observed = _versions(document)
                if any(observed.get(name) != version for name, version in roots.items()):
                    raise LockError('lock omits a reviewed root/version')
            if not args.check or args.verify_resolution:
                report = resolve_profile(roots, pins, fetch)
                generated = build_lock_document(report, source_hashes=sources,
                    python_version=PYTHON, platform_tag=PLATFORM).replace(
                    'Generated by scripts/generate_ci_dependency_lock.py;',
                    'Generated by scripts/generate_console_dependency_lock.py;')
                if args.check and document != generated:
                    raise LockError(f'{profile} registry resolution differs; lock not changed')
                documents[path] = generated
            print(f'console dependency profile {profile}: ' + ('fresh resolution matches' if args.verify_resolution else 'structure/source verified; index not checked' if args.check else 'resolved'))
        if not args.check:
            for path, document in documents.items():
                path.write_text(document, encoding='utf-8', newline='\n')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'console dependency lock failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
