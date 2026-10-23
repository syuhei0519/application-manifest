"""PLAT-171 shared input and observed-runtime contract (no writes)."""
from copy import deepcopy
import re

from contract import TERMINAL, require, validate_catalog
from integration import application

ENTITY = 'component:default/account'
ENVIRONMENT = 'local'
REQUEST_FIELDS = {'entity', 'environment', 'image', 'strategy', 'promotion_mode',
                  'expected_git_revision', 'expected_generation', 'expected_version', 'request_id'}


def validate_mode(strategy, mode):
    require((strategy == 'Canary' and mode in ('manual', 'auto')) or
            (strategy == 'Rolling' and mode is None), 'invalid-strategy-or-mode')


def validate_request(request, catalog, current):
    """Reject unknown inputs, not merely hide unsupported controls in the UI."""
    require(isinstance(request, dict) and set(request) == REQUEST_FIELDS, 'invalid-request-fields')
    require(request['entity'] == ENTITY and request['environment'] == ENVIRONMENT, 'target-out-of-scope')
    require(request['image'] in validate_catalog(catalog), 'reviewed-image-required')
    validate_mode(request['strategy'], request['promotion_mode'])
    require(isinstance(request['request_id'], str) and
            re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', request['request_id']), 'invalid-request-id')
    require(isinstance(request['expected_git_revision'], str) and
            re.fullmatch(r'[0-9a-f]{40}', request['expected_git_revision']), 'exact-git-revision-required')
    for key, observed in [('expected_git_revision', 'git_revision'),
                          ('expected_generation', 'generation'), ('expected_version', 'version')]:
        if key != 'expected_git_revision':
            require(type(request[key]) is int and request[key] >= 0, 'invalid-version')
        require(request[key] == current[observed], 'stale-' + observed)
    require(current['state'] in {'idle', 'succeeded', 'restored'} and current['git_runtime_drift'] is False,
            'release-active-or-drift')
    require(current['current_image'] in validate_catalog(catalog), 'current-image-unverified')
    require(request['image'] != current['current_image'], 'image-unchanged')
    return deepcopy(request)


def desired_documents(commit, catalog, image, strategy, mode, bootstrap_hash='', analysis_enabled=False):
    validate_mode(strategy, mode)
    require(type(analysis_enabled) is bool and (mode != 'auto' or analysis_enabled),
            'auto-requires-reviewed-controller-plugin')
    docs = application(commit, catalog, image)
    app = docs[1]
    app['spec']['source']['helm']['valuesObject'].update(strategy=strategy, promotionMode=mode)
    app['spec']['source']['helm']['valuesObject']['analysisPluginEnabled'] = analysis_enabled
    if analysis_enabled:
        docs[0]['spec']['namespaceResourceWhitelist'].append(dict(group='argoproj.io', kind='AnalysisTemplate'))
    require(not bootstrap_hash or re.fullmatch(r'[a-z0-9]{5,16}', bootstrap_hash), 'invalid-bootstrap-hash')
    if bootstrap_hash:
        app['spec']['source']['helm']['valuesObject']['bootstrapHash'] = bootstrap_hash
    if strategy == 'Rolling':
        # Git owns the full unweighted route and empty subsets after a terminal switch.
        # Leaving Canary ignore pointers would preserve stale weights/hash selectors.
        app['spec']['ignoreDifferences'] = []
    return docs


def switch_plan(request, catalog, current):
    """Separate reviewed Git changes, each observed before the next.

    Seed both subsets with the observed old hash while the Rolling route stays
    subset-free. Activate Canary with the old image before starting the new one.
    These are required phases, never proof that any phase has actually completed.
    """
    validate_request(request, catalog, current)
    old = current['current_image']
    strategy, mode = request['strategy'], request['promotion_mode']
    phases = []
    if (strategy, mode) != (current['strategy'], current['promotion_mode']):
        pod_hash = current.get('revision')
        require(isinstance(pod_hash, str) and re.fullmatch(r'[a-z0-9]{5,16}', pod_hash),
                'observed-current-hash-required')
        if strategy == 'Canary' and current['strategy'] == 'Rolling':
            pod_hash = current.get('revision')
            require(isinstance(pod_hash, str) and re.fullmatch(r'[a-z0-9]{5,16}', pod_hash),
                    'observed-current-hash-required')
            phases.append(dict(name='seed-subsets', image=old, strategy='Rolling', promotion_mode=None,
                               bootstrap_hash=pod_hash, verify='old-image-ready-subset-free-route-both-subsets-current-hash'))
        phases.append(dict(name='activate-mode', image=old, strategy=strategy, promotion_mode=mode,
                           bootstrap_hash=pod_hash,
                           verify='old-image-ready-controller-observed-mode-and-actual-traffic'))
    phases.append(dict(name='start-image', image=request['image'], strategy=strategy,
                       promotion_mode=mode, bootstrap_hash='', verify='new-generation-started'))
    return phases


def runtime_status(rollout, strategy, mode, *, expected_uid):
    """Project real Rollout conditions; never infer Rolling readiness from metrics."""
    validate_mode(strategy, mode)
    metadata, spec, status = (rollout.get(k, {}) for k in ('metadata', 'spec', 'status'))
    if (not expected_uid or metadata.get('uid') != expected_uid or
            metadata.get('name') != 'account-release' or
            metadata.get('namespace') != 'learning-account-release'):
        return dict(state='unavailable', reason='runtime-identity-mismatch', actions=[])
    generation = metadata.get('generation')
    result = dict(strategy=strategy, promotion_mode=mode, generation=generation,
                  observed_generation=status.get('observedGeneration'),
                  revision=status.get('currentPodHash'), stable_revision=status.get('stableRS'),
                  current_step=status.get('currentStepIndex'), resource_version=metadata.get('resourceVersion'),
                  state='syncing', reason='generation-not-observed', actions=[])
    # Read identity only from the actual resource, including when it is not ready.
    containers = spec.get('template', {}).get('spec', {}).get('containers', [])
    result['requested_image'] = next((c.get('image') for c in containers if c.get('name') == 'api'), None)
    # Fixed Rollouts v1.10.1 serializes observedGeneration as a decimal string.
    if type(generation) is not int or status.get('observedGeneration') != str(generation):
        return result
    canary = spec.get('strategy', {}).get('canary')
    if not isinstance(canary, dict):
        return dict(result, state='unavailable', reason='unexpected-runtime-strategy')
    if strategy == 'Rolling' and any(canary.get(k) for k in ('steps', 'trafficRouting', 'analysis')):
        return dict(result, state='unavailable', reason='rolling-has-canary-controls')
    conditions = {c.get('type'): c for c in status.get('conditions', [])}
    if status.get('abort'):
        # Abort/timeout does not prove stable traffic, Git restoration or health.
        # Only a separately verified durable restore can release the operation lock.
        return dict(result, state='recovering', reason='runtime-aborted')
    if conditions.get('Progressing', {}).get('reason') == 'ProgressDeadlineExceeded':
        # Keep exclusion held while exposing the actual failure to the operator.
        return dict(result, state='recovering', reason='runtime-progress-deadline-exceeded')
    desired = spec.get('replicas')
    ready = (type(desired) is int and desired > 0 and status.get('replicas') == desired and
             status.get('updatedReplicas') == desired and status.get('readyReplicas') == desired and
             status.get('availableReplicas') == desired and status.get('currentPodHash') and
             status.get('stableRS') == status.get('currentPodHash') and
             conditions.get('Available', {}).get('status') == 'True')
    if status.get('phase') == 'Healthy' and ready and not status.get('pauseConditions'):
        return dict(result, state='succeeded', reason='runtime-all-replicas-ready')
    if strategy == 'Rolling':
        return dict(result, state='rolling', reason='runtime-progressing')
    pauses = {p.get('reason') for p in status.get('pauseConditions', [])}
    actions = ['abort']
    if mode == 'manual' and pauses == {'CanaryPauseStep'}:
        actions.insert(0, 'promote')
    return dict(result, state='paused' if pauses else 'running', reason=','.join(sorted(pauses)) or
                'runtime-progressing', actions=actions)
