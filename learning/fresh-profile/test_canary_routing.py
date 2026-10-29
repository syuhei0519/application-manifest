"""Offline contract for fresh Account routing and Argo sync ownership."""
import copy
from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parent

class RoutingContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.objects = {x['kind']: x for x in yaml.safe_load_all((ROOT/'workload/account.yaml').read_text()) if x and x['kind'] in ('Rollout', 'VirtualService', 'DestinationRule')}
        rendered = subprocess.check_output(['helm', 'template', 'fresh-account-release-definition', str(ROOT/'release-definition'), '--set', 'workloadRevision='+'a'*40, '--set', 'accountRuntimeImage=ghcr.io/syuhei0519/backend-app@sha256:'+'b'*64], text=True)
        cls.app = yaml.safe_load(rendered)

    def test_rollout_uses_named_routes_and_pauses(self):
        canary = self.objects['Rollout']['spec']['strategy']['canary']
        self.assertEqual(canary['trafficRouting']['istio']['virtualService'], {'name':'account-api','routes':['primary']})
        self.assertEqual(canary['trafficRouting']['istio']['destinationRule'], {'name':'account-api','stableSubsetName':'stable','canarySubsetName':'canary'})
        steps = canary['steps']
        self.assertEqual([s['setWeight'] for s in steps if 'setWeight' in s], [10, 50, 100])
        self.assertEqual(len([s for s in steps if 'pause' in s]), 3)
        analyses = [s['analysis'] for s in steps if 'analysis' in s]
        self.assertEqual(len(analyses), 3)
        for stage, analysis in zip(('10', '50', '100'), analyses):
            self.assertEqual(analysis['templates'], [{'templateName': 'account-api-policy'}])
            self.assertEqual(analysis['args'][-1], {'name': 'stage', 'value': stage})

    def test_mode_selects_only_fixed_paths(self):
        base = ['helm', 'template', 'fresh-account-release-definition', str(ROOT/'release-definition'), '--set', 'workloadRevision='+'a'*40, '--set', 'accountRuntimeImage=ghcr.io/syuhei0519/backend-app@sha256:'+'b'*64]
        for mode, path in [('manual','learning/fresh-profile/workload'), ('auto','learning/fresh-profile/workload-auto')]:
            doc = yaml.safe_load(subprocess.check_output(base + ['--set', 'promotionMode='+mode], text=True))
            self.assertEqual(doc['spec']['source']['path'], path)
        bad = subprocess.run(base + ['--set', 'promotionMode=other'], capture_output=True)
        self.assertNotEqual(bad.returncode, 0)

    def test_sync_mode_defaults_to_automated_and_can_be_explicitly_paused(self):
        self.assertEqual(self.app["spec"]["syncPolicy"]["automated"], {"prune":False,"selfHeal":True})
        args = ['helm','template','fresh-account-release-definition',str(ROOT/'release-definition'),
                '--set','workloadRevision='+'a'*40,
                '--set','accountRuntimeImage=ghcr.io/syuhei0519/backend-app@sha256:'+'b'*64]
        paused = yaml.safe_load(subprocess.check_output(args+['--set','syncMode=paused'],text=True))
        self.assertNotIn('automated',paused['spec']['syncPolicy'])
        self.assertIn('Prune=false',paused['spec']['syncPolicy']['syncOptions'])
        upgrade = yaml.safe_load(subprocess.check_output(args+['--set','syncMode=paused','--set','workloadVariant=existing-upgrade'],text=True))
        self.assertEqual(upgrade['spec']['source']['path'], 'learning/fresh-profile/workload-existing')
        self.assertIn('ApplyOutOfSyncOnly=true',upgrade['spec']['syncPolicy']['syncOptions'])
        auto_upgrade = yaml.safe_load(subprocess.check_output(args+['--set','workloadVariant=existing-upgrade','--set','promotionMode=auto'],text=True))
        self.assertEqual(auto_upgrade['spec']['source']['path'], 'learning/fresh-profile/workload-existing-auto')
        invalid = subprocess.run(args+['--set','syncMode=unexpected'],capture_output=True)
        self.assertNotEqual(invalid.returncode,0)

    def test_route_and_subset_selectors(self):
        vs, dr = self.objects['VirtualService']['spec'], self.objects['DestinationRule']['spec']
        self.assertEqual(vs['http'][0]['name'], 'primary')
        routes = vs['http'][0]['route']
        self.assertEqual([(r['destination']['subset'], r['weight']) for r in routes], [('stable',100),('canary',0)])
        self.assertEqual([x['name'] for x in dr['subsets']], ['stable','canary'])
        for s in dr['subsets']:
            self.assertEqual(s['labels'], {'app.kubernetes.io/name':'account-api','rollouts-pod-template-hash':'unassigned'})
        self.assertEqual(vs['hosts'], [dr['host']])
        self.assertTrue(all(r['destination']['host'] == dr['host'] for r in routes))

    def test_sync_preserves_only_controller_owned_fields(self):
        spec = self.app['spec']
        self.assertIn('RespectIgnoreDifferences=true', spec['syncPolicy']['syncOptions'])
        ignores = {x['kind']:set(x['jsonPointers']) for x in spec['ignoreDifferences']}
        self.assertEqual(ignores, {'VirtualService':{'/spec/http/0/route/0/weight','/spec/http/0/route/1/weight'},'DestinationRule':{'/spec/subsets/0/labels/rollouts-pod-template-hash','/spec/subsets/1/labels/rollouts-pod-template-hash'}})
        # Model the documented RespectIgnoreDifferences pre-patch: fields in the
        # ignore set are carried from live into desired; unrelated drift is repaired.
        for kind, pointers in ignores.items():
            desired = copy.deepcopy(self.objects[kind]); live = copy.deepcopy(desired)
            for pointer in pointers:
                keys = pointer.lstrip('/').split('/'); node=live
                for key in keys[:-1]: node=node[int(key)] if isinstance(node,list) else node[key]
                node[keys[-1]] = 37 if keys[-1]=='weight' else 'candidatehash'
            live['spec']['host' if kind=='DestinationRule' else 'hosts'] = 'drift'
            merged = copy.deepcopy(desired)
            for pointer in pointers:
                keys=pointer.lstrip('/').split('/'); src=live; dst=merged
                for key in keys[:-1]:
                    src=src[int(key)] if isinstance(src,list) else src[key]; dst=dst[int(key)] if isinstance(dst,list) else dst[key]
                dst[keys[-1]]=src[keys[-1]]
            self.assertEqual(merged['spec']['host' if kind=='DestinationRule' else 'hosts'], desired['spec']['host' if kind=='DestinationRule' else 'hosts'])
            for pointer in pointers:
                node=merged
                for key in pointer.lstrip('/').split('/'): node=node[int(key)] if isinstance(node,list) else node[key]
                self.assertEqual(node, 37 if pointer.endswith('/weight') else 'candidatehash')

if __name__ == '__main__': unittest.main()
