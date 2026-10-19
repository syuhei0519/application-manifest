"""Synthetic field boundary and active route negatives; not runtime acceptance."""
import copy
import importlib.util
from pathlib import Path
import unittest
import http.server
import urllib.request
import urllib.error
from unittest.mock import patch
import threading

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/(name+'.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


app = load('rollout_application')
observe = load('observe_rollout')


class Ownership(unittest.TestCase):
    def test_only_runtime_fields_ignored(self):
        _, obj = app.application('learning-core-test','a'*40,'v2')
        spec = obj['spec']
        self.assertEqual(spec['syncPolicy'], {'syncOptions':['RespectIgnoreDifferences=true']})
        self.assertNotIn('finalizers',obj['metadata'])
        self.assertEqual([(r['kind'],r['name'],r['namespace']) for r in spec['ignoreDifferences']],
                         [('VirtualService','mesh-rollout',app.NS),('DestinationRule','mesh-rollout',app.NS)])
        self.assertEqual(spec['ignoreDifferences'][0]['jsonPointers'],
                         ['/spec/http/0/route/0/weight','/spec/http/0/route/1/weight'])
        self.assertEqual(spec['ignoreDifferences'][1]['jsonPointers'],
                         ['/spec/subsets/0/labels/rollouts-pod-template-hash','/spec/subsets/1/labels/rollouts-pod-template-hash'])

    def test_reject_unbounded_application(self):
        for args in [('default','a'*40,'v1'),(app.NS,'a'*40,'v1'),('learning-core','main','v1'),('learning-core','a'*40,'other')]:
            with self.assertRaises(ValueError):
                app.application(*args)


class Observation(unittest.TestCase):
    def fixture(self, stage):
        weight,index=observe.STAGES[stage]
        stable='old'
        latest='old' if stage in ['initial','promoted'] else 'new'
        meta=dict(name='mesh-rollout',namespace=app.NS)
        ro=dict(kind='Rollout',metadata=dict(meta,generation=3),status=dict(observedGeneration="3",stableRS=stable,currentPodHash=latest,phase='Healthy'))
        if index is not None:
            ro['status'].update(currentStepIndex=index,pauseConditions=[{'reason':'CanaryPauseStep'}])
        if stage=='aborted': ro['status']['abort']=True
        dr=dict(kind='DestinationRule',metadata=meta,spec=dict(host=observe.HOST,exportTo=['.'],subsets=[dict(name=s,labels={'app':'mesh-rollout','rollouts-pod-template-hash':h}) for s,h in [('stable',stable),('canary',latest)]]))
        vs=dict(kind='VirtualService',metadata=meta,spec=dict(hosts=[observe.HOST],exportTo=['.'],gateways=['mesh'],http=[dict(name='demo-only',retries={'attempts':0},match=[{'method':{'exact':'GET'},'uri':{'exact':'/version'}}],route=[dict(destination=dict(host=observe.HOST,subset=s,port=dict(number=8080)),weight=w) for s,w in [('stable',100-weight),('canary',weight)]])]))
        clusters=[dict(name=f'outbound|8080|{s}|{observe.HOST}',weight=w) for s,w in [('stable',100-weight),('canary',weight)] if w]
        routes=[dict(virtualHosts=[dict(domains=[observe.HOST],routes=[dict(match={'path':'/version','headers':[{'name':':method','stringMatch':{'exact':'GET'}}]},route=dict(weightedClusters=dict(clusters=clusters)))])])]
        return [ro,vs,dr,routes,stage]

    def test_stages(self):
        for stage in observe.STAGES:
            self.assertTrue(observe.check(*self.fixture(stage))['passed'])

    def test_reject_false_evidence(self):
        for mutation in ['stale','step','hash','weight','envoy','no-route','wrong-method','abort','subset-policy','route-extra']:
            f=self.fixture('ten')
            if mutation=='stale': f[0]['status']['observedGeneration']='2'
            if mutation=='step': f[0]['status']['currentStepIndex']=3
            if mutation=='hash': f[2]['spec']['subsets'][0]['labels']['rollouts-pod-template-hash']='other'
            if mutation=='weight': f[1]['spec']['http'][0]['route'][0]['weight']=100
            route=f[3][0]['virtualHosts'][0]['routes'][0]
            if mutation=='envoy': route['route']['weightedClusters']['clusters'][0]['weight']=100
            if mutation=='no-route': f[3]=[]
            if mutation=='wrong-method': route['match']['headers']=[]
            if mutation=='subset-policy': f[2]['spec']['subsets'][0]['trafficPolicy']={'loadBalancer':{'simple':'RANDOM'}}
            if mutation=='route-extra': f[1]['spec']['http'][0]['timeout']='5s'
            if mutation=='abort': f[0]['status']['abort']=True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                observe.check(*f)

    def test_reject_shadowing_first_route(self):
        for match in [{'prefix':'/'}, {'prefix':'/version'}, {'path':'/version'},
                      {'safeRegex':{'regex':'.*'}}]:
            f=self.fixture('ten')
            first={'match':match,'route':{'cluster':f'outbound|8080|stable|{observe.HOST}'}}
            f[3][0]['virtualHosts'][0]['routes'].insert(0,first)
            with self.subTest(match=match), self.assertRaises(ValueError):
                observe.check(*f)

    def test_unrelated_earlier_route(self):
        for match in [{'prefix':'/other'}, {'path':'/elsewhere'},
                      {'path':'/version','headers':[{'name':':method','exactMatch':'POST'}]}]:
            f=self.fixture('ten')
            f[3][0]['virtualHosts'][0]['routes'].insert(0,{'match':match,'route':{'cluster':'other'}})
            self.assertTrue(observe.check(*f)['passed'])


class FailedFixture(unittest.TestCase):
    def test_livez_survives_failed_readiness(self):
        demo_path=ROOT/'chart/files/demo.py'
        spec=importlib.util.spec_from_file_location('failed_demo',demo_path)
        demo=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        original=http.server.ThreadingHTTPServer
        created=[]
        def server_factory(address,handler):
            server=original(('127.0.0.1',0),handler)
            created.append(server)
            return server
        ready=threading.Event()
        def make_server(address,handler):
            server=server_factory(address,handler)
            ready.set()
            return server
        with patch.object(demo.http.server,'ThreadingHTTPServer',side_effect=make_server):
            worker=threading.Thread(target=demo.serve,args=('failed',),daemon=True)
            worker.start()
            self.assertTrue(ready.wait(3))
            server=created[0]
            try:
                base='http://127.0.0.1:'+str(server.server_port)
                with urllib.request.urlopen(base+'/livez',timeout=2) as response:
                    self.assertEqual(response.status,200)
                with self.assertRaises(urllib.error.HTTPError) as failed:
                    urllib.request.urlopen(base+'/readyz',timeout=2)
                self.assertEqual(failed.exception.code,503)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(3)
