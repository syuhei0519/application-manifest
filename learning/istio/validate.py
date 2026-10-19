#!/usr/bin/env python3
"""Offline routing contracts against the fixed platform Istio 1.31.2 source."""
import argparse
import copy
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft7Validator, ValidationError

ROOT = Path(__file__).resolve().parent
PLATFORM_SHA = 'efd3a1f5a31eaf904bc787456034e4cebe932f7f'
NS = 'learning-istio-demo'
HOST = 'mesh-demo.' + NS + '.svc.cluster.local'
WEIGHTS = {'default': [100, 0], '90-10': [90, 10], '50-50': [50, 50]}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.PIPE, text=True)


def verify(docs, profile, image, schemas):
    expected = {('ServiceAccount', 'mesh-demo'), ('ConfigMap', 'mesh-demo-code'),
                ('Service', 'mesh-demo'), ('Deployment', 'mesh-demo-v1'),
                ('Deployment', 'mesh-demo-client')}
    if profile != 'plain':
        expected |= {('Deployment', 'mesh-demo-v2'), ('VirtualService', 'mesh-demo'),
                     ('DestinationRule', 'mesh-demo')}
    require(len(docs) == len(expected), 'resource count')
    require({(d['kind'], d['metadata']['name']) for d in docs} == expected, 'resource boundary')
    for d in docs:
        require(d['metadata']['namespace'] == NS, 'namespace boundary')
    by_kind = {d['kind']: d for d in docs}
    service = by_kind['Service']['spec']
    require(service == {'type': 'ClusterIP', 'selector': {'app': 'mesh-demo'}, 'ports': [
        {'name': 'http', 'port': 8080, 'targetPort': 'http', 'appProtocol': 'http'}]}, 'service boundary')
    require(by_kind['ServiceAccount']['automountServiceAccountToken'] is False, 'service account')
    for d in [o for o in docs if o['kind'] == 'Deployment']:
        name = d['metadata']['name'].removeprefix('mesh-demo-')
        labels = {'app': 'mesh-demo-client' if name == 'client' else 'mesh-demo', 'version': name}
        spec = d['spec']
        require(spec['replicas'] == 1, 'replica budget')
        require(spec['selector']['matchLabels'] == labels, 'deployment selector')
        require(spec['template']['metadata']['labels'] == labels, 'pod labels')
        pod = spec['template']['spec']
        require(pod['serviceAccountName'] == 'mesh-demo' and pod['automountServiceAccountToken'] is False, 'pod account')
        container, = pod['containers']
        require(container['image'] == image, 'fixture image')
        require(container['args'] == (['idle'] if name == 'client' else ['serve', name]), 'revision body source')
        require(container.get('ports', []) == ([] if name == 'client' else [{'name': 'http', 'containerPort': 8080}]), 'container port')
        require(not pod.get('initContainers') and not pod.get('hostNetwork'), 'un-injected pod boundary')
    if profile == 'plain':
        return
    for kind in ['VirtualService', 'DestinationRule']:
        Draft7Validator(schemas[kind]).validate(by_kind[kind])
        require(by_kind[kind]['apiVersion'] == 'networking.istio.io/v1', 'Istio v1 API')
    dr = by_kind['DestinationRule']['spec']
    require(dr == {'host': HOST, 'exportTo': ['.'], 'subsets': [
        {'name': 'v1', 'labels': {'version': 'v1'}}, {'name': 'v2', 'labels': {'version': 'v2'}}]}, 'subset host/labels')
    vs = by_kind['VirtualService']['spec']
    require(set(vs) == {'hosts', 'exportTo', 'gateways', 'http'}, 'virtual service fields')
    require(vs['hosts'] == [HOST] and vs['exportTo'] == ['.'] and vs['gateways'] == ['mesh'], 'route host boundary')
    route, = vs['http']
    require(set(route) == {'name', 'match', 'retries', 'route'}, 'route fields')
    require(route['name'] == 'demo-only' and route['match'] == [{'method': {'exact': 'GET'}, 'uri': {'exact': '/version'}}], 'route match')
    require(route['retries'] == {'attempts': 0}, 'no retries')
    require(route['route'] == [
        {'destination': {'host': HOST, 'subset': 'v' + str(i + 1), 'port': {'number': 8080}}, 'weight': w}
        for i, w in enumerate(WEIGHTS[profile])], 'route host/port/subset/weight')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', required=True, type=Path)
    parser.add_argument('--istioctl', type=Path, help='optional pinned binary for offline injection/analyze')
    parser.add_argument('--output', required=True, type=Path, help='new directory only')
    args = parser.parse_args()
    require(run(['git', '-C', args.platform, 'rev-parse', 'HEAD']).strip() == PLATFORM_SHA, 'platform SHA')
    platform = args.platform / 'learning/istio'
    lock = json.loads((platform / 'versions.lock.json').read_text())
    helm = run(['which', 'helm']).strip()
    require(hashlib.sha256(Path(helm).read_bytes()).hexdigest() == lock['helm']['binary']['sha256'], 'Helm 4.2.4 checksum')
    image = lock['images']['fixture']['ref']
    crds = list(yaml.safe_load_all((platform / 'vendor/base/files/crd-all.gen.yaml').read_text()))
    schemas = {d['spec']['names']['kind']: next(v['schema']['openAPIV3Schema'] for v in d['spec']['versions'] if v['name'] == 'v1')
               for d in crds if d and d['spec']['names']['kind'] in ('VirtualService', 'DestinationRule')}
    args.output.mkdir(parents=True, exist_ok=False)
    docs_by_profile = {}
    for profile in ['default', '90-10', '50-50', 'plain']:
        command = ['helm', 'template', 'mesh-demo', ROOT / 'chart', '--namespace', NS,
                   '--kube-version', '1.36.4', '-f', ROOT / f'profiles/{profile}.yaml']
        run(['helm', 'lint', '--strict', ROOT / 'chart', '-f', ROOT / f'profiles/{profile}.yaml'])
        rendered = run(command)
        docs = list(yaml.safe_load_all(rendered))
        verify(docs, profile, image, schemas)
        docs_by_profile[profile] = docs
        (args.output / f'{profile}.yaml').write_text(rendered)
    # Return to the same default desired state after each split; no deletion ambiguity.
    require(run(['helm', 'template', 'mesh-demo', ROOT / 'chart', '--namespace', NS, '--kube-version', '1.36.4']) ==
            (args.output / 'default.yaml').read_text(), 'default recovery render')
    negatives = 0
    base = docs_by_profile['90-10']
    # Necessary mutated-render fixtures: schema alone cannot reject valid-but-wrong routing.
    mutations = [
        ('Service', ['metadata', 'namespace'], 'account'),
        ('VirtualService', ['spec', 'hosts'], ['backend.account.svc.cluster.local']),
        ('VirtualService', ['spec', 'http', 0, 'route', 0, 'destination', 'host'], 'other'),
        ('VirtualService', ['spec', 'http', 0, 'route', 0, 'destination', 'subset'], 'v3'),
        ('VirtualService', ['spec', 'http', 0, 'route', 0, 'destination', 'port', 'number'], 9090),
        ('VirtualService', ['spec', 'http', 0, 'route', 0, 'weight'], 89),
        ('DestinationRule', ['spec', 'host'], 'other'),
        ('DestinationRule', ['spec', 'subsets', 1, 'labels', 'version'], 'v3'),
        ('Service', ['spec', 'selector', 'app'], 'mesh-demo-client'),
        ('Service', ['spec', 'ports', 0, 'targetPort'], 'wrong'),
        ('Deployment', ['spec', 'template', 'metadata', 'labels', 'version'], 'wrong'),
        ('Deployment', ['spec', 'selector', 'matchLabels', 'app'], 'other'),
    ]
    for kind, path, value in mutations:
        docs = copy.deepcopy(base)
        target = next(d for d in docs if d['kind'] == kind)
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        try:
            verify(docs, '90-10', image, schemas)
        except (ValueError, ValidationError):
            # JSON schema failures and contract failures are both rejections.
            negatives += 1
        else:
            raise ValueError(f'negative fixture accepted: {kind} {path}')
    for option in ['mode=unknown', 'phase=other', 'host=other', 'port=9090', 'subset=v3',
                   'image=other', 'mode=split50,phase=plain']:
        p = subprocess.run(['helm', 'template', 'mesh-demo', str(ROOT / 'chart'), '--namespace', NS,
                            '--set', option], capture_output=True)
        require(p.returncode != 0, 'invalid values accepted: ' + option)
        negatives += 1
    p = subprocess.run(['helm', 'template', 'mesh-demo', str(ROOT / 'chart'), '--namespace', 'account'], capture_output=True)
    require(p.returncode != 0, 'wrong namespace accepted')
    if args.istioctl:
        require(hashlib.sha256(args.istioctl.read_bytes()).hexdigest() == lock['istio']['istioctl']['sha256'], 'Istio 1.31.2 checksum')
        injection_checks(args, platform, image, lock)
    print(f'PASS: 4 lint/render + official v1 CRD schemas, default recovery, {negatives + 1} negative fixtures')
    print('Offline injection/analyze: ' + ('PASS (regular and native sidecars)' if args.istioctl else 'NOT RUN; supply --istioctl'))


def injection_checks(args, platform, image, lock):
    # Render existing control configuration; never install or connect to a cluster.
    control = list(yaml.safe_load_all(run(['helm', 'template', 'istiod', platform / 'vendor/istiod',
        '--namespace', 'istio-learning-system', '-f', platform / 'install/istiod.values.yaml'])))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for d in control:
            if not d or d['kind'] != 'ConfigMap':
                continue
            if d['metadata']['name'].startswith('istio-sidecar-injector-'):
                (tmp / 'config.yaml').write_text(d['data']['config'])
                (tmp / 'values.json').write_text(d['data']['values'])
            if d['metadata']['name'] == 'istio-learning-1-31-2':
                (tmp / 'mesh.yaml').write_text(d['data']['mesh'])
        for profile in ['default', '90-10', '50-50']:
            run([args.istioctl, 'analyze', '--use-kube=false', '--failure-threshold', 'Warning',
                 platform / 'bootstrap/namespaces.yaml', args.output / f'{profile}.yaml'])
        for native in [False, True]:
            docs = list(yaml.safe_load_all((args.output / 'default.yaml').read_text()))
            for d in docs:
                if d['kind'] == 'Deployment':
                    d['spec']['template']['metadata']['annotations'] = {'sidecar.istio.io/nativeSidecar': str(native).lower()}
            (tmp / 'input.yaml').write_text(yaml.safe_dump_all(docs))
            injected = list(yaml.safe_load_all(run([args.istioctl, 'kube-inject', '-f', tmp / 'input.yaml',
                '--injectConfigFile', tmp / 'config.yaml', '--valuesFile', tmp / 'values.json',
                '--meshConfigFile', tmp / 'mesh.yaml', '-i', 'istio-learning-system', '-r', 'learning-1-31-2'])))
            for d in injected:
                if not d or d['kind'] != 'Deployment':
                    continue
                pod = d['spec']['template']['spec']
                proxies = [c for c in pod['containers'] + pod.get('initContainers', []) if c['name'] == 'istio-proxy']
                proxy, = proxies
                require(proxy['image'] == lock['images']['proxy']['ref'], 'injected proxy digest')
                require((proxy in pod.get('initContainers', [])) == native, 'native proxy placement')
                require((proxy.get('restartPolicy') == 'Always') == native, 'native restart policy')
                app, = [c for c in pod['containers'] if c['name'] == 'fixture']
                require(app['image'] == image, 'injection changed app image')


if __name__ == '__main__':
    main()
