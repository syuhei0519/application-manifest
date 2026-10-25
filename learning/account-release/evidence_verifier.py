# 観測結果の由来と測定条件を検証し、リリース判定に使える証拠かを確かめます。
"""Offline verifier for server-owned measured receipts; never performs IO.

The UI cannot write these receipts. A session observer captures the fixed target,
HTTP client results, Envoy counter windows and object snapshots. Booleans such as
`healthy: true` are not evidence. All checks below are repeated before unlocking.
"""
import datetime
import hashlib
import json

from contract import TARGET, Rejected, require, validate_catalog
from integration import verify_traffic
from release_modes import runtime_status
from deployment_target import CONTEXT, CONTROL_NAMESPACE, ROLLOUTS_NAMESPACE, GIT_BRANCH

NS='learning-account-release'
HOST='account-release.learning-account-release.svc.cluster.local'


def verify_controller_restarts(receipt,earliest,latest):
    controllers=receipt.get('controllers',{})
    require(set(controllers)=={'rollouts','argocd'},'controller-restart-evidence-required')
    for key,kind,name,namespace in [('rollouts','Deployment','learning-rollouts',ROLLOUTS_NAMESPACE),
                          ('argocd','StatefulSet','argocd-application-controller',CONTROL_NAMESPACE)]:
        pair=controllers[key];before,after=pair['before'],pair['after']
        require(earliest<=before['at']<after['at']<=latest,'controller-restart-window-invalid')
        ids=[];identity=None
        for snapshot in (before,after):
            workload=snapshot['workload'];meta=workload['metadata'];status=workload['status']
            require(workload['kind']==kind and meta['name']==name and meta['namespace']==namespace,
                    'controller-restart-target-mismatch')
            current=(meta['uid'],workload['spec']['template']['spec']['containers'])
            if identity is None:identity=current
            require(current==identity and status['observedGeneration']==meta['generation'] and
                    status['readyReplicas']==workload['spec']['replicas']>0,'controller-not-ready-or-replaced')
            owners={meta['uid']}
            if kind=='Deployment':
                owners={rs['metadata']['uid'] for rs in snapshot['replicasets']['items'] if any(
                    o.get('uid')==meta['uid'] and o.get('controller') is True for o in rs['metadata'].get('ownerReferences',[]))}
            pods=[pod for pod in snapshot['pods']['items'] if not pod['metadata'].get('deletionTimestamp') and any(
                o.get('uid') in owners and o.get('controller') is True for o in pod['metadata'].get('ownerReferences',[]))]
            require(len(pods)==workload['spec']['replicas'] and all(any(
                c['type']=='Ready' and c['status']=='True' for c in p['status']['conditions']) and
                all(c['ready'] is True for c in p['status']['containerStatuses']) for p in pods),
                'controller-pods-not-ready')
            ids.append({pod['metadata']['uid'] for pod in pods})
        require(not ids[0]&ids[1],'controller-not-restarted')


def digest(receipt):
    return hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def verify(receipt,current,catalog,now,*,phase=None):
    require(receipt.get('schema')=='account-runtime-receipt-v1' and receipt.get('context')==CONTEXT,
            'receipt-target-unverified')
    require(receipt.get('kind') in ('completion','recovery'),'invalid-receipt-kind')
    recovering=receipt['kind']=='recovery'
    expected_image=current['current_image'] if recovering else current['requested_image']
    require(expected_image in validate_catalog(catalog),'receipt-image-unverified')
    if phase is not None:
        require(not recovering and current['requested_image']==phase['image'] and
                current['strategy']==phase['strategy'] and current['promotion_mode']==phase['promotion_mode'],
                'receipt-phase-mismatch')
    before,after=receipt['before'],receipt['after']
    require(type(after['at']) in (int,float) and 0<=now-after['at']<=90 and before['at']<after['at'],
            'receipt-expired-or-invalid-window')
    pod_sets=[]
    for snapshot in (before,after):
        app,ro,vs,dr=(snapshot[k] for k in ('application','rollout','virtualservice','destinationrule'))
        for key,value in [('uid',current['rollout_uid']),('generation',current['generation']),('name','account-release'),('namespace',NS)]:
            require(ro['metadata'][key]==value,'receipt-runtime-identity-mismatch')
        require(ro['status']['currentPodHash']==current['revision'],'receipt-revision-mismatch')
        revision=current['git_revision']
        require(app['metadata']['name']=='account-release' and app['metadata']['namespace']==CONTROL_NAMESPACE and
                app['metadata']['uid']==current['application_uid'],'receipt-application-identity-mismatch')
        require(app['spec']['source']['targetRevision']==revision and app['status']['sync']['revision']==revision and
                app['status']['sync']['status']=='Synced' and not app.get('operation') and
                app['status']['operationState']['phase']=='Succeeded','receipt-git-not-synced')
        projected=runtime_status(ro,current['strategy'],current['promotion_mode'],expected_uid=current['rollout_uid'])
        require(projected['requested_image']==current['requested_image'],'receipt-desired-image-mismatch')
        require(projected['state']==('recovering' if recovering else 'succeeded'),'receipt-runtime-not-ready')
        stable=ro['status']['stableRS']
        sets=[rs for rs in snapshot['replicasets']['items'] if rs['metadata'].get('labels',{}).get('rollouts-pod-template-hash')==stable and
              any(o.get('uid')==current['rollout_uid'] and o.get('controller') is True for o in rs['metadata'].get('ownerReferences',[]))]
        require(len(sets)==1,'receipt-stable-owner-unverified');rs=sets[0]
        selected=[p for p in snapshot['pods']['items'] if not p['metadata'].get('deletionTimestamp') and
                  any(o.get('uid')==rs['metadata']['uid'] and o.get('controller') is True for o in p['metadata'].get('ownerReferences',[]))]
        require(len(selected)==rs['spec']['replicas']==rs['status']['readyReplicas'] and len(selected)>0,'receipt-pods-not-ready')
        for pod in selected:
            api=next(c for c in pod['status']['containerStatuses'] if c['name']=='api')
            require(api['ready'] is True and api['imageID']==expected_image and any(
                c['type']=='Ready' and c['status']=='True' for c in pod['status']['conditions']),'receipt-pod-image-or-health-mismatch')
        pod_sets.append({p['metadata']['uid'] for p in selected})
        require(vs['metadata']['namespace']==dr['metadata']['namespace']==NS and
                vs['metadata']['name']==dr['metadata']['name']=='account-release','receipt-route-target-mismatch')
        require(vs['spec']['hosts']==[HOST] and vs['spec']['gateways']==['mesh'] and
                len(vs['spec']['http'])==1 and vs['spec']['http'][0]['name']=='primary' and dr['spec']['host']==HOST,
                'receipt-route-scope-mismatch')
        routes=vs['spec']['http'][0]['route']
        if phase is not None and phase.get('bootstrap_hash'):
            require(stable==current['revision']==phase['bootstrap_hash'],'receipt-bootstrap-revision-mismatch')
            subsets=dr['spec'].get('subsets',[])
            require(len(subsets)==2 and {s['name'] for s in subsets}=={'stable','canary'} and
                    all(s['labels']=={'app':'account-release','rollouts-pod-template-hash':phase['bootstrap_hash']} for s in subsets),
                    'receipt-bootstrap-subsets-mismatch')
        elif phase is not None and current['strategy']=='Rolling':
            require(not dr['spec'].get('subsets'),'receipt-rolling-subsets-mismatch')
        if current['strategy']=='Rolling':
            require('argo-rollouts.argoproj.io/managed-by-rollouts' not in dr['metadata'].get('annotations',{}),
                    'receipt-istio-detach-not-observed')
            require(routes==[{'destination':{'host':HOST,'port':{'number':8080}},'weight':100}],'receipt-rolling-route-mismatch')
        else:
            require(routes==[{'destination':{'host':HOST,'port':{'number':8080},'subset':name},'weight':weight}
                            for name,weight in [('stable',100),('canary',0)]],'receipt-canary-recovery-route-mismatch')
            stable_subsets=[s for s in dr['spec']['subsets'] if s['name']=='stable']
            require(len(stable_subsets)==1 and stable_subsets[0]['labels']['rollouts-pod-template-hash']==stable,'receipt-subset-mismatch')
    require(pod_sets[0]==pod_sets[1],'receipt-pods-changed-during-observation')
    traffic=receipt['traffic']
    require(before['at']<=traffic['start']<traffic['end']<=after['at'],'receipt-traffic-window-mismatch')
    require(traffic['images']['stable']==expected_image and traffic['weight']==0,'receipt-traffic-image-mismatch')
    actual=verify_traffic(traffic,expected_target=TARGET)
    require(actual['stable']==200 and actual['candidate']==0,'receipt-traffic-not-restored')
    restart=False
    if receipt.get('pre_restart'):
        pre=receipt['pre_restart'];operation=after['application']['status']['operationState']
        finished=datetime.datetime.fromisoformat(operation['finishedAt'].replace('Z','+00:00')).timestamp()
        require(pre['rollout_uid']==current['rollout_uid'] and pre['git_revision']==current['git_revision'] and
                pre['image']==expected_image and pre['at']<=finished<=before['at'] and
                set(pre['pod_uids']) and not (set(pre['pod_uids'])&pod_sets[1]) and
                operation.get('syncResult',{}).get('revision')==current['git_revision'],'receipt-resync-restart-unverified')
        verify_controller_restarts(receipt,pre['at'],before['at'])
        restart=True
    return {'traffic_health_verified':True,'resync_restart_verified':restart,'verified_at':after['at'],
            'receipt_sha256':digest(receipt)}
