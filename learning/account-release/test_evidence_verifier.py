import copy
import json
from pathlib import Path
import unittest
from contract import Rejected,TARGET
from evidence_verifier import verify,HOST,NS
from integration import image_revision
from test_control_adapter import FIXTURE,CATALOG
from test_integration import fixture
from deployment_target import CONTEXT, CONTROL_NAMESPACE, ROLLOUTS_NAMESPACE


def receipt():
    state=copy.deepcopy(FIXTURE);ro=state['rollout'];app=state['application']
    current_image=ro['spec']['template']['spec']['containers'][0]['image'];revision=ro['status']['currentPodHash']
    ro['status'].update(phase='Healthy',replicas=2,readyReplicas=2,updatedReplicas=2,availableReplicas=2,
                        stableRS=revision,currentStepIndex=6)
    app['status']['sync']['status']='Synced';app['status']['operationState'].update(phase='Succeeded',finishedAt='1970-01-01T00:15:50Z')
    app['status']['operationState']['syncResult']={'revision':app['status']['sync']['revision']}
    current=dict(rollout_uid=ro['metadata']['uid'],application_uid=app['metadata']['uid'],generation=ro['metadata']['generation'],git_revision=app['status']['sync']['revision'],
                 requested_image=current_image,current_image=current_image,revision=revision,strategy='Canary',promotion_mode='auto')
    rs={'metadata':{'uid':'rs-new','labels':{'rollouts-pod-template-hash':revision},
        'ownerReferences':[{'uid':current['rollout_uid'],'controller':True}]},'spec':{'replicas':2},'status':{'readyReplicas':2}}
    state['replicasets']={'items':[rs]}
    state['pods']={'items':[{'metadata':{'uid':f'pod-{i}','ownerReferences':[{'uid':'rs-new','controller':True}]},
        'status':{'conditions':[{'type':'Ready','status':'True'}],
        'containerStatuses':[{'name':'api','ready':True,'imageID':current_image}]}} for i in (1,2)]}
    state['virtualservice']={'metadata':{'name':'account-release','namespace':NS},'spec':{'hosts':[HOST],'gateways':['mesh'],
        'http':[{'name':'primary','route':[{'destination':{'host':HOST,'port':{'number':8080},'subset':name},'weight':weight}
        for name,weight in [('stable',100),('canary',0)]]}]}}
    state['destinationrule']={'metadata':{'name':'account-release','namespace':NS},'spec':{'host':HOST,
        'subsets':[{'name':'stable','labels':{'rollouts-pod-template-hash':revision}}]}}
    before=copy.deepcopy(state);after=copy.deepcopy(state);before['at']=999;after['at']=1110
    traffic=fixture(0);traffic['target']=dict(TARGET)
    other=next(e['image'] for e in CATALOG if e['image']!=current_image)
    old=traffic['images'];traffic['images']={'stable':current_image,'candidate':other}
    for key in ('before','after','before_timestamps','after_timestamps'):
        for item in traffic[key]['data']['result']:
            role=next(k for k,v in old.items() if image_revision(v)==item['metric']['destination_version'])
            item['metric']['destination_version']=image_revision(traffic['images'][role])
    return {'schema':'account-runtime-receipt-v1','context':CONTEXT,'kind':'completion',
            'before':before,'after':after,'traffic':traffic},current


class EvidenceTests(unittest.TestCase):
    def test_seed_phase_requires_both_subsets_bound_to_current_hash(self):
        data,current=receipt();current.update(strategy='Rolling',promotion_mode=None)
        for snapshot in (data['before'],data['after']):
            snapshot['rollout']['spec']['strategy']['canary']={'maxSurge':1,'maxUnavailable':0}
            snapshot['virtualservice']['spec']['http'][0]['route']=[{'destination':{'host':HOST,'port':{'number':8080}},'weight':100}]
            snapshot['destinationrule']['spec']['subsets']=[{'name':name,'labels':{'app':'account-release','rollouts-pod-template-hash':current['revision']}} for name in ('stable','canary')]
        phase=dict(name='seed-subsets',image=current['requested_image'],strategy='Rolling',promotion_mode=None,bootstrap_hash=current['revision'])
        self.assertTrue(verify(data,current,CATALOG,1111,phase=phase)['traffic_health_verified'])
        for side in ('before','after'):
            bad=copy.deepcopy(data)
            bad[side]['destinationrule']['metadata']['annotations']={'argo-rollouts.argoproj.io/managed-by-rollouts':'account-release'}
            with self.assertRaisesRegex(Rejected,'istio-detach-not-observed'):
                verify(bad,current,CATALOG,1111,phase=phase)
        for subsets in ([],[{'name':'stable','labels':{'app':'account-release','rollouts-pod-template-hash':current['revision']}}],
                        [{'name':name,'labels':{'app':'account-release','rollouts-pod-template-hash':'old-hash'}} for name in ('stable','canary')]):
            bad=copy.deepcopy(data);bad['after']['destinationrule']['spec']['subsets']=subsets
            with self.assertRaisesRegex(Rejected,'bootstrap-subsets'):verify(bad,current,CATALOG,1111,phase=phase)

    def test_rolling_detach_requires_empty_subsets_and_released_ownership(self):
        data,current=receipt();current.update(strategy='Rolling',promotion_mode=None)
        for snapshot in (data['before'],data['after']):
            snapshot['rollout']['spec']['strategy']['canary']={'maxSurge':1,'maxUnavailable':0}
            snapshot['virtualservice']['spec']['http'][0]['route']=[{'destination':{'host':HOST,'port':{'number':8080}},'weight':100}]
            snapshot['destinationrule']['spec']['subsets']=[]
        phase=dict(name='activate-mode',image=current['requested_image'],strategy='Rolling',promotion_mode=None,bootstrap_hash='')
        self.assertTrue(verify(data,current,CATALOG,1111,phase=phase)['traffic_health_verified'])
        for side in ('before','after'):
            bad=copy.deepcopy(data)
            bad[side]['destinationrule']['metadata']['annotations']={'argo-rollouts.argoproj.io/managed-by-rollouts':'account-release'}
            with self.assertRaisesRegex(Rejected,'istio-detach-not-observed'):
                verify(bad,current,CATALOG,1111,phase=phase)
        bad=copy.deepcopy(data)
        bad['after']['destinationrule']['spec']['subsets']=[{'name':'stable','labels':{'app':'account-release'}}]
        with self.assertRaisesRegex(Rejected,'rolling-subsets-mismatch'):
            verify(bad,current,CATALOG,1111,phase=phase)

    def test_measured_ready_git_runtime_traffic_receipt(self):
        data,current=receipt();result=verify(data,current,CATALOG,1111)
        self.assertTrue(result['traffic_health_verified']);self.assertFalse(result['resync_restart_verified'])
    def test_missing_mixed_stale_and_error_receipts_are_not_completion(self):
        mutations=[lambda d:d.update(context='kind-shared'),lambda d:d.update(context='kind-plat171-account'),lambda d:d['after'].update(at=1),
            lambda d:d['after']['rollout']['metadata'].update(uid='foreign'),
            lambda d:d['after']['application']['status']['sync'].update(status='OutOfSync'),
            lambda d:d['after']['pods']['items'][0]['status']['containerStatuses'][0].update(ready=False),
            lambda d:d['after']['pods']['items'][0]['metadata'].update(uid='changed'),
            lambda d:d['traffic']['client'].update(timeouts=1),
            lambda d:d['after']['virtualservice']['spec']['http'][0]['route'][0].update(weight=50)]
        for mutate in mutations:
            data,current=receipt();mutate(data)
            with self.subTest(mutate=mutate),self.assertRaises(Rejected):verify(data,current,CATALOG,1111)
    def test_restart_requires_new_pods_same_desired_and_post_sync_observation(self):
        data,current=receipt()
        data['pre_restart']={'rollout_uid':current['rollout_uid'],'git_revision':current['git_revision'],
            'image':current['requested_image'],'at':900,'pod_uids':['before-1','before-2']}
        with self.assertRaisesRegex(Rejected,'controller-restart-evidence'):verify(data,current,CATALOG,1111)
        data['controllers']={}
        for key,kind,name in [('rollouts','Deployment','learning-rollouts'),('argocd','StatefulSet','argocd-application-controller')]:
            pair={}
            for index,side in enumerate(('before','after')):
                owner=key+'-rs' if kind=='Deployment' else key
                pair[side]={'at':910+index*10,'workload':{'kind':kind,'metadata':{'uid':key,'name':name,'namespace':ROLLOUTS_NAMESPACE if key=='rollouts' else CONTROL_NAMESPACE,'generation':1},
                    'spec':{'replicas':1,'template':{'spec':{'containers':[{'name':key,'image':'fixed-controller'}]}}},
                    'status':{'observedGeneration':1,'readyReplicas':1}},
                    'replicasets':{'items':[{'metadata':{'uid':owner,'ownerReferences':[{'uid':key,'controller':True}]}}]},
                    'pods':{'items':[{'metadata':{'uid':key+'-'+side,'ownerReferences':[{'uid':owner,'controller':True}]},
                        'status':{'conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[{'ready':True}]}}]}}
            data['controllers'][key]=pair
        self.assertTrue(verify(data,current,CATALOG,1111)['resync_restart_verified'])
        bad=copy.deepcopy(data)
        bad['controllers']['argocd']['after']['pods']=bad['controllers']['argocd']['before']['pods']
        with self.assertRaisesRegex(Rejected,'controller-not-restarted'):verify(bad,current,CATALOG,1111)
        for field,value in [('pod_uids',['pod-1']),('git_revision','c'*40),('at',1000)]:
            bad=copy.deepcopy(data);bad['pre_restart'][field]=value
            with self.assertRaisesRegex(Rejected,'resync-restart'):verify(bad,current,CATALOG,1111)

if __name__=='__main__':unittest.main()
