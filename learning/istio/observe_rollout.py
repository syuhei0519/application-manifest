#!/usr/bin/env python3
"""Read saved JSON snapshots; print only selected routing evidence, never raw objects."""
import argparse
import json
from pathlib import Path

HOST = 'mesh-rollout.learning-istio-demo.svc.cluster.local'
STAGES = {'initial': (0, None), 'ten': (10, 1), 'fifty': (50, 3),
          'hundred': (100, 5), 'promoted': (0, None), 'aborted': (0, None)}


def check(rollout, vs, dr, routes, stage):
    weight, index = STAGES[stage]
    for obj, kind in [(rollout, 'Rollout'), (vs, 'VirtualService'), (dr, 'DestinationRule')]:
        if (obj['kind'], obj['metadata']['name'], obj['metadata']['namespace']) != (kind, 'mesh-rollout', 'learning-istio-demo'):
            raise ValueError('object boundary')
    status = rollout['status']
    if status.get('observedGeneration') != str(rollout['metadata']['generation']):
        raise ValueError('stale Rollout observation')
    stable, latest = status['stableRS'], status['currentPodHash']
    if not stable or not latest:
        raise ValueError('missing revision hashes')
    if index is not None:
        if (status.get('currentStepIndex') != index or stable == latest or status.get('abort') or
            not any(c['reason'] == 'CanaryPauseStep' for c in status.get('pauseConditions', []))):
            raise ValueError('expected distinct revisions at the requested pause')
    elif stage == 'aborted':
        if not status.get('abort') or stable == latest:
            raise ValueError('abort must retain the distinct failed candidate')
    elif stable != latest or status.get('abort') or status.get('phase') != 'Healthy':
        raise ValueError('expected completed stable revision')
    subsets = dr['spec']['subsets']
    if [s['name'] for s in subsets] != ['stable', 'canary'] or dr['spec']['host'] != HOST:
        raise ValueError('subset boundary')
    for subset, hash_value in zip(subsets, [stable, latest]):
        if subset['labels'] != {'app': 'mesh-rollout', 'rollouts-pod-template-hash': hash_value}:
            raise ValueError('subset hash mismatch')
    http, = vs['spec']['http']
    expected = [dict(destination=dict(host=HOST, subset=s, port=dict(number=8080)), weight=w)
                for s, w in [('stable', 100-weight), ('canary', weight)]]
    if (vs['spec'].get('hosts') != [HOST] or vs['spec'].get('gateways') != ['mesh'] or
        http.get('retries') != {'attempts':0} or http['name'] != 'demo-only' or http['route'] != expected or
        http['match'] != [{'method': {'exact': 'GET'}, 'uri': {'exact': '/version'}}]):
        raise ValueError('VirtualService does not match stage')
    # istioctl proxy-config routes CLIENT -o json: only target vhost/GET route, not unrelated routes.
    matches = []
    for config in routes:
        for host in config.get('virtualHosts', []):
            if HOST not in host.get('domains', []) and HOST+':8080' not in host.get('domains', []):
                continue
            for route in host.get('routes', []):
                match = route.get('match', {})
                if match.get('path') != '/version' or not any(
                    h.get('name') == ':method' and (h.get('exactMatch') == 'GET' or
                    h.get('stringMatch') == {'exact': 'GET'}) for h in match.get('headers', [])):
                    continue
                action = route.get('route', {})
                if 'cluster' in action:
                    weights = {action['cluster']: 100}
                else:
                    weights = {c['name']: c['weight'] for c in action.get('weightedClusters', {}).get('clusters', [])}
                wanted = {f'outbound|8080|{s}|{HOST}': w for s,w in [('stable',100-weight),('canary',weight)] if w}
                if {k:v for k,v in weights.items() if v} != wanted:
                    raise ValueError('active Envoy weights mismatch')
                matches.append(weights)
    if len(matches) != 1:
        raise ValueError('expected exactly one active Envoy target route')
    return dict(stage=stage, stableHash=stable, latestHash=latest, weights=[100-weight,weight], passed=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['rollout', 'vs', 'dr', 'routes']:
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--stage', choices=STAGES, required=True)
    a = p.parse_args()
    result = check(*(json.loads(getattr(a, n).read_text()) for n in ['rollout','vs','dr','routes']), a.stage)
    print(json.dumps(result, sort_keys=True))
