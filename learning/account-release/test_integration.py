"""Offline fixtures; example.invalid digests are never runtime acceptance."""
import copy
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml
import jsonschema

from contract import TARGET, Rejected
from integration import application, image_revision, sdk_query_set, verify_traffic
from runtime_support import runtime_documents

ROOT = Path(__file__).resolve().parent
IMAGES = {'stable': 'example.invalid/account@sha256:' + 'a' * 64,
          'candidate': 'example.invalid/account@sha256:' + 'b' * 64}
CATALOG = [dict(image=v, source_commit=c * 40, config_digest='sha256:' + c * 64,
                platform='linux/amd64', verified=True, provenance='FIXTURE ONLY')
           for v, c in zip(IMAGES.values(), ['a', 'b'])]


def fixture(weight=10):
    n = 1000 if weight in (10, 50) else 200
    def snapshot(timestamp, increments):
        return {'status': 'success', 'data': {'resultType': 'vector', 'result': [
            {'metric': {'__name__': 'istio_requests_total', 'job': 'account-release-envoy',
                        'reporter': 'destination', 'source_workload': 'account-release-client',
                        'source_workload_namespace': TARGET['namespace'], 'destination_service_name': 'account-release',
                        'destination_workload_namespace': TARGET['namespace'], 'destination_version': image_revision(image),
                        'response_code': '200', 'instance': role + ':15090'},
             'value': [timestamp, str(100 + increments[role])]}
            for role, image in IMAGES.items()]}}
    def timestamps(t):
        data = snapshot(t, dict(stable=0, candidate=0))
        for item in data['data']['result']:
            del item['metric']['__name__']
            item['value'] = [t, str(t-5)]
        return data
    return dict(target=TARGET, weight=weight, observed_weights={'stable': 100-weight, 'candidate': weight},
                images=IMAGES, start=1000, end=1100,
                client_window=dict(start=1002, end=1090),
                client=dict(sent=n, ok=n, errors=0, timeouts=0, retries=0, warmup_excluded=True),
                before_timestamps=timestamps(1000), after_timestamps=timestamps(1100),
                before=snapshot(1000, dict(stable=0, candidate=0)),
                after=snapshot(1100, dict(stable=n*(100-weight)//100, candidate=n*weight//100)))


class IntegrationTests(unittest.TestCase):
    def test_repository_profile_matches_reviewed_catalog(self):
        profile = yaml.safe_load((ROOT/'runtime-profile.yaml').read_text())
        catalog = json.loads((ROOT/'image-catalog.reviewed.json').read_text())
        repo = ROOT.parents[1]
        commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        tracked = subprocess.check_output(['git', '-C', str(repo), 'show',
            commit+':learning/account-release/runtime-profile.yaml'], text=True)
        self.assertEqual(profile, yaml.safe_load(tracked))
        # The runtime validator checks the catalog binding and both legacy and
        # explicit mode profiles, then renders the exact committed chart.
        docs, job = runtime_documents(commit, catalog, profile['image'], repo, os.environ.get('HELM', 'helm'))
        self.assertEqual(docs[1]['spec']['source']['targetRevision'], commit)
        self.assertEqual(job['spec']['template']['spec']['containers'][0]['image'], profile['image'])
        jsonschema.validate(profile, json.loads((ROOT/'chart/values.schema.json').read_text()))

    def test_git_profile_is_authoritative(self):
        profile = application('c'*40, CATALOG, IMAGES['stable'])[1]['spec']['source']['helm']['valuesObject']
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            release = repo/'learning/account-release'
            shutil.copytree(ROOT/'chart', release/'chart')
            tracked = release/'runtime-profile.yaml'
            tracked.write_text(yaml.safe_dump(profile))
            def git(*args):
                return subprocess.check_output(['git', '-C', tmp, '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', *args], stderr=subprocess.PIPE).decode().strip()
            git('init')
            git('add', '.')
            git('commit', '-m', 'stable profile')
            stable = git('rev-parse', 'HEAD')
            tracked.write_text(yaml.safe_dump(application('c'*40, CATALOG, IMAGES['candidate'])[1]['spec']['source']['helm']['valuesObject']))
            git('add', '.')
            git('commit', '-m', 'candidate profile')
            candidate = git('rev-parse', 'HEAD')
            # Dirty checkout cannot alter the migration or Application source.
            tracked.write_text('invalid: checkout')
            (release/'chart/templates/release.yaml').write_text('invalid: checkout')
            for sha, image in [(stable, IMAGES['stable']), (candidate, IMAGES['candidate'])]:
                docs, job = runtime_documents(sha, CATALOG, image, repo, os.environ.get('HELM', 'helm'))
                self.assertEqual(docs[1]['spec']['source']['targetRevision'], sha)
                self.assertEqual(docs[1]['spec']['source']['helm'],
                    {'releaseName': 'account-release', 'valueFiles': ['../runtime-profile.yaml']})
                self.assertEqual(job['spec']['template']['spec']['containers'][0]['image'], image)
            for sha, image in [(stable, IMAGES['candidate']), (candidate, IMAGES['stable']),
                               ('f'*40, IMAGES['stable']), ('main', IMAGES['stable'])]:
                with self.assertRaises(Rejected):
                    runtime_documents(sha, CATALOG, image, repo, os.environ.get('HELM', 'helm'))

    def test_application_bounded_ownership(self):
        project, app = application('c'*40, CATALOG, IMAGES['candidate'])
        self.assertEqual(project['spec']['clusterResourceWhitelist'], [])
        self.assertEqual(app['spec']['source']['targetRevision'], 'c'*40)
        self.assertEqual(app['spec']['syncPolicy'], {'syncOptions': ['RespectIgnoreDifferences=true']})
        self.assertEqual(sum(len(x['jsonPointers']) for x in app['spec']['ignoreDifferences']), 4)
        self.assertNotIn('finalizers', app['metadata'])
        for revision in ['main', '', 'a'*39]:
            with self.assertRaises(Rejected):
                application(revision, CATALOG, IMAGES['candidate'])
        bad = copy.deepcopy(CATALOG)
        bad[1]['image'] = 'example.invalid/account@sha256:' + 'a'*56 + 'b'*8
        with self.assertRaises(Rejected):
            application('c'*40, bad, bad[1]['image'])

    def test_render_schema_and_target_guards(self):
        values = application('c'*40, CATALOG, IMAGES['candidate'])[1]['spec']['source']['helm']['valuesObject']
        schema = json.loads((ROOT/'chart/values.schema.json').read_text())
        jsonschema.validate(values, schema)
        helm = os.environ.get('HELM', 'helm')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'values.json'
            def render(data, ns=TARGET['namespace']):
                path.write_text(json.dumps(data))
                return subprocess.run([helm, 'template', 'account-release', str(ROOT/'chart'), '-n', ns,
                                       '-f', str(path)], capture_output=True, text=True)
            r = render(values)
            self.assertEqual(r.returncode, 0, r.stderr)
            docs = list(yaml.safe_load_all(r.stdout))
            self.assertEqual(sorted(d['kind'] for d in docs), ['DestinationRule', 'Rollout', 'Service', 'Service', 'VirtualService'])
            self.assertTrue(all(d['metadata']['namespace'] == TARGET['namespace'] for d in docs))
            rollout = next(d for d in docs if d['kind'] == 'Rollout')
            self.assertEqual([s['setWeight'] for s in rollout['spec']['strategy']['canary']['steps'] if 'setWeight' in s], [10, 50, 100])
            self.assertEqual(rollout['spec']['template']['spec']['containers'][0]['image'], IMAGES['candidate'])
            self.assertNotEqual(render(values, 'default').returncode, 0)
            for key, value in [('owner', 'shared'), ('image', 'account:latest'), ('sourceCommit', 'main'),
                               ('imageRevision', image_revision(IMAGES['stable']))]:
                bad = dict(values, **{key: value})
                self.assertNotEqual(render(bad).returncode, 0, key)
            self.assertNotEqual(render({}).returncode, 0)

    def test_dynamic_sdk_queries(self):
        result = sdk_query_set('aaaaa', 'bbbbb', 2, 1)
        self.assertEqual(len(result), 6)
        self.assertNotIn('{{args.', str(result))
        self.assertIn('account_namespace="learning-account-release"', result['candidate-requests'])
        self.assertIn('== 2)', result['stable-requests'])
        self.assertIn('== 1)', result['candidate-requests'])
        self.assertEqual(set(sdk_query_set('aaaaa', 'bbbbb', 0, 2)),
                         {'candidate-requests', 'candidate-availability', 'candidate-latency'})
        for stable, candidate in [(True, 1), (-1, 1), (1, 0), (1, 11)]:
            with self.assertRaises(ValueError):
                sdk_query_set('aaaaa', 'bbbbb', stable, candidate)
        with self.assertRaises(Rejected):
            sdk_query_set('aaaaa', 'aaaaa', 1, 1)

    def test_actual_traffic_all_stages(self):
        for weight in (0, 10, 50, 100):
            result = verify_traffic(fixture(weight))
            self.assertEqual(result['candidate'], (1000 if weight in (10, 50) else 200)*weight//100)

    def test_traffic_rejects_missing_wrong_scope_and_errors(self):
        mutations = [
            lambda e: e.update(target=dict(TARGET, context='kind-shared')),
            lambda e: e.update(observed_weights={'stable': 50, 'candidate': 50}),
            lambda e: e['client'].update(timeouts=1),
            lambda e: e['client'].update(warmup_excluded=False),
            lambda e: e['after'].update(status='error'),
            lambda e: e['after'].update(warnings=['partial']),
            lambda e: e['after']['data']['result'].pop(),
            lambda e: e['after']['data']['result'][0]['metric'].update(destination_version='unknown'),
            lambda e: e['after']['data']['result'][0]['metric'].update(response_code='500'),
            lambda e: e['after']['data']['result'][0]['metric'].update(source_workload='another-client'),
            lambda e: e['after']['data']['result'][0].update(value=[900, '1000']),
            lambda e: e['after']['data']['result'][0].update(value=[1100, 'NaN']),
            lambda e: e['after']['data']['result'][0].update(value=[1100, '1']),
            lambda e: e['after']['data']['result'][0].update(value=[1100, '1001']),
        ]
        for mutation in mutations:
            e = copy.deepcopy(fixture())
            mutation(e)
            with self.subTest(mutation=mutation), self.assertRaises((Rejected, ValueError)):
                verify_traffic(e)

    def test_traffic_band_and_new_series(self):
        e = fixture()
        e['after']['data']['result'][0]['value'][1] = '900'
        e['after']['data']['result'][1]['value'][1] = '300'
        with self.assertRaisesRegex(Rejected, 'outside-contract'):
            verify_traffic(e)
        e = fixture()
        e['after']['data']['result'].append(copy.deepcopy(e['after']['data']['result'][0]))
        with self.assertRaisesRegex(Rejected, 'duplicate'):
            verify_traffic(e)

    def test_recovery_uses_window_deltas_without_hiding_error_series(self):
        for code in ('0', '201', '400', '500'):
            e = fixture(0)
            for name in ('before', 'after', 'before_timestamps', 'after_timestamps'):
                item = copy.deepcopy(e[name]['data']['result'][0])
                item['metric']['response_code'] = code
                if 'timestamps' not in name:
                    item['value'][1] = '17'
                e[name]['data']['result'].append(item)
            with self.subTest(code=code):
                self.assertEqual(verify_traffic(e)['stable'], 200)
                bad = copy.deepcopy(e)
                bad['after']['data']['result'][-1]['value'][1] = '18'
                with self.assertRaisesRegex(Rejected, 'non-200-response-in-window'):
                    verify_traffic(bad)
                bad = copy.deepcopy(e)
                bad['after']['data']['result'][-1]['value'][1] = '0'
                with self.assertRaisesRegex(Rejected, 'counter-reset'):
                    verify_traffic(bad)
                bad = copy.deepcopy(e)
                bad['after']['data']['result'].pop()
                with self.assertRaises(Rejected):
                    verify_traffic(bad)
                bad = copy.deepcopy(e)
                bad['after_timestamps']['data']['result'][-1]['value'][1] = '860'
                with self.assertRaisesRegex(Rejected, 'stale-traffic-sample'):
                    verify_traffic(bad)

    def test_real_prometheus_lookback_timestamp_is_not_freshness(self):
        # Counter query gets current evaluation time even if the raw sample is 4 minutes old.
        e = fixture()
        e['after_timestamps']['data']['result'][0]['value'] = [1100, '860']
        with self.assertRaisesRegex(Rejected, 'stale-traffic-sample'):
            verify_traffic(e)
        e = fixture()
        e['after_timestamps']['data']['result'][0]['value'] = [1100, '1080']
        with self.assertRaisesRegex(Rejected, 'client-window'):
            verify_traffic(e)
        e = fixture()
        e['after_timestamps']['data']['result'].pop()
        with self.assertRaisesRegex(Rejected, 'missing-sample-timestamp'):
            verify_traffic(e)


if __name__ == '__main__':
    unittest.main()
