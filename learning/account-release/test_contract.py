import copy
import unittest
from contract import TARGET, Rejected, evaluate_metrics, transition, validate_start, validate_catalog

IDENTITIES = {r: {'revision_hash': 'hash-' + r, 'image': 'example.invalid/account@sha256:' + c * 64}
              for r, c in [('stable', 'a'), ('candidate', 'b')]}


def evaluate(value, roles):
    return evaluate_metrics(value, {r: IDENTITIES[r] for r in roles}, now=1000)


def observation(**change):
    role = {'requests': 100, 'availability': 1, 'latency_fraction': .99, 'coverage_complete': True, 'last_sample_at': 990}
    value = {'window_seconds': 300, 'window_start': 700, 'window_end': 1000, 'query_status': 'success',
             'stable': {**role, **IDENTITIES['stable']}, 'candidate': {**role, **IDENTITIES['candidate']}}
    value.update(change)
    return value


def release(strategy='Canary', mode='manual'):
    return {'state': 'proposed', 'strategy': strategy, 'mode': mode, 'version': 1,
            'verdict': 'unknown', 'paused': False, 'reason': 'awaiting-git-approval'}


def step(r, event, **kw):
    if event == 'metrics' and 'reason' not in kw:
        kw['reason'] = 'fixture-' + kw['verdict']
    return transition(r, event, expected_version=r['version'], **kw)


class ContractTests(unittest.TestCase):
    def test_five_representative_scenarios(self):
        for mode in ('manual', 'auto'):
            r = step(step(release(mode=mode), 'approve_git', approved=True), 'synced')
            for expected in ('canary10', 'canary50', 'canary100'):
                self.assertEqual(r['state'], expected)
                r = step(r, 'metrics', verdict='passed')
                if mode == 'manual':
                    r = step(r, 'promote')
            self.assertEqual(r['state'], 'succeeded')
            self.assertEqual(r['verdict'], 'passed')
        r = step(step(release(), 'approve_git', approved=True), 'synced')
        r = step(r, 'metrics', verdict='failed')
        self.assertEqual(r['state'], 'aborting')
        r = step(r, 'stable_routed')
        self.assertEqual(r['state'], 'aborted')
        with self.assertRaises(Rejected):
            step(r, 'restore_git')
        r = step(step(r, 'restore_git', approved=True), 'restore_verified')
        self.assertEqual(r['state'], 'restored')
        r = step(step(release(mode='auto'), 'approve_git', approved=True), 'synced')
        r = step(r, 'metrics', verdict='inconclusive')
        self.assertEqual((r['state'], r['paused']), ('canary10', True))
        r = step(r, 'metrics', verdict='passed')
        self.assertEqual(r['state'], 'canary10')
        r = step(r, 'resume')
        self.assertEqual(r['verdict'], 'unknown')
        r = step(step(release('Rolling', None), 'approve_git', approved=True), 'synced')
        for event in ('promote', 'pause', 'resume'):
            with self.assertRaises(Rejected):
                step(r, event)
        self.assertEqual(step(step(r, 'metrics', verdict='passed'), 'rolling_ready')['state'], 'succeeded')

    def test_illegal_operations_stale_approval_and_drift(self):
        for state in ('proposed', 'syncing', 'canary10', 'canary50', 'canary100', 'rolling', 'aborting', 'aborted', 'restoring', 'succeeded'):
            r = release()
            r['state'] = state
            for event in ('switch_strategy', 'delete', 'full_promote', 'invented'):
                with self.subTest(state=state, event=event), self.assertRaises(Rejected):
                    step(r, event)
            with self.assertRaises(Rejected):
                transition(r, 'abort', expected_version=0)
        with self.assertRaises(Rejected):
            step(release(), 'approve_git')
        r = step(step(release(), 'approve_git', approved=True), 'synced')
        with self.assertRaises(Rejected):
            step(r, 'promote')
        with self.assertRaises(Rejected):
            step(r, 'metrics', verdict='passed', drift=True)
        self.assertEqual(step(r, 'abort', drift=True)['state'], 'aborting')

    def test_metrics_unavailable_is_not_zero_or_success(self):
        for v in (None, float('nan'), float('inf'), -1, True, '100'):
            o = observation()
            o['candidate']['requests'] = v
            self.assertEqual(evaluate(o, ['stable', 'candidate'])[0], 'inconclusive')
        for v in ('timeout', 'error', None):
            self.assertEqual(evaluate(observation(query_status=v), ['stable', 'candidate'])[0], 'inconclusive')
        o = observation()
        o['candidate']['requests'] = 19
        self.assertEqual(evaluate(o, ['stable', 'candidate'])[0], 'inconclusive')
        o['candidate']['requests'] = 20
        o['candidate']['availability'] = .98
        self.assertEqual(evaluate(o, ['stable', 'candidate'])[0], 'failed')
        o['candidate']['availability'] = .99
        o['candidate']['latency_fraction'] = .95
        self.assertEqual(evaluate(o, ['stable', 'candidate'])[0], 'passed')
        # At 100% old has zero routing; only candidate is evaluated, explicitly.
        del o['stable']
        self.assertEqual(evaluate(o, ['candidate'])[0], 'passed')
        self.assertEqual(evaluate(o, ['stable', 'candidate'])[0], 'inconclusive')

    def test_stale_wrong_revision_and_pause_invalidate_observation(self):
        for name, value in [('last_sample_at', 800), ('revision_hash', 'wrong'), ('image', 'wrong')]:
            o = observation()
            o['candidate'][name] = value
            self.assertEqual(evaluate(o, ['stable', 'candidate'])[0], 'inconclusive')
        self.assertEqual(evaluate(observation(window_end=800), ['candidate'])[0], 'inconclusive')
        r = step(step(release(), 'approve_git', approved=True), 'synced')
        r = step(step(r, 'metrics', verdict='passed'), 'pause')
        for event in ('resume', 'promote'):
            with self.assertRaises(Rejected):
                step(r, event)

    def test_valid_failure_is_not_hidden_by_missing_role_and_reason_survives(self):
        o = observation()
        o['stable']['availability'] = .5
        del o['candidate']
        verdict, reason = evaluate(o, ['stable', 'candidate'])
        self.assertEqual(verdict, 'failed')
        self.assertIn('stable-availability-below-99pct', reason)
        self.assertIn('candidate-coverage-missing', reason)
        r = step(step(release(), 'approve_git', approved=True), 'synced')
        r = step(r, 'metrics', verdict=verdict, reason=reason)
        self.assertEqual((r['state'], r['metrics_reason']), ('aborting', reason))
        self.assertEqual(step(r, 'stable_routed')['metrics_reason'], reason)

    def test_start_identity_catalog_concurrency_and_ui_staleness(self):
        images = ['example.invalid/account@sha256:' + c * 64 for c in 'ab']
        catalog = [{'image': im, 'source_commit': 'c' * 40, 'config_digest': 'sha256:' + 'd' * 64,
                    'platform': 'linux/amd64', 'verified': True, 'provenance': 'synthetic-test-only'} for im in images]
        current = {'state': 'idle', 'version': 5, 'stable_image': images[0], 'git_runtime_drift': False}
        request = {'target': TARGET, 'image': images[1], 'previous_image': images[0], 'strategy': 'Canary',
                   'mode': 'manual', 'expected_version': 5, 'idempotency_key': 'test-1'}
        self.assertEqual(validate_start(request, catalog, current)['state'], 'proposed')
        for name, value in [('target', {**TARGET, 'namespace': 'production'}), ('image', 'account:latest'),
                            ('previous_image', images[1]), ('strategy', 'BlueGreen'), ('mode', 'rolling'),
                            ('expected_version', 4), ('idempotency_key', '')]:
            bad = copy.deepcopy(request)
            bad[name] = value
            with self.subTest(name=name), self.assertRaises(Rejected):
                validate_start(bad, catalog, current)
        for state in ('proposed', 'canary10', 'restoring', 'aborted'):
            with self.assertRaises(Rejected):
                validate_start(request, catalog, {**current, 'state': state})
        with self.assertRaises(Rejected):
            validate_start(request, catalog, {**current, 'git_runtime_drift': True})
        with self.assertRaises(Rejected):
            validate_start(request, [], current)
        duplicate = copy.deepcopy(catalog)
        duplicate[1]['image'] = duplicate[0]['image'].replace('example.invalid', 'another.invalid')
        with self.assertRaises(Rejected):
            validate_catalog(duplicate)


if __name__ == '__main__':
    unittest.main()
