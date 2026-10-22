#!/usr/bin/env python3
"""PLAT-169 executable specification. Pure functions; no UI, Git or cluster writes."""
from copy import deepcopy
import math
import re

TARGET = {'context': 'kind-plat170-account', 'namespace': 'learning-account-release',
          'control_namespace': 'learning-account-control', 'application': 'account-release',
          'rollout': 'account-release', 'service': 'account-release',
          'virtual_service': 'account-release', 'destination_rule': 'account-release',
          'metrics_job': 'account-backend', 'metrics_service': 'account'}
POLICY = {'window_seconds': 300, 'minimum_requests': 20, 'availability_min': .99,
          'latency_bucket_seconds': .25, 'latency_fraction_min': .95, 'query_timeout_seconds': 5}
ROUTING = {10: {'samples': 1000, 'candidate_min': 60, 'candidate_max': 140},
           50: {'samples': 1000, 'candidate_min': 440, 'candidate_max': 560},
           100: {'samples': 200, 'candidate_min': 200, 'candidate_max': 200},
           0: {'samples': 200, 'candidate_min': 0, 'candidate_max': 0}}
ACTIVE = {'proposed', 'syncing', 'canary10', 'canary50', 'canary100', 'rolling', 'aborting', 'aborted', 'restoring'}
TERMINAL = {'idle', 'succeeded', 'restored', 'failed'}


class Rejected(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Rejected(reason)


def validate_catalog(catalog):
    """Runtime accepts reviewed OCI manifests, not guessed tags or fixture digests."""
    require(isinstance(catalog, list) and len(catalog) >= 2, 'image-catalog-required')
    digests = set()
    for entry in catalog:
        require(re.fullmatch(r'[a-z0-9./_-]+@sha256:[0-9a-f]{64}', entry['image']), 'immutable-image-required')
        require(re.fullmatch(r'[0-9a-f]{40}', entry['source_commit']), 'source-pin-required')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', entry['config_digest']), 'config-pin-required')
        require(entry['platform'] == 'linux/amd64' and entry['verified'] is True, 'image-evidence-unverified')
        require(isinstance(entry['provenance'], str) and entry['provenance'].strip(), 'provenance-required')
        manifest = entry['image'].split('@')[1]
        require(manifest not in digests, 'duplicate-manifest-digest')
        digests.add(manifest)
    return {entry['image'] for entry in catalog}


def validate_start(request, catalog, current):
    images = validate_catalog(catalog)
    require(request['target'] == TARGET, 'target-out-of-scope')
    require(request['image'] in images and request['previous_image'] in images and
            request['image'] != request['previous_image'], 'distinct-reviewed-images-required')
    require(request['previous_image'] == current['stable_image'], 'stale-previous-image')
    require(request['strategy'] in ('Canary', 'Rolling'), 'invalid-strategy')
    require((request['strategy'] == 'Canary' and request['mode'] in ('manual', 'auto')) or
            (request['strategy'] == 'Rolling' and request['mode'] is None), 'invalid-mode')
    require(current['state'] in TERMINAL and not current['git_runtime_drift'], 'release-active-or-drift')
    require(type(request['expected_version']) is int and type(current['version']) is int and
            request['expected_version'] == current['version'], 'stale-version')
    require(isinstance(request['idempotency_key'], str) and bool(request['idempotency_key'].strip()), 'idempotency-required')
    return {'state': 'proposed', 'strategy': request['strategy'], 'mode': request['mode'],
            'version': current['version'] + 1, 'verdict': 'unknown', 'paused': False, 'reason': 'awaiting-git-approval'}


def evaluate_metrics(observation, expected, now):
    """Missing/NaN/timeout is inconclusive, never success or a numeric zero."""
    if observation.get('window_seconds') != POLICY['window_seconds'] or observation.get('query_status') != 'success':
        return 'inconclusive', 'window-or-query-unavailable'
    if not isinstance(expected, dict) or not expected or any(r not in ('stable', 'candidate') for r in expected):
        return 'inconclusive', 'invalid-role-scope'
    end = observation.get('window_end')
    if (isinstance(end, bool) or not isinstance(end, (float, int)) or not math.isfinite(end) or
            isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now) or
            not 0 <= now - end < 90 or observation.get('window_start') != end - POLICY['window_seconds']):
        return 'inconclusive', 'stale-or-invalid-window'
    failures, unknown = [], []
    for role, identity in expected.items():
        data = observation.get(role)
        if not isinstance(data, dict) or data.get('coverage_complete') is not True:
            unknown.append(role + '-coverage-missing')
            continue
        if (not isinstance(identity, dict) or set(identity) != {'revision_hash', 'image'} or
                not all(isinstance(v, str) and v for v in identity.values()) or
                any(data.get(k) != v for k, v in identity.items())):
            unknown.append(role + '-revision-mismatch')
            continue
        sample = data.get('last_sample_at')
        if (isinstance(sample, bool) or not isinstance(sample, (int, float)) or not math.isfinite(sample) or
                not 0 <= now - sample < 90 or sample > end):
            unknown.append(role + '-stale-sample')
            continue
        values = [data.get(k) for k in ('requests', 'availability', 'latency_fraction')]
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
            unknown.append(role + '-nonfinite-or-missing')
            continue
        requests, availability, latency = values
        if requests < 0 or not 0 <= availability <= 1 or not 0 <= latency <= 1:
            unknown.append(role + '-invalid-range')
            continue
        if requests < POLICY['minimum_requests']:
            unknown.append(role + '-insufficient-traffic')
            continue
        if availability < POLICY['availability_min']:
            failures.append(role + '-availability-below-99pct')
        if latency < POLICY['latency_fraction_min']:
            failures.append(role + '-latency-250ms-below-95pct')
    if failures:
        return 'failed', ','.join(failures + unknown)
    return ('inconclusive', ','.join(unknown)) if unknown else ('passed', 'all-observed-roles-within-policy')


def transition(release, event, *, expected_version, approved=False, drift=False, verdict=None, reason=None):
    """Reference state machine; persistent lock/CAS and adapters are later issues."""
    require(type(expected_version) is int and expected_version == release['version'], 'stale-version')
    require(release['strategy'] in ('Canary', 'Rolling'), 'invalid-strategy')
    state, strategy, mode = release['state'], release['strategy'], release['mode']
    require((strategy == 'Canary' and mode in ('manual', 'auto')) or (strategy == 'Rolling' and mode is None), 'invalid-mode')
    require(state in ACTIVE | TERMINAL, 'unknown-state')
    require(not drift or event in ('abort', 'stable_routed'), 'git-runtime-drift')
    new = deepcopy(release)
    if event == 'approve_git':
        require(state == 'proposed' and approved is True, 'git-approval-required')
        new.update(state='syncing', reason='approved-git-change')
    elif event == 'synced':
        require(state == 'syncing', 'sync-not-pending')
        new.update(state='canary10' if strategy == 'Canary' else 'rolling', verdict='unknown', reason='observing')
    elif event == 'metrics':
        require(state in ('canary10', 'canary50', 'canary100', 'rolling'), 'not-observing')
        require(verdict in ('passed', 'failed', 'inconclusive'), 'invalid-verdict')
        require(isinstance(reason, str) and bool(reason.strip()), 'evaluation-reason-required')
        new.update(verdict=verdict, reason='metrics-' + verdict, metrics_reason=reason)
        if verdict == 'failed':
            new.update(state='aborting', reason='metric-threshold-violation')
        elif verdict == 'inconclusive':
            new['paused'] = True
        elif verdict == 'passed' and strategy == 'Canary' and mode == 'auto' and not release['paused']:
            new.update(state={'canary10': 'canary50', 'canary50': 'canary100', 'canary100': 'succeeded'}[state],
                       verdict='passed' if state == 'canary100' else 'unknown')
        # inconclusive stays at its stage (pause); no auto retry/promote/abort on missing data.
    elif event == 'promote':
        require(strategy == 'Canary' and mode == 'manual' and state.startswith('canary'), 'manual-canary-only')
        require(release['verdict'] == 'passed' and not release['paused'], 'metrics-not-passed-or-paused')
        new.update(state={'canary10': 'canary50', 'canary50': 'canary100', 'canary100': 'succeeded'}[state],
                   verdict='passed' if state == 'canary100' else 'unknown', reason='manual-promotion')
    elif event == 'pause':
        require(strategy == 'Canary' and state.startswith('canary'), 'canary-only')
        new.update(paused=True, verdict='unknown', reason='operator-pause')
    elif event == 'resume':
        require(strategy == 'Canary' and state.startswith('canary') and release['paused'] and
                release['verdict'] == 'passed', 'fresh-passed-metrics-required')
        new.update(paused=False, verdict='unknown', reason='operator-resume-reobserve')
    elif event == 'rolling_ready':
        require(strategy == 'Rolling' and state == 'rolling' and release['verdict'] == 'passed', 'rolling-not-ready')
        new.update(state='succeeded', reason='rolling-healthy')
    elif event == 'abort':
        require(state in ('syncing', 'canary10', 'canary50', 'canary100', 'rolling'), 'not-abortable')
        new.update(state='aborting', reason='operator-abort')
    elif event == 'stable_routed':
        require(state == 'aborting', 'abort-not-pending')
        new.update(state='aborted', reason='stable-traffic-restored-git-still-pending')
    elif event == 'restore_git':
        require(state == 'aborted' and approved is True, 'restore-approval-required')
        new.update(state='restoring', reason='previous-digest-committed')
    elif event == 'restore_verified':
        require(state == 'restoring', 'restore-not-pending')
        new.update(state='restored', reason='git-runtime-crud-and-two-syncs-verified')
    else:
        raise Rejected('unknown-operation')
    new['version'] += 1
    return new
