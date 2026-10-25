#!/usr/bin/env python3
# 固定したソースから試験用の構成ファイルを作ります。この生成処理だけでは実環境に適用しません。
"""Offline runtime artifact generator. No credentials or Kubernetes API calls."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import tempfile

import yaml

from contract import require, Rejected
from integration import application
from release_modes import desired_documents

ROOT = Path(__file__).resolve().parent


def profile_image(profile):
    value = profile.get('image') if isinstance(profile, dict) else None
    if isinstance(value, str):
        return value
    require(isinstance(value, dict) and set(value) == {'repository', 'tag', 'digest', 'pullPolicy'} and
            value['pullPolicy'] == 'IfNotPresent', 'finite-image-profile-required')
    return f"{value['repository']}:{value['tag']}@{value['digest']}"


def profile_catalog_image(profile):
    value = profile.get('image') if isinstance(profile, dict) else None
    if isinstance(value, str):
        return value
    profile_image(profile)
    return f"{value['repository']}@{value['digest']}"


def committed_source(repo, commit):
    """Read regular blobs from an exact commit; ignore the mutable checkout."""
    import re
    require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit), 'exact-manifest-commit-required')
    def git(*args):
        try:
            return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:
            raise Rejected('manifest-commit-source-unavailable') from exc
    require(git('cat-file', '-t', commit).strip() == b'commit', 'manifest-object-must-be-commit')
    prefix = 'learning/account-release/'
    entries = git('ls-tree', '-rz', '--full-tree', commit, '--', prefix+'chart', prefix+'runtime-profile.yaml')
    files = {}
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        metadata, path = entry.split(b'\t', 1)
        mode, kind, oid = metadata.split()
        path = path.decode('utf-8')
        require(mode in (b'100644', b'100755') and kind == b'blob', 'manifest-source-must-be-regular-file')
        require(path.startswith(prefix), 'manifest-source-path-outside-release')
        relative = path[len(prefix):]
        require('\\' not in relative and all(x not in ('', '.', '..') for x in relative.split('/')),
                'unsafe-manifest-source-path')
        files[relative] = git('cat-file', 'blob', oid.decode('ascii'))
    require('runtime-profile.yaml' in files and 'chart/Chart.yaml' in files,
            'committed-profile-and-chart-required')
    return files


def runtime_documents(commit, catalog, image, repo, helm='helm'):
    files = committed_source(repo, commit)
    profile = yaml.safe_load(files['runtime-profile.yaml'])
    docs = application(commit, catalog, image)
    # Legacy PLAT-170 commits retain their exact four-field profile. New release
    # profiles must make all mode decisions explicit in Git, never in overrides.
    if isinstance(profile, dict) and set(profile) != set(docs[1]['spec']['source']['helm']['valuesObject']):
        required = {'strategy','promotionMode','analysisPluginEnabled'}
        require(required <= set(profile),'explicit-release-mode-required')
        docs = desired_documents(commit,catalog,image,profile['strategy'],profile['promotionMode'],
                                 profile.get('bootstrapHash',''),profile['analysisPluginEnabled'])
        if 'bootstrapHash' in profile:
            docs[1]['spec']['source']['helm']['valuesObject']['bootstrapHash'] = profile['bootstrapHash']
    expected = docs[1]['spec']['source']['helm']['valuesObject']
    comparable = copy.deepcopy(profile)
    if isinstance(comparable.get('image'), dict):
        require(isinstance(comparable.get('release'), dict), 'typed-release-profile-required')
        comparable['image'] = profile_catalog_image(comparable)
        comparable.pop('release')
    require(comparable == expected, 'tracked-runtime-profile-must-match-reviewed-image')
    docs[1]['spec']['source']['helm'] = dict(releaseName='account-release', valueFiles=['../runtime-profile.yaml'])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for relative, content in files.items():
            target = root/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        rendered = subprocess.check_output([helm, 'template', 'account-release', str(root/'chart'), '-n',
            'learning-account-release', '-f', str(root/'runtime-profile.yaml')], text=True)
    rollout = next(d for d in yaml.safe_load_all(rendered) if d['kind'] == 'Rollout')
    require(profile_catalog_image(profile) == image and
            rollout['spec']['template']['spec']['containers'][0]['image'] == profile_image(profile),
            'committed-chart-image-must-match-reviewed-image')
    env = {item['name']: item for item in rollout['spec']['template']['spec']['containers'][0]['env']}
    for name, field in [('POD_NAMESPACE', 'metadata.namespace'), ('POD_UID', 'metadata.uid')]:
        require('value' not in env.get(name, {}) and
                env.get(name, {}).get('valueFrom', {}).get('fieldRef', {}).get('fieldPath') == field,
                'committed-chart-must-use-actual-pod-identity')
    template = copy.deepcopy(rollout['spec']['template'])
    template['metadata']['annotations']['sidecar.istio.io/inject'] = 'false'
    template['metadata']['labels'] = {'app': 'account-release-migration', 'learning.local/owner': 'plat170-account'}
    template['spec']['restartPolicy'] = 'Never'
    api = template['spec']['containers'][0]
    api['args'] = ['migrate']
    for probe in ('startupProbe', 'readinessProbe', 'livenessProbe'):
        api.pop(probe)
    job = dict(apiVersion='batch/v1', kind='Job', metadata=dict(name='account-release-migration',
        namespace='learning-account-release', labels={'learning.local/owner': 'plat170-account'}),
        spec=dict(backoffLimit=0, activeDeadlineSeconds=180, template=template))
    return docs, job


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest-commit', required=True)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--image', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--helm', default='helm')
    a = p.parse_args()
    catalog = json.loads(a.catalog.read_text())
    docs, job = runtime_documents(a.manifest_commit, catalog, a.image, ROOT.parents[1], a.helm)
    a.output.mkdir(parents=True, exist_ok=False)
    (a.output/'application.yaml').write_text(yaml.safe_dump_all(docs, sort_keys=False))
    (a.output/'migration.yaml').write_text(yaml.safe_dump(job, sort_keys=False))
    print('Git profile Application and migration rendered; no apply or Secret creation')


if __name__ == '__main__':
    main()
