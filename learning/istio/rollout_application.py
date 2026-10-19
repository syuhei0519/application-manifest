#!/usr/bin/env python3
"""Offline Core proposal; no RBAC, credentials, cluster calls, or automatic sync."""
import argparse
import re
from pathlib import Path
import yaml

NS = 'learning-istio-demo'
NAME = 'mesh-rollout'
REPO = 'https://github.com/syuhei0519/application-manifest.git'
POINTERS = {
    'VirtualService': ['/spec/http/0/route/0/weight', '/spec/http/0/route/1/weight'],
    'DestinationRule': ['/spec/subsets/0/labels/rollouts-pod-template-hash',
                        '/spec/subsets/1/labels/rollouts-pod-template-hash'],
}


def application(control, commit, revision):
    if not re.fullmatch(r'learning-[a-z0-9]([-a-z0-9]*[a-z0-9])?', control) or len(control) > 63 or control == NS:
        raise ValueError('distinct learning-* control namespace required')
    if not re.fullmatch('[a-f0-9]{40}', commit) or revision not in ('v1', 'v2', 'failed'):
        raise ValueError('fixed commit and bounded revision required')
    project = dict(apiVersion='argoproj.io/v1alpha1', kind='AppProject', metadata=dict(name=NAME, namespace=control),
        spec=dict(sourceRepos=[REPO], destinations=[dict(server='https://kubernetes.default.svc', namespace=NS)],
                  clusterResourceWhitelist=[], namespaceResourceWhitelist=[
                      dict(group='argoproj.io', kind='Rollout'), dict(group='', kind='Service'),
                      dict(group='networking.istio.io', kind='VirtualService'),
                      dict(group='networking.istio.io', kind='DestinationRule')]))
    app = dict(apiVersion='argoproj.io/v1alpha1', kind='Application', metadata=dict(name=NAME, namespace=control),
        spec=dict(project=NAME, source=dict(repoURL=REPO, targetRevision=commit, path='learning/istio/rollout-chart',
                  helm=dict(releaseName=NAME, valueFiles=['../rollout-profiles/'+revision+'.yaml'])),
                  destination=dict(server='https://kubernetes.default.svc', namespace=NS),
                  syncPolicy=dict(syncOptions=['RespectIgnoreDifferences=true']),
                  ignoreDifferences=[dict(group='networking.istio.io', kind=k, name=NAME, namespace=NS,
                                          jsonPointers=p) for k, p in POINTERS.items()]))
    return [project, app]


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control-namespace', required=True)
    p.add_argument('--manifest-commit', required=True)
    p.add_argument('--revision', choices=['v1', 'v2', 'failed'], required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    docs = application(a.control_namespace, a.manifest_commit, a.revision)
    with a.output.open('x') as stream:
        yaml.safe_dump_all(docs, stream, sort_keys=False)
