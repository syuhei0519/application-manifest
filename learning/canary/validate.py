#!/usr/bin/env python3
"""Account-only schema/render/ownership/negative/recovery checks. No cluster calls."""
import argparse
import copy
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parent
CHART = ROOT / 'chart'
SOURCE = 'e030275abbf52ecd27b7152ce597b3d89be3f830'
UPSTREAM = 'https://raw.githubusercontent.com/argoproj/argo-rollouts/356117c8e9a21b74b58c2c01d640aa91994522b5/manifests/install.yaml'
UPSTREAM_SHA = 'be25242c55bab7650e0b845da11dbce811f7427deae367e224435e0d766ebe5a'
SCHEMA = json.loads((CHART / 'values.schema.json').read_text())


def fixture():
    values = yaml.safe_load((CHART / 'values.yaml').read_text())
    values.update(owner='synthetic-fixture', imageConfigID='sha256:' + 'a' * 64)
    values['telemetry']['enabled'] = True
    return values


def render(values, namespace='learning-fixture', release='learning-account', success=True):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'values.yaml'
        path.write_text(yaml.safe_dump(values))
        result = subprocess.run(['helm', 'template', release, str(CHART), '--namespace', namespace,
                                 '--kube-version', '1.36.4', '-f', str(path)], capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stderr
        return list(yaml.safe_load_all(result.stdout)) if success else []


def verify(objects, mode='rollout', namespace='learning-fixture'):
    kind = 'Rollout' if mode == 'rollout' else 'Deployment'
    workload_name = 'learning-account-canary' if mode == 'rollout' else 'learning-account'
    services = {'learning-account', 'learning-account-stable', 'learning-account-canary'} if mode == 'rollout' else {'learning-account'}
    assert len(objects) == len(services) + 1
    assert {(o['kind'], o['metadata']['name']) for o in objects} == {(kind, workload_name)} | {('Service', n) for n in services}
    for obj in objects:
        assert obj['metadata']['namespace'] == namespace
        assert obj['metadata']['labels']['learning.local/owner'] == 'synthetic-fixture'
        assert obj['metadata']['labels']['learning.local/service'] == 'account'
    workload = next(o for o in objects if o['kind'] == kind)
    selector = {'learning.local/workload': workload_name}
    assert workload['spec']['selector']['matchLabels'] == selector
    assert all(workload['spec']['template']['metadata']['labels'][k] == v for k, v in selector.items())
    for obj in objects:
        if obj['kind'] == 'Service':
            assert obj['spec']['selector'] == selector
            assert obj['spec']['type'] == 'ClusterIP'
            assert obj['spec']['ports'] == [dict(name='http', port=8080, targetPort='http')]
    pod = workload['spec']['template']['spec']
    api = pod['containers'][0]
    assert api['ports'] == [dict(name='http', containerPort=8080)]
    assert api['image'] == f'local.learning/account:{SOURCE}-' + 'a' * 64
    assert api['imagePullPolicy'] == 'Never'
    assert pod['automountServiceAccountToken'] is False
    env = {x['name']: x for x in api['env']}
    assert env['PGPASSWORD']['valueFrom']['secretKeyRef'] == dict(name='learning-account-db', key='app-password')
    assert env['PGHOST']['value'] == 'learning-account-db'
    assert env['OTEL_EXPORTER_OTLP_ENDPOINT']['value'] == 'http://otel-collector.observability.svc.cluster.local:4318'
    assert env['POD_NAMESPACE']['value'] == 'account'
    if mode == 'rollout':
        assert workload['spec']['replicas'] == 10
        assert workload['spec']['paused'] is False
        assert workload['spec']['strategy']['canary'] == dict(stableService='learning-account-stable', canaryService='learning-account-canary',
            maxSurge=0, maxUnavailable=1, steps=[dict(setWeight=10), dict(pause={}), dict(setWeight=50), dict(pause={}), dict(setWeight=100), dict(pause={})])
        assert 'workloadRef' not in workload['spec']
    return workload


def expect_rejection(call):
    try:
        call()
    except (AssertionError, ValueError):
        return
    raise AssertionError('negative fixture unexpectedly accepted')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    values = fixture()
    data = urllib.request.urlopen(UPSTREAM, timeout=60).read()
    assert hashlib.sha256(data).hexdigest() == UPSTREAM_SHA
    crd = next(x for x in yaml.safe_load_all(data) if x['kind'] == 'CustomResourceDefinition' and x['metadata']['name'] == 'rollouts.argoproj.io')
    rollout_schema = next(v['schema']['openAPIV3Schema'] for v in crd['spec']['versions'] if v['name'] == 'v1alpha1')
    baseline = render(values)
    good = verify(baseline)
    for rev in ('good', 'candidate', 'failed'):
        current = copy.deepcopy(values)
        current['revision'] = rev
        Draft7Validator(SCHEMA).validate(current)
        objs = render(current)
        workload = verify(objs)
        Draft7Validator(rollout_schema).validate(workload)
        api = workload['spec']['template']['spec']['containers'][0]
        assert next(e['value'] for e in api['env'] if e['name'] == 'LOG_LEVEL') == ('info' if rev == 'good' else 'warn')
        assert api['readinessProbe']['httpGet']['path'] == ('/__learning_not_ready' if rev == 'failed' else '/readyz')
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / (rev + '.yaml')).write_text(yaml.safe_dump_all(objs, sort_keys=False))
        assert yaml.safe_load((ROOT / 'profiles' / (rev + '.yaml')).read_text()) == {'revision':rev}
    recovery_values = dict(values, mode='deployment', revision='good')
    recovery_objects = render(recovery_values)
    recovery = verify(recovery_objects, 'deployment')
    # Structural equivalence to the existing baseline API, including enabled SDK env.
    spec = importlib.util.spec_from_file_location('learning_baseline', ROOT.parent / 'validate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old_values = module.fixture('account')
    old_values['sourceCommits']['account'] = SOURCE
    old_values['telemetry']['enabled'] = True
    with tempfile.TemporaryDirectory() as tmp:
        old = list(yaml.safe_load_all(module.render(old_values, Path(tmp))))
    # Compare by identity rather than Helm file sort order.
    expected = {(o['kind'],o['metadata']['name']):o for o in old if o['kind'] == 'Deployment' or (o['kind'] == 'Service' and o['metadata']['name'] == 'learning-account')}
    assert {(o['kind'],o['metadata']['name']):o for o in recovery_objects} == expected
    rollout_pod = copy.deepcopy(good['spec']['template'])
    del rollout_pod['metadata']['annotations']['learning.local/config-revision']
    rollout_pod['metadata']['labels']['learning.local/workload'] = 'learning-account'
    assert rollout_pod == recovery['spec']['template']
    if args.output:
        (args.output / 'deployment.yaml').write_text(yaml.safe_dump_all(recovery_objects, sort_keys=False))
    # Invalid rendered selectors/ports/namespaces and ownership violations are rejected.
    for mutation in ('selector', 'port', 'namespace', 'extra-resource', 'pod-selector'):
        bad = copy.deepcopy(baseline)
        service = next(o for o in bad if o['kind'] == 'Service')
        if mutation == 'selector': service['spec']['selector'] = {'learning.local/workload':'learning-order'}
        if mutation == 'port': service['spec']['ports'][0]['targetPort'] = 9090
        if mutation == 'namespace': service['metadata']['namespace'] = 'other'
        if mutation == 'extra-resource': bad.append(dict(kind='Secret', metadata=dict(name='unexpected')))
        if mutation == 'pod-selector': next(o for o in bad if o['kind'] == 'Rollout')['spec']['template']['metadata']['labels']['learning.local/workload'] = 'other'
        expect_rejection(lambda: verify(bad))
    for key, value in [('imageConfigID','latest'), ('sourceCommit','main'), ('owner',''), ('service','order'),
                       ('mode','blueGreen'), ('revision','unknown'), ('selector',{}), ('port',9090), ('namespace','default')]:
        bad = dict(values, **{key:value})
        assert list(Draft7Validator(SCHEMA).iter_errors(bad))
        render(bad, success=False)
    render(values, namespace='default', success=False)
    render(values, release='learning-order', success=False)
    render(dict(values, mode='deployment', revision='candidate'), success=False)
    with tempfile.TemporaryDirectory() as tmp:
        file = Path(tmp) / 'values.yaml'
        file.write_text(yaml.safe_dump(values))
        subprocess.run(['helm','lint','--strict',str(CHART),'--namespace','learning-fixture','-f',str(file)],check=True)
    print('PASS Account: three revisions match pinned Rollout CRD; 10/50/100 pauses; 4 API-only resources; disjoint selectors; DB/Secret/SDK contract; recovery equals existing API; 17 negative fixtures. Runtime untested.')


if __name__ == '__main__':
    main()
