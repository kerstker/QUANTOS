from copy import deepcopy

import pytest

from scripts.generate_console_dependency_lock import resolve_profile, LockError


def package(name, version='1.0', requirements=(), filename=None):
    filename = filename or f'{name.replace("-", "_")}-{version}-py3-none-any.whl'
    return {'info': {'name': name, 'version': version, 'requires_dist': list(requirements),
                     'requires_python': '>=3.10'},
            'urls': [{'filename': filename, 'packagetype': 'bdist_wheel', 'yanked': False,
                      'url': f'https://files.pythonhosted.org/packages/{filename}',
                      'digests': {'sha256': 'a' * 64}}]}


def resolve(roots, pins, packages):
    return resolve_profile(roots, pins, lambda name, _version: deepcopy(packages[name]))


def test_runtime_closure_is_pinned_and_does_not_include_development_tools():
    packages = {'demo': package('demo', requirements=['child>=1', 'windows; sys_platform == "win32"']),
                'child': package('child')}
    report = resolve({'demo': '1.0'}, {'child': '1.0', 'pytest': '9.1.1'}, packages)
    assert {p['metadata']['name'] for p in report['install']} == {'demo', 'child'}


def test_extras_are_propagated_without_enabling_unrequested_extras():
    packages = {'demo': package('demo', requirements=['child[email]>=1']),
                'child': package('child', requirements=['email; extra == "email"', 'other; extra == "other"']),
                'email': package('email')}
    report = resolve({'demo': '1.0'}, {'child': '1.0', 'email': '1.0'}, packages)
    assert {p['metadata']['name'] for p in report['install']} == {'demo', 'child', 'email'}


def test_older_musl_tag_is_compatible_but_manylinux_is_not():
    p = package('demo', filename='demo-1.0-cp312-cp312-manylinux_2_28_aarch64.whl')
    p['urls'] += package('demo', filename='demo-1.0-cp312-cp312-musllinux_1_1_aarch64.whl')['urls']
    report = resolve({'demo': '1.0'}, {}, {'demo': p})
    assert 'musllinux_1_1' in report['install'][0]['download_info']['url']
    p['urls'] = p['urls'][:1]
    with pytest.raises(LockError, match='compatible artifact'):
        resolve({'demo': '1.0'}, {}, {'demo': p})


@pytest.mark.parametrize('requirement', ['child>=2', 'unknown>=1', 'child; platform_release == "host"'])
def test_missing_conflicting_or_host_specific_dependencies_fail_closed(requirement):
    with pytest.raises(LockError):
        resolve({'demo': '1.0'}, {'child': '1.0'}, {'demo': package('demo', requirements=[requirement])})


def test_only_reviewed_scikit_source_distribution_may_be_built():
    p = package('scikit-learn', filename='scikit_learn-1.0.tar.gz')
    p['urls'][0]['packagetype'] = 'sdist'
    result = resolve({'scikit-learn': '1.0'}, {}, {'scikit-learn': p})
    assert result['install'][0]['download_info']['url'].endswith('.tar.gz')
    p['urls'][0]['yanked'] = True
    with pytest.raises(LockError):
        resolve({'scikit-learn': '1.0'}, {}, {'scikit-learn': p})


def test_registry_identity_and_python_compatibility_are_checked():
    p = package('demo')
    p['info']['name'] = 'other'
    with pytest.raises(LockError): resolve({'demo': '1.0'}, {}, {'demo': p})
    p = package('demo'); p['info']['requires_python'] = '>=3.13'
    with pytest.raises(LockError): resolve({'demo': '1.0'}, {}, {'demo': p})
