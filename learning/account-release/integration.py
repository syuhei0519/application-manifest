#!/usr/bin/env python3
"""Offline Account GitOps proposals and actual Envoy counter evidence checks.

No cluster, Git or HTTP writes. Catalog review is an external trust boundary.
"""
import argparse
import json
import math
import re
from pathlib import Path

from contract import TARGET, ROUTING, require, validate_catalog
from sdk_queries import queries

NS = TARGET['namespace']
NAME = 'account-release'
REPO = 'https://github.com/syuhei0519/application-manifest.git'
POINTERS = {
    'VirtualService': ['/spec/http/0/route/0/weight', '/spec/http/0/route/1/weight'],
    'DestinationRule': ['/spec/subsets/0/labels/rollouts-pod-template-hash',
                        '/spec/subsets/1/labels/rollouts-pod-template-hash'],
}


def image_revision(image):
    require(isinstance(image, str) and re.fullmatch(r'[a-z0-9./_-]+@sha256:[a-f0-9]{64}', image),
            'immutable-image-required')
    return 'sha256-' + image.split('@sha256:')[1][:56]


def application(commit, catalog, image):
    require(isinstance(commit, str) and re.fullmatch(r'[a-f0-9]{40}', commit), 'manifest-commit-required')
    require(image in validate_catalog(catalog), 'reviewed-image-required')
    versions = [image_revision(e['image']) for e in catalog]
    require(len(set(versions)) == len(versions), 'image-version-label-collision')
    entry = next(e for e in catalog if e['image'] == image)
    values = dict(owner='plat170-account', image=image, sourceCommit=entry['source_commit'],
                  imageRevision=image_revision(image))
    project = dict(apiVersion='argoproj.io/v1alpha1', kind='AppProject',
        metadata=dict(name=NAME, namespace=TARGET['control_namespace']),
        spec=dict(sourceRepos=[REPO], destinations=[dict(server='https://kubernetes.default.svc', namespace=NS)],
                  clusterResourceWhitelist=[], namespaceResourceWhitelist=[
                      dict(group='argoproj.io', kind='Rollout'), dict(group='', kind='Service'),
                      dict(group='networking.istio.io', kind='VirtualService'),
                      dict(group='networking.istio.io', kind='DestinationRule')]))
    app = dict(apiVersion='argoproj.io/v1alpha1', kind='Application',
        metadata=dict(name=NAME, namespace=TARGET['control_namespace']),
        spec=dict(project=NAME, source=dict(repoURL=REPO, targetRevision=commit,
                  path='learning/account-release/chart', helm=dict(releaseName=NAME, valuesObject=values)),
                  destination=dict(server='https://kubernetes.default.svc', namespace=NS),
                  syncPolicy=dict(syncOptions=['RespectIgnoreDifferences=true']),
                  ignoreDifferences=[dict(group='networking.istio.io', kind=k, name=NAME, namespace=NS,
                                          jsonPointers=p) for k, p in POINTERS.items()]))
    return [project, app]


def sdk_query_set(stable_hash, candidate_hash, stable_count, candidate_count):
    require(all(isinstance(h, str) and re.fullmatch(r'[a-z0-9]{5,16}', h)
                for h in (stable_hash, candidate_hash)) and stable_hash != candidate_hash,
            'distinct-observed-revision-hashes-required')
    result = {}
    for name, expression in queries(stable_count, candidate_count).items():
        for key, value in {'namespace': NS, 'service': 'account', 'stable-hash': stable_hash,
                           'latest-hash': candidate_hash}.items():
            expression = expression.replace('{{args.' + key + '}}', value)
        result[name.replace('latest-', 'candidate-')] = expression
    return result


# Keep all response codes and versions visible; unknown values must not be filtered away.
TRAFFIC_QUERY = ('istio_requests_total{job="account-release-envoy",reporter="destination",'
                 'source_workload="account-release-client",source_workload_namespace="' + NS + '",'
                 'destination_service_name="account-release",destination_workload_namespace="' + NS + '"}')
TRAFFIC_TIMESTAMP_QUERY = 'timestamp(' + TRAFFIC_QUERY + ')'


def _number(value):
    require(not isinstance(value, bool), 'invalid-counter')
    number = float(value)
    require(math.isfinite(number) and number >= 0, 'invalid-counter')
    return number


def _snapshot(payload, timestamps, versions, boundary, client_boundary, after=False):
    require(payload.get('status') == 'success' and payload.get('data', {}).get('resultType') == 'vector',
            'query-unavailable')
    require(not payload.get('warnings') and not payload.get('infos'), 'partial-query-result')
    require(timestamps.get('status') == 'success' and timestamps.get('data', {}).get('resultType') == 'vector'
            and not timestamps.get('warnings') and not timestamps.get('infos'), 'timestamp-query-unavailable')
    # Instant query value[0] is evaluation time, NOT the underlying scrape time.
    # timestamp(metric) returns the raw sample time in value[1]. Query both at one explicit time.
    times = {}
    for item in timestamps['data']['result']:
        labels = {k: v for k, v in item['metric'].items() if k != '__name__'}
        key = tuple(sorted(labels.items()))
        require(key not in times, 'duplicate-timestamp-series')
        evaluation, sample = map(_number, item['value'])
        require(abs(evaluation - boundary) < 0.001, 'timestamp-evaluation-mismatch')
        require(0 <= boundary - sample < 90, 'stale-traffic-sample')
        require(sample >= client_boundary if after else sample <= client_boundary, 'sample-does-not-cover-client-window')
        times[key] = sample
    result = {}
    for series in payload['data']['result']:
        labels = series['metric']
        require(all(labels.get(k) == v for k, v in {
            '__name__': 'istio_requests_total', 'job': 'account-release-envoy', 'reporter': 'destination',
            'source_workload': 'account-release-client', 'source_workload_namespace': NS,
            'destination_service_name': NAME, 'destination_workload_namespace': NS}.items()), 'wrong-traffic-scope')
        require(labels.get('destination_version') in versions, 'unknown-image-version')
        require(labels.get('response_code') == '200', 'http-error-observed')
        require(bool(labels.get('instance')), 'missing-instance')
        evaluation, counter = series['value']
        require(abs(_number(evaluation) - boundary) < 0.001, 'counter-evaluation-mismatch')
        key = tuple(sorted((k, v) for k, v in labels.items() if k != '__name__'))
        require(key in times, 'missing-sample-timestamp')
        require(key not in result, 'duplicate-traffic-series')
        result[key] = (labels['destination_version'], _number(counter))
    require(result, 'empty-traffic-result')
    require(result.keys() == times.keys(), 'timestamp-coverage-mismatch')
    return result


def verify_traffic(evidence, *, expected_target=TARGET):
    """Exact counter deltas during an exclusive, quiescent request window.

    Caller preserves raw Prometheus replies + client results and controller snapshots.
    Series changes/resets and missing scope fail closed; no Pod-ratio inference.
    """
    require(evidence['target'] == expected_target, 'wrong-target')
    stage = evidence['weight']
    require(type(stage) is int and stage in ROUTING, 'invalid-weight')
    require(evidence['observed_weights'] == {'stable': 100 - stage, 'candidate': stage}, 'routing-drift')
    images = evidence['images']
    require(set(images) == {'stable', 'candidate'} and images['stable'] != images['candidate'], 'distinct-images-required')
    versions = {role: image_revision(img) for role, img in images.items()}
    require(len(set(versions.values())) == 2, 'image-version-label-collision')
    start, end = _number(evidence['start']), _number(evidence['end'])
    require(end > start, 'invalid-window')
    client_start, client_end = map(_number, (evidence['client_window']['start'], evidence['client_window']['end']))
    require(start <= client_start < client_end <= end, 'invalid-client-window')
    spec = ROUTING[stage]
    client = evidence['client']
    require(set(client) == {'sent', 'ok', 'errors', 'timeouts', 'retries', 'warmup_excluded'}, 'invalid-client-summary')
    require(all(type(client[k]) is int and client[k] >= 0 for k in client if k != 'warmup_excluded'), 'invalid-client-counts')
    require(client['warmup_excluded'] is True and client['sent'] == client['ok'] == spec['samples'] and
            client['errors'] == client['timeouts'] == client['retries'] == 0, 'client-window-failed')
    before = _snapshot(evidence['before'], evidence['before_timestamps'], set(versions.values()), start, client_start)
    after = _snapshot(evidence['after'], evidence['after_timestamps'], set(versions.values()), end, client_end, after=True)
    require(before.keys() == after.keys(), 'series-changed-or-missing')
    deltas = {v: 0 for v in versions.values()}
    for key, (version, count) in after.items():
        delta = count - before[key][1]
        require(delta >= 0 and delta.is_integer(), 'counter-reset-or-noninteger')
        deltas[version] += int(delta)
    stable, candidate = deltas[versions['stable']], deltas[versions['candidate']]
    require(stable + candidate == spec['samples'], 'traffic-coverage-mismatch')
    require(spec['candidate_min'] <= candidate <= spec['candidate_max'], 'traffic-outside-contract')
    return dict(weight=stage, stable=stable, candidate=candidate, errors=0, start=start, end=end)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    app = sub.add_parser('application')
    app.add_argument('--manifest-commit', required=True)
    app.add_argument('--catalog', type=Path, required=True)
    app.add_argument('--image', required=True)
    traffic = sub.add_parser('traffic')
    traffic.add_argument('--evidence', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'application':
        result = {'apiVersion': 'v1', 'kind': 'List', 'items': application(args.manifest_commit,
                  json.loads(args.catalog.read_text()), args.image)}
    else:
        result = verify_traffic(json.loads(args.evidence.read_text()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, TypeError, ValueError, OSError):
        raise SystemExit('Account integration input rejected; no operation performed')
