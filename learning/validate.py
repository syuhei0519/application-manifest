#!/usr/bin/env python3
"""Offline render/schema fixtures for this learning path only. No cluster calls."""
import argparse
import copy
import hashlib
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
            if not any(x['name'] == 'OTEL_EXPORTER_OTLP_ENDPOINT' for x in container['env']):
                assert not {'POD_NAMESPACE', 'POD_UID', 'DEPLOYMENT_ENVIRONMENT'} & env.keys()
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


def account_telemetry(values, directory, namespace='learning-fixture', output=None):
    """Verify and optionally seal only the API Deployment; never apply resources."""
    assert values['service'] == 'account', 'Account values required'
    assert values['telemetry']['enabled'] is False, 'use the existing opt-in-off values as baseline'
    VALIDATOR.validate(values)
    on_values = copy.deepcopy(values)
    on_values['telemetry']['enabled'] = True
    enabled = list(yaml.safe_load_all(render(on_values, directory, namespace=namespace)))
    disabled = list(yaml.safe_load_all(render(values, directory, namespace=namespace)))
    assert len(enabled) == len(disabled)
    api_pairs = []
    for on, off in zip(enabled, disabled):
        if on['kind'] != 'Deployment':
            assert on == off, 'non-API resources, including the completed migration Job, must be unchanged'
            continue
        assert on['metadata']['name'] == 'learning-account'
        assert on['metadata']['namespace'] == namespace
        unchanged = copy.deepcopy(on)
        containers = unchanged['spec']['template']['spec']['containers']
        assert len(containers) == 1 and containers[0]['name'] == 'api'
        env = containers[0]['env']
        assert env[-4:] == [
            {'name': 'OTEL_EXPORTER_OTLP_ENDPOINT', 'value': 'http://otel-collector.observability.svc.cluster.local:4318'},
            {'name': 'DEPLOYMENT_ENVIRONMENT', 'value': 'local'},
            {'name': 'POD_NAMESPACE', 'value': 'account'},
            {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}}]
        del env[-4:]
        assert unchanged == off, 'API change must be exactly the four telemetry env inputs'
        api_pairs.append((off, on))
    assert len(api_pairs) == 1
    if output:
        assert not output.exists(), 'use a new output directory; preserve previous fixed bytes'
        output.mkdir(parents=True)
        files = []
        for name, obj in zip(('account-api-before.yaml', 'account-api-telemetry.yaml'), api_pairs[0]):
            data = yaml.safe_dump(obj, sort_keys=False).encode('utf-8')
            (output / name).write_bytes(data)
            files.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        obj = api_pairs[0][0]
        inventory = {
            'resources': [{'apiVersion': obj['apiVersion'], 'kind': 'Deployment',
                           'namespace': namespace, 'name': 'learning-account',
                           'owner': obj['metadata']['labels']['learning.local/owner']}],
            'files': files,
            'excluded_from_apply_and_restore': ['Job', 'StatefulSet', 'PersistentVolumeClaim',
                                                'Secret', 'ConfigMap', 'Service'],
            'baseline': 'existing approved opt-in-off values; verify against the actual API before application'}
        (output / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    print('PASS Account telemetry only: API delta is exactly four env inputs; Job/DB/other objects unchanged; apply/restore inventory is one Deployment')


def order_payment(directory):
    """Order API-only opt-in; all other objects and migration raw bytes stay fixed."""
    url = 'http://learning-payment:8080'
    for service in SERVICES:
        values = fixture(service)
        absent = copy.deepcopy(values)
        del absent['orderPaymentURL']
        VALIDATOR.validate(absent)
        disabled = render(values, directory)
        assert render(absent, directory) == disabled, 'absent and empty must render identically'
        values['orderPaymentURL'] = url
        if service != 'order':
            assert list(VALIDATOR.iter_errors(values))
            render(values, directory, ok=False)
            continue
        VALIDATOR.validate(values)
        enabled = render(values, directory)
        before = list(yaml.safe_load_all(disabled))
        after = list(yaml.safe_load_all(enabled))
        assert len(before) == len(after)
        deployments = 0
        for off, on in zip(before, after):
            if on['kind'] != 'Deployment':
                assert off == on, 'non-API resource changed'
                continue
            deployments += 1
            assert on['metadata']['name'] == 'learning-order'
            env = on['spec']['template']['spec']['containers'][0]['env']
            assert env.pop() == {'name': 'ORDER_PAYMENT_URL', 'value': url}
            assert on == off, 'only the one API env may change'
        assert deployments == 1
        migration = lambda text: [part for part in text.split('---')
                                  if '# Source: learning-service/templates/migration.yaml' in part]
        assert len(migration(disabled)) == 1
        assert migration(enabled) == migration(disabled), 'migration bytes changed'
        for bad in (True, 8080, [], 'https://learning-payment:8080',
                    'http://learning-payment:8080/', 'http://other:8080',
                    'http://user:pass@learning-payment:8080', ' '):
            values['orderPaymentURL'] = bad
            assert list(VALIDATOR.iter_errors(values))
            render(values, directory, ok=False)
    print('PASS Order payment: absent=empty for all four services; one Order API env only; migration bytes and all non-API objects unchanged; other services and 8 invalid values rejected')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--order-payment-only', action='store_true', help='run only Order payment opt-in checks')
    mode.add_argument('--account-telemetry-only', action='store_true', help='run only the R1 Account deployment boundary check')
    parser.add_argument('--account-values', type=Path, help='existing approved opt-in-off values, without changing image/DB/other settings')
    parser.add_argument('--namespace', default='learning-fixture')
    parser.add_argument('--output', type=Path, help='new directory for Deployment-only before/telemetry files and fixed inventory')
    args = parser.parse_args()
    if (args.account_values or args.output) and not args.account_telemetry_only:
        parser.error('--account-values/--output require --account-telemetry-only')
    if args.order_payment_only:
        with tempfile.TemporaryDirectory(prefix='order-payment-') as tmp:
            order_payment(Path(tmp))
        return
    if args.account_telemetry_only:
        values = yaml.safe_load(args.account_values.read_text()) if args.account_values else fixture('account')
        with tempfile.TemporaryDirectory(prefix='account-telemetry-') as tmp:
            account_telemetry(values, Path(tmp), args.namespace, args.output)
        return
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
        account_telemetry(fixture('account'), directory)
        order_payment(directory)
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
        for service in ('order', 'payment', 'notification'):
            values = fixture(service)
            values['telemetry']['enabled'] = True
            bad_cases.append(values)
        values = fixture('account')
        values['telemetry']['enabled'] = 'true'
        bad_cases.append(values)
        for values in bad_cases:
            assert list(VALIDATOR.iter_errors(values))
            render(values, directory, ok=False)
        for namespace in ('default', 'account', 'kube-system'):
            render(fixture('account'), directory, namespace=namespace, ok=False)
        render(fixture('account'), directory, release='wrong', ok=False)
        print('PASS 16 rejection fixtures: identity, credentials, namespace/release, Account-only boolean opt-in')
    print('PASS learning path only; synthetic image IDs, no Kubernetes/runtime/image-build acceptance')


if __name__ == '__main__':
    main()
