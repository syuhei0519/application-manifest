import copy
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

from contract import Rejected, evaluate_metrics
from release_modes import desired_documents, runtime_status, switch_plan, validate_request
from test_integration import CATALOG, IMAGES
from deployment_target import CONTROL_NAMESPACE

ROOT = Path(__file__).resolve().parent


def current():
    return dict(state='succeeded', current_image=IMAGES['stable'], previous_image=IMAGES['candidate'],
                git_revision='c'*40, generation=3, version=10, git_runtime_drift=False,
                revision='abc123', strategy='Canary', promotion_mode='manual')


def request(strategy='Canary', mode='manual'):
    return dict(entity='component:default/account', environment='local', image=IMAGES['candidate'],
                strategy=strategy, promotion_mode=mode, expected_git_revision='c'*40,
                expected_generation=3, expected_version=10, request_id='request-001')


class ModesTests(unittest.TestCase):
    def test_git_profile_modes_are_authoritative(self):
        from runtime_support import runtime_documents
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);release=repo/'learning/account-release'
            shutil.copytree(ROOT/'chart',release/'chart')
            def git(*args): return subprocess.check_output(['git','-C',tmp,'-c','user.name=Fixture',
                '-c','user.email=fixture@example.invalid',*args],stderr=subprocess.PIPE,text=True).strip()
            git('init')
            for strategy,mode,seed in [('Rolling',None,'abc123'),('Canary','auto',''),('Canary','manual','abc123')]:
                profile=desired_documents('c'*40,CATALOG,IMAGES['candidate'],strategy,mode,seed,True)[1]['spec']['source']['helm']['valuesObject']
                (release/'runtime-profile.yaml').write_text(yaml.safe_dump(profile))
                git('add','.');git('commit','-m','fixture profile '+strategy+str(mode))
                sha=git('rev-parse','HEAD')
                (release/'runtime-profile.yaml').write_text('invalid: dirty-checkout')
                docs,_=runtime_documents(sha,CATALOG,IMAGES['candidate'],repo,os.environ.get('HELM','helm'))
                self.assertEqual(docs[1]['spec']['source']['helm'],dict(releaseName='account-release',valueFiles=['../runtime-profile.yaml']))
                self.assertEqual(docs[1]['spec']['source']['targetRevision'],sha)
                self.assertEqual(docs[1]['spec']['ignoreDifferences']==[],strategy=='Rolling')
                self.assertTrue(any(r['kind']=='AnalysisTemplate' for r in docs[0]['spec']['namespaceResourceWhitelist']))

    def test_input_matrix_and_stale_or_busy(self):
        for strategy, mode, valid in [('Canary','manual',True),('Canary','auto',True),('Rolling',None,True),
                                     ('Rolling','auto',False),('Rolling','manual',False),('Canary',None,False)]:
            r = request(strategy, mode)
            if valid:
                self.assertEqual(validate_request(r, CATALOG, current()), r)
            else:
                with self.assertRaises(Rejected): validate_request(r, CATALOG, current())
        for field, value in [('expected_generation',2),('expected_version',9),('expected_git_revision','d'*40),
                             ('image','account:latest'),('environment','production'),('entity','component:default/order'),
                             ('pause',True),('expected_generation',True),('promotion_mode','bogus')]:
            with self.subTest(field=field), self.assertRaises(Rejected):
                validate_request(dict(request(), **{field:value}), CATALOG, current())
        for state in ['proposed','preparing','syncing','running','aborting','aborted','restoring','recovering','failed','unknown']:
            with self.subTest(state=state), self.assertRaises(Rejected):
                validate_request(request('Rolling',None), CATALOG, dict(current(),state=state))

    def test_terminal_switch_retains_identity_and_orders_preparation(self):
        c = current()
        plan = switch_plan(request('Rolling',None), CATALOG,c)
        self.assertEqual([p['name'] for p in plan], ['activate-mode','start-image'])
        self.assertEqual(plan[0]['image'],c['current_image'])
        self.assertEqual(plan[0]['bootstrap_hash'],'')
        # The full Rolling route is installed before DR subsets are removed.
        # A delayed detach cleanup therefore converges to the same empty set.
        self.assertEqual(plan[1]['bootstrap_hash'],'')
        self.assertEqual(c,current())
        c.update(strategy='Rolling',promotion_mode=None)
        plan = switch_plan(request(),CATALOG,c)
        self.assertEqual([p['name'] for p in plan],['seed-subsets','activate-mode','start-image'])
        self.assertEqual(plan[0]['bootstrap_hash'],'abc123')
        self.assertEqual(plan[0]['strategy'],'Rolling')
        self.assertEqual(plan[1]['image'],c['current_image'])
        with self.assertRaises(Rejected): switch_plan(request(),CATALOG,dict(c,revision='unassigned!'))

    def test_render_rolling_removes_canary_route_and_ignore_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            for strategy, mode, seed in [('Canary','manual',''),('Rolling',None,''),('Rolling',None,'abc123')]:
                _,app=desired_documents('c'*40,CATALOG,IMAGES['candidate'],strategy,mode,seed)
                p=Path(tmp)/'values.json'
                p.write_text(json.dumps(app['spec']['source']['helm']['valuesObject']))
                render=subprocess.check_output([os.environ.get('HELM','helm'),'template','account-release',
                    str(ROOT/'chart'),'-n','learning-account-release','-f',str(p)],text=True)
                docs={d['kind']:d for d in yaml.safe_load_all(render)}
                canary=docs['Rollout']['spec']['strategy']['canary']
                # v1.10.1 removes false paused; Kubernetes defaults port TCP.
                # Spell the stable serialized form to avoid permanent OutOfSync.
                self.assertNotIn('paused',docs['Rollout']['spec'])
                self.assertEqual(docs['Rollout']['spec']['template']['spec']['containers'][0]['ports'][0]['protocol'],'TCP')
                route=docs['VirtualService']['spec']['http'][0]['route']
                if strategy=='Rolling':
                    self.assertEqual(set(canary),{'maxSurge','maxUnavailable'})
                    self.assertEqual(app['spec']['ignoreDifferences'],[])
                    self.assertEqual(len(route),1)
                    self.assertNotIn('subset',route[0]['destination'])
                    if seed:
                        self.assertEqual({s['labels']['rollouts-pod-template-hash'] for s in
                                          docs['DestinationRule']['spec']['subsets']},{seed})
                    else: self.assertEqual(docs['DestinationRule']['spec']['subsets'],[])
                else: self.assertEqual([s['setWeight'] for s in canary['steps'] if 'setWeight' in s],[10,50,100])

    def test_rolling_runtime_is_not_metric_verdict(self):
        r=dict(metadata=dict(name='account-release',namespace='learning-account-release',uid='fixture-uid',generation=3,resourceVersion='100'), spec=dict(replicas=2,strategy=dict(canary={}),
               template=dict(spec=dict(containers=[dict(name='api',image=IMAGES['candidate'])]))),
               status=dict(observedGeneration='3',currentPodHash='abc123',stableRS='abc123',replicas=2,
                           updatedReplicas=2,readyReplicas=2,availableReplicas=2,phase='Healthy',
                           conditions=[dict(type='Available',status='True')]))
        def observe(obj): return runtime_status(obj,'Rolling',None,expected_uid='fixture-uid')
        self.assertEqual(observe(r)['state'],'succeeded')
        for key,value in [('name','unrelated'),('namespace','default'),('uid','recreated')]:
            bad=copy.deepcopy(r);bad['metadata'][key]=value
            self.assertEqual(observe(bad)['state'],'unavailable')
        for key,value in [('observedGeneration','2'),('observedGeneration',3),('readyReplicas',1),('availableReplicas',1),('replicas',3),
                          ('stableRS','oldhash'),('pauseConditions',[dict(reason='x')])]:
            bad=copy.deepcopy(r);bad['status'][key]=value
            self.assertNotEqual(observe(bad)['state'],'succeeded')
        r['status']['conditions']=[dict(type='Progressing',status='False',reason='ProgressDeadlineExceeded')]
        self.assertEqual(observe(r)['state'],'recovering')
        self.assertEqual(observe(r)['reason'],'runtime-progress-deadline-exceeded')
        self.assertEqual(observe(r)['actions'],[])
        r['status']['abort']=True
        self.assertEqual(observe(r)['state'],'recovering')
        self.assertEqual(observe(r)['reason'],'runtime-aborted')
        self.assertEqual(observe(r)['actions'],[])

    def test_mode_sync_orders_detach_and_retains_unreferenced_policy(self):
        # The live controller removes DR hash selectors while detaching Istio.
        # An unpruned, no-longer-desired Template also keeps ArgoCD OutOfSync.
        with tempfile.TemporaryDirectory() as tmp:
            for strategy,mode,seed in [('Rolling',None,'abc123'),('Rolling',None,''),
                                       ('Canary','manual',''),('Canary','auto','abc123')]:
                _,app=desired_documents('c'*40,CATALOG,IMAGES['candidate'],strategy,mode,seed,True)
                values=Path(tmp)/'values.json'
                values.write_text(json.dumps(app['spec']['source']['helm']['valuesObject']))
                rendered=subprocess.check_output([os.environ.get('HELM','helm'),'template','account-release',
                    str(ROOT/'chart'),'-n','learning-account-release','-f',str(values)],text=True)
                docs={d['kind']:d for d in yaml.safe_load_all(rendered)}
                self.assertIn('AnalysisTemplate',docs)
                wave=lambda kind:int(docs[kind]['metadata'].get('annotations',{}).get('argocd.argoproj.io/sync-wave','0'))
                rollout=docs['Rollout']['spec']['strategy']['canary']
                if strategy=='Rolling':
                    self.assertLess(wave('VirtualService'),wave('Rollout'))
                    self.assertLess(wave('Rollout'),wave('DestinationRule'))
                    self.assertFalse(any(rollout.get(k) for k in ('steps','analysis','trafficRouting')))
                else:
                    self.assertLess(wave('DestinationRule'),wave('Rollout'))
                    self.assertTrue(any('analysis' in step for step in rollout['steps']))

    def test_auto_requires_controller_analysis_and_no_manual_pause(self):
        with self.assertRaises(Rejected): desired_documents('c'*40,CATALOG,IMAGES['candidate'],'Canary','auto')
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ['manual','auto']:
                docs=desired_documents('c'*40,CATALOG,IMAGES['candidate'],'Canary',mode,analysis_enabled=True)
                path=Path(tmp)/'v.json';path.write_text(json.dumps(docs[1]['spec']['source']['helm']['valuesObject']))
                rendered=subprocess.check_output([os.environ.get('HELM','helm'),'template','account-release',str(ROOT/'chart'),'-n','learning-account-release','-f',str(path)],text=True)
                objects={d['kind']:d for d in yaml.safe_load_all(rendered)}
                steps=objects['Rollout']['spec']['strategy']['canary']['steps']
                self.assertEqual(sum('analysis' in s for s in steps),3)
                self.assertEqual(sum('pause' in s for s in steps),3 if mode=='manual' else 0)
                metric=objects['AnalysisTemplate']['spec']['metrics'][0]
                self.assertEqual([metric[k] for k in ['count','failureLimit','inconclusiveLimit','consecutiveErrorLimit']],[1,0,0,0])
                self.assertEqual(metric['initialDelay'],'300s')

    def test_plugin_query_templates_match_shared_sdk(self):
        from sdk_queries import policy_query_templates
        self.assertEqual(json.loads((ROOT/'metric-plugin/queries.json').read_text()),policy_query_templates())

    def test_shared_policy_cases(self):
        identity=dict(revision_hash='bbbbb',image=IMAGES['candidate'])
        for case in json.loads((ROOT/'policy-cases.json').read_text()):
            observation=dict(window_seconds=300,query_status='success',window_end=1000,window_start=700,
                candidate=dict(identity,coverage_complete=True,last_sample_at=990,requests=case['requests'],
                    availability=case['availability'],latency_fraction=case['latency']))
            with self.subTest(case=case['name']):
                self.assertEqual(evaluate_metrics(observation,{'candidate':identity},1000)[0],case['expected'])

    def test_analysis_overlay_is_explicit_and_scoped(self):
        from analysis_proposal import analysis_overlay, BASE_IMAGE
        ns='learning-account-release'
        def obj(kind,name,**fields): return dict(apiVersion='fixture-only',kind=kind,metadata=dict(name=name,namespace=ns),**fields)
        role=lambda name:obj('Role',name,rules=[])
        source={'target.yaml':[role('learning-rollouts'),role('argocd-learning'),
            obj('Deployment','learning-rollouts',spec=dict(template=dict(spec=dict(serviceAccountName='learning-rollouts',
                containers=[dict(image=BASE_IMAGE,securityContext=dict(readOnlyRootFilesystem=True))],
                volumes=[dict(name='plugin-bin',emptyDir={})]))))],
            'control.yaml':[dict(apiVersion='v1',kind='ConfigMap',metadata=dict(name='argocd-cm',namespace=CONTROL_NAMESPACE),
                data={'resource.inclusions':'[]'})]}
        original=copy.deepcopy(source)
        result=analysis_overlay(source,'learning.local/account-rollouts@sha256:'+'a'*64,'b'*64)
        self.assertEqual(source,original)
        roles={o['metadata']['name']:o for o in result['target.yaml'] if o['kind']=='Role'}
        self.assertEqual({r for rule in roles['learning-rollouts']['rules'] for r in rule['resources']},
                         {'analysisruns','analysisruns/status','analysisruns/finalizers'})
        self.assertEqual(roles['argocd-learning']['rules'][0]['resources'],['analysistemplates'])
        self.assertTrue(all(o['kind']!='Secret' for docs in result.values() for o in docs))
        with self.assertRaises(Rejected): analysis_overlay(source,'controller:latest','b'*64)
        with self.assertRaises(Rejected): analysis_overlay(source,'learning.local/account-rollouts@sha256:'+'a'*64,'bad')
        with self.assertRaises(Rejected): analysis_overlay(result,'learning.local/account-rollouts@sha256:'+'a'*64,'b'*64)


if __name__ == '__main__': unittest.main()
