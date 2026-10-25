#!/usr/bin/env python3
# Account リリース用の初期構成を、レビューできる提案ファイルとして作ります。
"""Render reviewable installation proposals; never create credentials or apply.

Only a clean checkout at PLATFORM_SHA is accepted. Upstream Core/Rollouts bytes
are downloaded with the existing pinned checksum verifier. Output contains no Secret.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

import yaml

from contract import TARGET, require

PLATFORM_SHA = '867cb81f071d06f7521b93aa110ae001200142a4'
ROOT = Path(__file__).resolve().parent
NS, CONTROL, OWNER = TARGET['namespace'], TARGET['control_namespace'], 'plat170-account'


def build(base, cache):
    outputs = base.render(NS, CONTROL, OWNER, cache)
    del outputs['sample.yaml']
    for obj in outputs['control.yaml']:
        if obj['kind'] == 'ConfigMap' and obj['metadata']['name'] == 'argocd-cm':
            entries = yaml.safe_load(obj['data']['resource.inclusions'])
            entries.append(dict(apiGroups=['networking.istio.io'], kinds=['VirtualService', 'DestinationRule'], clusters=['*']))
            obj['data']['resource.inclusions'] = yaml.safe_dump(entries)
    rule = base.rule
    for obj in outputs['target.yaml']:
        if obj['kind'] == 'Role' and obj['metadata']['name'] == 'argocd-learning':
            obj['rules'] = [
                rule('argoproj.io', 'rollouts', 'get list watch create update patch delete'),
                rule('', 'services', 'get list watch create update patch delete'),
                rule('networking.istio.io', 'virtualservices destinationrules', 'get list watch create update patch delete'),
                rule('argoproj.io', 'analysistemplates analysisruns', 'get list watch'),
                rule('apps', 'replicasets deployments', 'get list watch'),
                rule('', 'pods events', 'get list watch')]
        if obj['kind'] == 'Role' and obj['metadata']['name'] == 'learning-rollouts':
            obj['rules'] = [
                rule('argoproj.io', 'rollouts experiments analysistemplates analysisruns', 'get list watch'),
                rule('argoproj.io', 'rollouts rollouts/status rollouts/finalizers', 'update patch', 'account-release'),
                rule('apps', 'replicasets', 'get list watch create update patch delete'),
                rule('apps', 'deployments', 'get list watch'),
                rule('', 'services', 'get list watch'),
                rule('', 'pods', 'get list watch update'),
                rule('', 'events', 'create update patch'),
                rule('', 'configmaps', 'get list watch', 'argo-rollouts-config argo-rollouts-notification-configmap'),
                rule('', 'secrets', 'get list watch', 'argo-rollouts-notification-secret'),
                rule('coordination.k8s.io', 'leases', 'create get update'),
                rule('batch', 'jobs', 'get list watch'),
                rule('networking.k8s.io', 'ingresses', 'get list watch'),
                rule('networking.istio.io', 'virtualservices destinationrules', 'get list watch'),
                rule('networking.istio.io', 'virtualservices destinationrules', 'update', 'account-release')]
        if obj['kind'] == 'Deployment' and obj['metadata']['name'] == 'learning-rollouts':
            template = obj['spec']['template']
            template.setdefault('metadata', {}).setdefault('annotations', {})['sidecar.istio.io/inject'] = 'false'
            template['spec']['containers'][0]['args'].append('--istio-api-version=v1')
    for objects in outputs.values():
        require(all(x['kind'] != 'Secret' for x in objects), 'unexpected-secret')
    return outputs


def monitoring_overlay(docs):
    """Deterministic overlay on the pinned monitoring chart, not a kubectl edit."""
    selected = [d for d in docs if d and d['kind'] == 'ConfigMap' and
                d['metadata']['name'] == 'learning-prometheus-config' and d['metadata']['namespace'] == 'observability']
    require(len(selected) == 1, 'exact-monitoring-config-required')
    config = yaml.safe_load(selected[0]['data']['prometheus.yml'])
    require(not any(j['job_name'] == 'account-release-envoy' for j in config['scrape_configs']), 'duplicate-envoy-job')
    sdk = next(j for j in config['scrape_configs'] if j['job_name'] == 'otel-application')
    require(sdk['static_configs'][0]['labels']['account_namespace'] == NS, 'wrong-sdk-namespace')
    config['scrape_configs'].append(yaml.safe_load((ROOT/'envoy-scrape.yaml').read_text()))
    selected[0]['data']['prometheus.yml'] = yaml.safe_dump(config, sort_keys=False)
    return docs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--platform', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--helm', default='helm')
    args = p.parse_args()
    def git(*parts):
        return subprocess.check_output(['git', '-C', str(args.platform), *parts], text=True).strip()
    require(git('rev-parse', 'HEAD') == PLATFORM_SHA and not git('status', '--porcelain'), 'clean-pinned-platform-required')
    require(not args.output.exists(), 'new-output-directory-required')
    spec = importlib.util.spec_from_file_location('fixed_platform', args.platform/'learning/progressive-delivery/render.py')
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    args.cache.mkdir(parents=True, exist_ok=True)
    outputs = build(base, args.cache)
    outputs['namespaces.yaml'] = [dict(apiVersion='v1', kind='Namespace', metadata=dict(name=ns,
        labels={'learning.local/owner': OWNER, **({'istio.io/rev': 'learning-1-31-2'} if ns == NS else {})}))
        for ns in (NS, CONTROL, 'observability', 'istio-learning-system')]
    client_labels = {'app': 'account-release-client', 'version': 'client', 'learning.local/owner': OWNER}
    client_cm = dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name='account-release-client', namespace=NS),
                     data={name: (ROOT/name).read_text() for name in ('load.py', 'contract.py')})
    client = dict(apiVersion='apps/v1', kind='Deployment', metadata=dict(name='account-release-client', namespace=NS),
        spec=dict(replicas=1, selector=dict(matchLabels={'app': 'account-release-client'}),
            template=dict(metadata=dict(labels=client_labels, annotations={'sidecar.istio.io/inject': 'true',
                          'proxy.istio.io/config': '{"holdApplicationUntilProxyStarts":true}'}),
                spec=dict(automountServiceAccountToken=False,
                    securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, seccompProfile=dict(type='RuntimeDefault')),
                    containers=[dict(name='client', image='docker.io/library/python@sha256:9bbb8720ae0a24a6ca8dd678bfdf57818fe70caf54c52a53ec01d8db43405056',
                        command=['python3', '-c', 'import time; time.sleep(86400)'],
                        securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities=dict(drop=['ALL'])),
                        resources=dict(requests=dict(cpu='10m', memory='32Mi'), limits=dict(cpu='200m', memory='128Mi')),
                        volumeMounts=[dict(name='code', mountPath='/work', readOnly=True)])],
                    volumes=[dict(name='code', configMap=dict(name='account-release-client'))]))))
    outputs['client.yaml'] = [client_cm, client]
    args.output.mkdir(parents=True)
    istio = args.platform/'learning/istio'
    values = yaml.safe_load((istio/'install/istiod.values.yaml').read_text())
    values['meshConfig']['discoverySelectors'][0]['matchLabels']['kubernetes.io/metadata.name'] = NS
    values_file = args.output/'istiod.values.yaml'
    values_file.write_text(yaml.safe_dump(values))
    def helm(release, chart, namespace, *options):
        return list(yaml.safe_load_all(subprocess.check_output([args.helm, 'template', release, str(chart),
                '--namespace', namespace, '--kube-version', '1.36.4', *map(str, options)], text=True)))
    outputs['istio-base.yaml'] = helm('istio-base', istio/'vendor/base', 'istio-learning-system', '-f', istio/'install/base.values.yaml')
    outputs['istiod.yaml'] = helm('istiod', istio/'vendor/istiod', 'istio-learning-system', '-f', values_file)
    outputs['monitoring.yaml'] = monitoring_overlay(helm('learning-monitoring', args.platform/'learning/monitoring/chart',
                 'observability', '--set', 'owner='+OWNER, '--set', 'accountNamespace='+NS))
    inventory = []
    for name, objects in outputs.items():
        objects = [o for o in objects if o]
        require(all(o['kind'] != 'Secret' for o in objects), 'unexpected-secret')
        (args.output/name).write_text(yaml.safe_dump_all(objects, sort_keys=False))
        for obj in objects:
            if obj['kind'] in ('Role', 'ClusterRole', 'RoleBinding', 'ClusterRoleBinding', 'ServiceAccount'):
                inventory.append(obj)
    (args.output/'access-inventory.json').write_text(json.dumps(inventory, indent=2))
    print('Offline proposal rendered; NOT approved or applied. Secrets, registration, DB and runtime acceptance pending.')


if __name__ == '__main__':
    main()
