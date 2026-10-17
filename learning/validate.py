#!/usr/bin/env python3
"""Offline render/schema fixtures for this learning path only. No cluster calls."""
import copy
import json
import subprocess
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parent
CHART = ROOT / 'chart'
DEFAULTS = yaml.safe_load((CHART / 'values.yaml').read_text())
SCHEMA = json.loads((CHART / 'values.schema.json').read_text())
Draft7Validator.check_schema(SCHEMA)
VALIDATOR = Draft7Validator(SCHEMA)
SERVICES = {'account': 'account', 'order': 'orders', 'payment': 'payments',
            'notification': 'notifications'}


def run(args, ok=True):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if (result.returncode == 0) != ok:
        raise AssertionError(f'{args[:3]} unexpected exit {result.returncode}: {result.stderr}')
    return result.stdout


def fixture(service):
    values = copy.deepcopy(DEFAULTS)
    values.update(service=service, owner='synthetic-fixture', imageConfigID='sha256:' + 'a' * 64)
    values['database']['imageConfigID'] = 'sha256:' + 'b' * 64
    return values


def render(values, directory, namespace='learning-fixture', release=None, ok=True):
    path = directory / 'fixture.yaml'
    path.write_text(yaml.safe_dump(values))
    args = ['helm', 'template', release or 'learning-' + values['service'], str(CHART),
            '--namespace', namespace, '--kube-version', '1.36.4', '-f', str(path)]
    return run(args, ok)


def verify(service, objects):
    name = 'learning-' + service
    by_kind = {}
    for obj in objects:
        by_kind.setdefault(obj['kind'], []).append(obj)
        assert obj['metadata']['namespace'] == 'learning-fixture'
        assert obj['metadata']['labels']['learning.local/owner'] == 'synthetic-fixture'
        assert obj['metadata']['labels']['learning.local/service'] == service
    assert {k: len(v) for k, v in by_kind.items()} == {
        'ConfigMap': 1, 'PersistentVolumeClaim': 1, 'Service': 2,
        'StatefulSet': 1, 'Deployment': 1, 'Job': 1}
    pvc = by_kind['PersistentVolumeClaim'][0]
    assert pvc['metadata']['name'] == name + '-data'
    assert pvc['spec']['storageClassName'] == 'standard'
    assert pvc['spec']['resources']['requests']['storage'] == '1Gi'
    init = by_kind['ConfigMap'][0]['data']['10-app.sh']
    assert service + '_app' in init and SERVICES[service] in init
    assert 'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT' in init
    # Validate actual embedded shell syntax without running it or generating a credential.
    subprocess.run(['sh', '-n'], input=init, text=True, check=True)
    deploy = by_kind['Deployment'][0]
    job = by_kind['Job'][0]
    db = by_kind['StatefulSet'][0]
    expected_image = ('local.learning/' + service + ':' + DEFAULTS['sourceCommits'][service]
                      + '-' + 'a' * 64)
    for obj in (deploy, job, db):
        pod = obj['spec']['template']
        spec = pod['spec']
        assert spec['automountServiceAccountToken'] is False
        container = spec['containers'][0]
        assert container['imagePullPolicy'] == 'Never'
        assert container['securityContext']['readOnlyRootFilesystem'] is True
        assert container['securityContext']['allowPrivilegeEscalation'] is False
        assert not spec.get('hostNetwork') and not spec.get('hostPID')
        assert not any('hostPath' in v for v in spec['volumes'])
        if obj is db:
            assert container['image'] == 'local.learning/postgresql:' + 'b' * 64
            assert spec['volumes'][0]['persistentVolumeClaim']['claimName'] == name + '-data'
            assert pod['metadata']['annotations']['learning.local/upstream-digest'] == DEFAULTS['database']['upstreamDigest']
        else:
            assert container['image'] == expected_image
            env = {x['name']: x for x in container['env']}
            assert env['PGHOST']['value'] == name + '-db'
            assert env['PGDATABASE']['value'] == SERVICES[service]
            assert env['PGUSER']['value'] == service + '_app'
            assert env['PGPASSWORD']['valueFrom']['secretKeyRef'] == {
                'name': name + '-db', 'key': 'app-password'}
            assert 'DATABASE_URL' not in env
    assert job['spec']['backoffLimit'] == 0
    assert job['spec']['activeDeadlineSeconds'] == 180
    assert job['spec']['template']['spec']['containers'][0]['args'] == ['migrate']
    api = deploy['spec']['template']['spec']['containers'][0]
    assert api['startupProbe']['httpGet']['path'] == '/livez'
    assert api['livenessProbe']['httpGet']['path'] == '/livez'
    assert api['readinessProbe']['httpGet']['path'] == '/readyz'
    assert len({s['metadata']['name'] for s in by_kind['Service']}) == 2
    for svc in by_kind['Service']:
        target = db if svc['metadata']['name'].endswith('-db') else deploy
        assert svc['spec']['selector'] == target['spec']['selector']['matchLabels']
        assert all(target['spec']['template']['metadata']['labels'][k] == v
                   for k, v in svc['spec']['selector'].items())
        assert svc['spec'].get('type', 'ClusterIP') == 'ClusterIP'


def main():
    assert list(VALIDATOR.iter_errors(DEFAULTS)), 'unconfigured defaults must fail'
    identities = set()
    with tempfile.TemporaryDirectory(prefix='learning-render-') as tmp:
        directory = Path(tmp)
        for service in SERVICES:
            values = fixture(service)
            VALIDATOR.validate(values)
            rendered = render(values, directory)
            objects = list(yaml.safe_load_all(rendered))
            verify(service, objects)
            for obj in objects:
                identity = obj['kind'], obj['metadata']['name']
                assert identity not in identities, 'cross-service resource collision'
                identities.add(identity)
            run(['helm', 'lint', '--strict', '--namespace', 'learning-fixture',
                 str(CHART), '-f', str(directory / 'fixture.yaml')])
            print(f'PASS {service}: schema, strict lint, 7 resources, ownership/DB/image/probe contracts')
        # Rejection fixtures exercise the Helm entry point as well as JSON Schema.
        bad_cases = []
        for key, value in [('imageConfigID', ''), ('imageConfigID', 'latest'),
                           ('owner', ''), ('service', 'unknown'), ('password', 'synthetic')]:
            values = fixture('account')
            values[key] = value
            bad_cases.append(values)
        for key, value in [('imageConfigID', ''), ('upstreamDigest', 'sha256:' + 'c' * 64)]:
            values = fixture('account')
            values['database'][key] = value
            bad_cases.append(values)
        values = fixture('account')
        values['sourceCommits']['account'] = 'main'
        bad_cases.append(values)
        for values in bad_cases:
            assert list(VALIDATOR.iter_errors(values))
            render(values, directory, ok=False)
        for namespace in ('default', 'account', 'kube-system'):
            render(fixture('account'), directory, namespace=namespace, ok=False)
        render(fixture('account'), directory, release='wrong', ok=False)
        print('PASS 12 rejection fixtures: missing/mutable identity, credential field, namespace/release boundaries')
    print('PASS learning path only; synthetic image IDs, no Kubernetes/runtime/image-build acceptance')


if __name__ == '__main__':
    main()
