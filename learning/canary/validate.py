#!/usr/bin/env python3
# Canary Chart と成功・失敗 profile を描画し、段階配備と解析の設定を静的に確認します。
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
FIXTURE = ROOT / 'fixture'
SOURCE = 'e030275abbf52ecd27b7152ce597b3d89be3f830'
UPSTREAM = 'https://raw.githubusercontent.com/argoproj/argo-rollouts/356117c8e9a21b74b58c2c01d640aa91994522b5/manifests/install.yaml'
UPSTREAM_SHA = 'be25242c55bab7650e0b845da11dbce811f7427deae367e224435e0d766ebe5a'
SCHEMA = json.loads((CHART / 'values.schema.json').read_text())


def fixture():
    values = yaml.safe_load((CHART / 'values.yaml').read_text())
    values.update(owner='synthetic-fixture', imageConfigID='sha256:' + 'a' * 64)
    values['telemetry']['enabled'] = True
    return values


def render(values, namespace='learning-fixture', release='learning-account', success=True, chart=CHART):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'values.yaml'
        path.write_text(yaml.safe_dump(values))
        result = subprocess.run(['helm', 'template', release, str(chart), '--namespace', namespace,
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


def verify_analysis(objects, baseline, case):
    expected = {(o['kind'], o['metadata']['name']) for o in baseline} | {
        ('AnalysisTemplate', 'learning-account-analysis')}
    assert len(objects) == 5
    assert {(o['kind'], o['metadata']['name']) for o in objects} == expected
    for obj in objects:
        assert obj['metadata']['namespace'] == 'learning-fixture'
        assert obj['metadata']['labels']['learning.local/owner'] == 'synthetic-fixture'
        assert obj['metadata']['labels']['learning.local/service'] == 'account'
    rollout = next(o for o in objects if o['kind'] == 'Rollout')
    steps = rollout['spec']['strategy']['canary']['steps']
    assert steps[0] == {'setWeight': 10}
    assert steps[2:] == [{'setWeight':50}, {'pause':{}}, {'setWeight':100}, {'pause':{}}]
    analysis = steps[1]['analysis']
    assert analysis['templates'] == [{'templateName':'learning-account-analysis'}]
    assert analysis['args'] == [dict(name='service',value='account'), dict(name='namespace',value='learning-fixture'),
        dict(name='latest-hash',valueFrom={'podTemplateHashValue':'Latest'}),
        dict(name='stable-hash',valueFrom={'podTemplateHashValue':'Stable'}), dict(name='case',value=case)]
    assert rollout['spec']['template']['metadata']['annotations']['learning.local/analysis-case'] == case
    normalized = copy.deepcopy(rollout)
    normalized['spec']['strategy']['canary']['steps'][1] = {'pause':{}}
    del normalized['spec']['template']['metadata']['annotations']['learning.local/analysis-case']
    assert normalized == next(o for o in baseline if o['kind'] == 'Rollout')
    for obj in baseline:
        if obj['kind'] == 'Service':
            assert obj == next(o for o in objects if (o['kind'],o['metadata']['name']) == (obj['kind'],obj['metadata']['name']))
    for obj in objects:
        if (obj['kind'], obj['metadata']['name']) not in {(o['kind'],o['metadata']['name']) for o in baseline}:
            assert obj['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '-1'
    template = next(o for o in objects if o['kind'] == 'AnalysisTemplate')
    assert template['spec']['args'] == [dict(name=n) for n in ('service','namespace','latest-hash','stable-hash','case')]
    metric = template['spec']['metrics'][0]
    for key, value in dict(count=1, failureLimit=0, inconclusiveLimit=0, consecutiveErrorLimit=0).items():
        assert metric[key] == value
    assert metric['provider'] == {'web':dict(
        url='http://learning-account-analysis-provider.learning-fixture.svc.cluster.local:8080/measure?service={{args.service}}&namespace={{args.namespace}}&latest-hash={{args.latest-hash}}&stable-hash={{args.stable-hash}}&case={{args.case}}',
        jsonPath='{$}', timeoutSeconds=1)}
    return rollout, template


def verify_metrics(objects):
    assert {(o['kind'], o['metadata']['name']) for o in objects} == {
        ('Rollout', 'learning-account-canary'), ('AnalysisTemplate', 'learning-account-metrics'),
        *[('Service', n) for n in ('learning-account', 'learning-account-stable', 'learning-account-canary')]}
    assert len(objects) == 5
    assert all(o['metadata']['namespace'] == 'learning-fixture' for o in objects)
    rollout = next(o for o in objects if o['kind'] == 'Rollout')
    steps = rollout['spec']['strategy']['canary']['steps']
    assert steps[0:2] == [dict(setWeight=10), dict(pause={})]
    assert steps[2]['analysis']['templates'] == [dict(templateName='learning-account-metrics')]
    assert steps[2]['analysis']['args'][2:4] == [
        dict(name='latest-hash', valueFrom=dict(podTemplateHashValue='Latest')),
        dict(name='stable-hash', valueFrom=dict(podTemplateHashValue='Stable'))]
    assert steps[3:] == [dict(setWeight=50), dict(pause={}), dict(setWeight=100), dict(pause={})]
    template = next(o for o in objects if o['kind'] == 'AnalysisTemplate')
    assert template['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '-1'
    queries = json.loads((CHART / 'files/revision-queries.json').read_text())
    assert set(queries) == {r+'-'+m for r in ('latest','stable') for m in ('requests','availability','latency')}
    assert len(template['spec']['metrics']) == 6
    for metric in template['spec']['metrics']:
        for key, value in dict(count=1, failureLimit=0, inconclusiveLimit=0, consecutiveErrorLimit=0).items():
            assert metric[key] == value
        assert metric['provider'] == dict(prometheus=dict(
            address='http://learning-prometheus.observability.svc.cluster.local:9090', timeout=5, query=queries[metric['name']]))
        threshold = '20' if metric['name'].endswith('requests') else ('0.99' if metric['name'].endswith('availability') else '0.95')
        assert 'len(result) == 1' in metric['successCondition']
        assert 'result[0] >= ' + threshold in metric['successCondition']
        assert 'len(result) != 1' in metric['failureCondition']
        assert '!isNaN(result[0]) && !isInf(result[0])' in metric['successCondition']
        q = queries[metric['name']]
        assert all(x in q for x in ['[5m]', 'account_namespace="{{args.namespace}}"', 'learning_service="{{args.service}}"',
                                    'rollout_revision="{{args.', 'sum by(instance)(increase(', 'count_over_time(', 'timestamp(', 'le="+Inf"'])
        assert 'vector(0)' not in q
    return rollout, template


def verify_provider(objects):
    assert len(objects) == 3
    assert {(o['kind'], o['metadata']['name']) for o in objects} == {
        (kind, 'learning-account-analysis-provider') for kind in ('ConfigMap','Deployment','Service')}
    for obj in objects:
        assert obj['metadata']['namespace'] == 'learning-fixture'
        assert obj['metadata']['labels']['learning.local/owner'] == 'synthetic-fixture'
        assert obj['metadata']['labels']['learning.local/service'] == 'account'
        assert not obj['metadata'].get('annotations')
    provider = next(o for o in objects if o['kind'] == 'Deployment')
    assert provider['spec']['replicas'] == 1
    selector = {'learning.local/workload':'learning-account-analysis-provider'}
    assert provider['spec']['selector']['matchLabels'] == selector
    assert all(provider['spec']['template']['metadata']['labels'][k] == v for k,v in selector.items())
    pod = provider['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert pod['securityContext']['runAsNonRoot'] is True
    container = pod['containers'][0]
    assert container['image'] == 'docker.io/library/python@sha256:9bbb8720ae0a24a6ca8dd678bfdf57818fe70caf54c52a53ec01d8db43405056'
    assert container['resources'] == dict(requests=dict(cpu='25m',memory='32Mi'),limits=dict(cpu='200m',memory='64Mi'))
    assert container['securityContext'] == dict(allowPrivilegeEscalation=False,readOnlyRootFilesystem=True,capabilities={'drop':['ALL']})
    assert container['command'] == ['python3','-B','/fixture/provider.py']
    config = next(o for o in objects if o['kind'] == 'ConfigMap')
    assert config['data'] == {'provider.py':(FIXTURE/'files/provider.py').read_text()}
    assert pod['volumes'] == [dict(name='fixture',configMap=dict(name=config['metadata']['name']))]
    assert provider['spec']['template']['metadata']['annotations']['learning.local/provider-checksum'] == hashlib.sha256(config['data']['provider.py'].encode()).hexdigest()
    service = next(o for o in objects if o['kind'] == 'Service' and o['metadata']['name'] == 'learning-account-analysis-provider')
    assert service['spec'] == dict(type='ClusterIP',selector=selector,ports=[dict(name='http',port=8080,targetPort='http')])


def expect_rejection(call):
    try:
        call()
    except (AssertionError, ValueError, KeyError):
        return
    raise AssertionError('negative fixture unexpectedly accepted')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    values = fixture()
    data = urllib.request.urlopen(UPSTREAM, timeout=60).read()
    assert hashlib.sha256(data).hexdigest() == UPSTREAM_SHA
    crds = list(yaml.safe_load_all(data))
    crd = next(x for x in crds if x['kind'] == 'CustomResourceDefinition' and x['metadata']['name'] == 'rollouts.argoproj.io')
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
    schemas = {x['spec']['names']['kind']: next(v['schema']['openAPIV3Schema'] for v in x['spec']['versions'] if v['name']=='v1alpha1')
               for x in crds if x['kind']=='CustomResourceDefinition'}
    pod_templates = []
    for case in ('success', 'failure', 'inconclusive', 'missing', 'timeout'):
        current = copy.deepcopy(values)
        current['revision'] = 'candidate'
        current['analysis'] = dict(enabled=True, case=case)
        Draft7Validator(SCHEMA).validate(current)
        objs = render(current)
        manual = render(dict(current, analysis=dict(enabled=False,case=case)))
        rollout, template = verify_analysis(objs, manual, case)
        Draft7Validator(schemas['Rollout']).validate(rollout)
        Draft7Validator(schemas['AnalysisTemplate']).validate(template)
        # Structural specimen only: a real generated Run/ownerReference must be observed locally.
        run = dict(apiVersion='argoproj.io/v1alpha1',kind='AnalysisRun',metadata=dict(name='schema-fixture',namespace='learning-fixture'),
                   spec=dict(metrics=template['spec']['metrics'],args=[dict(name=n,value=v) for n,v in
                       [('service','account'),('namespace','learning-fixture'),('latest-hash','new-fixture'),('stable-hash','stable-fixture'),('case',case)]]))
        Draft7Validator(schemas['AnalysisRun']).validate(run)
        pod_templates.append(rollout['spec']['template'])
        for mutation in ('namespace','provider-resource','step','hash-arg','limit'):
            bad = copy.deepcopy(objs)
            if mutation == 'namespace': bad[0]['metadata']['namespace'] = 'other'
            if mutation == 'provider-resource': bad.append(dict(kind='Deployment',metadata=dict(name='learning-account-analysis-provider')))
            if mutation == 'step': next(o for o in bad if o['kind']=='Rollout')['spec']['strategy']['canary']['steps'][1] = {'pause':{}}
            if mutation == 'hash-arg': next(o for o in bad if o['kind']=='Rollout')['spec']['strategy']['canary']['steps'][1]['analysis']['args'][2] = dict(name='latest-hash',value='main')
            if mutation == 'limit': next(o for o in bad if o['kind']=='AnalysisTemplate')['spec']['metrics'][0]['consecutiveErrorLimit'] = 4
            expect_rejection(lambda: verify_analysis(bad,manual,case))
        if args.output:
            (args.output / ('analysis-' + case + '.yaml')).write_text(yaml.safe_dump_all(objs,sort_keys=False))
    assert len({json.dumps(p,sort_keys=True) for p in pod_templates}) == 5
    for profile in ('metrics-good', 'metrics-candidate'):
        current = copy.deepcopy(values)
        current.update(yaml.safe_load((ROOT / 'profiles' / (profile + '.yaml')).read_text()))
        Draft7Validator(SCHEMA).validate(current)
        objs = render(current)
        rollout, template = verify_metrics(objs)
        Draft7Validator(schemas['Rollout']).validate(rollout)
        Draft7Validator(schemas['AnalysisTemplate']).validate(template)
        for mutation in ('resource', 'pause', 'hash', 'query', 'timeout', 'error-limit'):
            bad = copy.deepcopy(objs)
            r = next(o for o in bad if o['kind'] == 'Rollout')
            m = next(o for o in bad if o['kind'] == 'AnalysisTemplate')['spec']['metrics'][0]
            if mutation == 'resource': bad.append(dict(kind='ConfigMap',metadata=dict(name='forbidden')))
            if mutation == 'pause': r['spec']['strategy']['canary']['steps'].pop(1)
            if mutation == 'hash': r['spec']['strategy']['canary']['steps'][2]['analysis']['args'][2] = dict(name='latest-hash',value='main')
            if mutation == 'query': m['provider']['prometheus']['query'] = 'vector(1)'
            if mutation == 'timeout': m['provider']['prometheus']['timeout'] = 30
            if mutation == 'error-limit': m['consecutiveErrorLimit'] = 3
            expect_rejection(lambda: verify_metrics(bad))
        if args.output:
            (args.output / (profile + '.yaml')).write_text(yaml.safe_dump_all(objs,sort_keys=False))
        bad = copy.deepcopy(current)
        bad['telemetry']['enabled'] = False
        assert list(Draft7Validator(SCHEMA).iter_errors(bad))
        render(bad,success=False)
    print('PASS metrics gate: 10% preparation pause, six revision queries, Prometheus timeout5s/error0, schemas and safety rejections')
    provider_objects = render({'owner':'synthetic-fixture'}, release='learning-account-analysis-provider', chart=FIXTURE)
    verify_provider(provider_objects)
    bad = copy.deepcopy(provider_objects)
    bad[0]['metadata']['namespace'] = 'other'
    expect_rejection(lambda: verify_provider(bad))
    bad = copy.deepcopy(provider_objects)
    next(o for o in bad if o['kind']=='Deployment')['spec']['selector']['matchLabels'] = {}
    expect_rejection(lambda: verify_provider(bad))
    render({'owner':'synthetic-fixture'},namespace='default',release='learning-account-analysis-provider',chart=FIXTURE,success=False)
    render({'owner':'synthetic-fixture'},release='learning-account',chart=FIXTURE,success=False)
    render({'owner':'synthetic-fixture','image':'latest'},release='learning-account-analysis-provider',chart=FIXTURE,success=False)
    if args.output:
        (args.output / 'provider-fixture.yaml').write_text(yaml.safe_dump_all(provider_objects,sort_keys=False))

    for analysis in ({'enabled':True,'case':'unknown'}, {'enabled':'true','case':'success'}, {'enabled':True,'case':'success','url':'https://other'}):
        bad = dict(values,analysis=analysis)
        assert list(Draft7Validator(SCHEMA).iter_errors(bad))
        render(bad,success=False)
    for mode,revision in (('deployment','good'),('rollout','failed')):
        bad = dict(values,mode=mode,revision=revision,analysis=dict(enabled=True,case='success'))
        assert list(Draft7Validator(SCHEMA).iter_errors(bad))
        render(bad,success=False)
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
    print('PASS independent fixture: 3 local-Test resources; Argo chart has no provider resources; fixed owner/namespace/release/image boundary.')
    print('PASS opt-in analysis: five case PodTemplates; Latest/Stable args; pinned Rollout/AnalysisTemplate/AnalysisRun schemas; provider boundary; negative fixtures. Controller/cluster AC untested.')
    print('PASS Account: three revisions match pinned Rollout CRD; 10/50/100 pauses; 4 API-only resources; disjoint selectors; DB/Secret/SDK contract; recovery equals existing API; 17 negative fixtures. Runtime untested.')


if __name__ == '__main__':
    main()
