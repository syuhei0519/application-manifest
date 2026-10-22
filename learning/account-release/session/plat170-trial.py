# Historical approved session helper. Reapproval and new ownership guards are required for another session.
"""Single approved dedicated-kind session. Never reads Secret objects."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]/"evidence"
sys.path.insert(0, str(ROOT.parent/'application-manifest/learning/account-release'))
from integration import TRAFFIC_QUERY, TRAFFIC_TIMESTAMP_QUERY, verify_traffic, sdk_query_set
from contract import TARGET

K = ['kubectl', '--kubeconfig', str(ROOT/'plat170-kubeconfig'), '--context', TARGET['context']]
NS = TARGET['namespace']
CONTROL = TARGET['control_namespace']
REVISION = 'b971ef3e310bac85d1899a1816a71f8c46686d1e'
CATALOG = json.loads((ROOT.parent/'application-manifest/learning/account-release/image-catalog.reviewed.json').read_text())
IMAGES = {'stable': CATALOG[0]['image'], 'candidate': CATALOG[1]['image']}

def verify_state(state, weight):
    app, ro, vs, dr = (state[k] for k in ('application','rollout','vs','dr'))
    assert app['spec']['source']['targetRevision'] == REVISION
    assert app['status']['sync']['revision'] == REVISION and app['status']['sync']['status']=='Synced'
    assert not app.get('operation') and app['status']['operationState']['phase']=='Succeeded'
    assert str(ro['status']['observedGeneration']) == str(ro['metadata']['generation'])
    assert ro['spec']['template']['spec']['containers'][0]['image'] == IMAGES['candidate']
    stable,candidate=ro['status']['stableRS'],ro['status']['currentPodHash']
    assert stable!=candidate
    if weight:
        assert not ro['status'].get('abort') and ro['status']['phase']=='Paused'
        assert ro['status']['currentStepIndex']=={10:1,50:3,100:5}[weight]
        assert any(p['reason']=='CanaryPauseStep' for p in ro['status']['pauseConditions'])
    else:
        assert ro['status']['abort'] is True
    host='account-release.learning-account-release.svc.cluster.local'
    assert vs['spec']['http'][0]['route']==[
        dict(destination=dict(host=host,subset=r,port={'number':8080}),weight=w)
        for r,w in [('stable',100-weight),('canary',weight)]]
    assert dr['spec']['subsets']==[
        {'name':r,'labels':{'app':'account-release','rollouts-pod-template-hash':h}}
        for r,h in [('stable',stable),('canary',candidate)]]
    counts, pods, identity = {}, [], {}
    for role,h in [('stable',stable),('candidate',candidate)]:
        rs=next(r for r in state['rs']['items'] if r['metadata']['labels'].get('rollouts-pod-template-hash')==h)
        assert rs['spec']['template']['spec']['containers'][0]['image']==IMAGES[role]
        desired=rs['spec']['replicas']
        selected=[p for p in state['pods']['items'] if p['metadata'].get('labels',{}).get('rollouts-pod-template-hash')==h
                  and not p['metadata'].get('deletionTimestamp')]
        assert len(selected)==desired==rs['status'].get('replicas',0)==rs['status'].get('readyReplicas',0)
        if role=='stable' and weight!=100 or role=='candidate' and weight!=0:
            assert desired>=1, 'required role missing: '+role
        counts[h]=desired
        for p in selected:
            assert any(c['type']=='Ready' and c['status']=='True' for c in p['status']['conditions'])
            assert any(o['uid']==rs['metadata']['uid'] for o in p['metadata']['ownerReferences'])
            api=next(c for c in p['status']['containerStatuses'] if c['name']=='api')
            assert api['ready'] and api['imageID']==IMAGES[role]
            assert next(c for c in p['spec']['containers'] if c['name']=='api')['image']==IMAGES[role]
            pods.append(p['metadata']['uid'])
            identity[p['metadata']['uid']]={'hash':h,'image':IMAGES[role],'ip':p['status']['podIP']}
    actual=[p['metadata']['uid'] for p in state['pods']['items'] if p['metadata'].get('labels',{}).get('app')=='account-release'
            and not p['metadata'].get('deletionTimestamp')]
    assert set(pods)==set(actual), 'unexpected active revision'
    vhosts=[v for c in state['envoy_routes']['configs'] for v in c['route_config'].get('virtual_hosts',[]) if host in v['domains']]
    assert len(vhosts)==1 and len(vhosts[0]['routes'])==2
    for route, match in zip(vhosts[0]['routes'], [{'path':'/api/accounts','case_sensitive':True},{'prefix':'/api/accounts/','case_sensitive':True}]):
        assert route['match']==match and route['name']=='primary'
        action=route['route']
        observed={action['cluster']:100} if 'cluster' in action else {c['name']:c['weight'] for c in action['weighted_clusters']['clusters']}
        expected={f'outbound|8080|{r}|{host}':w for r,w in [('stable',100-weight),('canary',weight)] if w}
        assert {k:v for k,v in observed.items() if v}==expected
        assert 'retry_policy' not in action
    up=state['up']['data']['result']
    expected_targets={p['ip']+':15090' for p in identity.values()}
    assert {r['metric']['instance'] for r in up if r['metric']['job']=='account-release-envoy' and r['value'][1]=='1'}==expected_targets
    return {'revision':REVISION,'generation':ro['metadata']['generation'],'stable':stable,'candidate':candidate,
        'step':ro['status'].get('currentStepIndex'),'pause':ro['status'].get('pauseConditions'),
        'vs':vs['spec'],'dr':dr['spec'],'identity':identity,'counts':counts}

def run(*args, input=None):
    return subprocess.check_output(list(args), input=input, text=True, stderr=subprocess.PIPE)

def get(kind, name='', namespace=NS):
    return json.loads(run(*K, '-n', namespace, 'get', kind, *([name] if name else []), '-o', 'json'))

def guard():
    assert get('namespace', 'kube-system')['metadata']['uid'] == '2552b4bc-7402-483f-9b20-25f4d851d829'
    assert run('docker', 'inspect', '--format', '{{.Id}}', 'plat170-account-control-plane').startswith('01f61a9ae51f')

def client(code):
    return json.loads(run(*K, '-n', NS, 'exec', '-i', 'deployment/account-release-client', '-c', 'client', '--', 'python3', '-', input=code))

def prom(query, at=None):
    at = time.time() if at is None else at
    code = ('import json,urllib.request,urllib.parse\n'
        'params='+repr({'query': query, 'time': str(at), 'timeout': '5s'})+'\n'
        'url="http://learning-prometheus.observability.svc.cluster.local:9090/api/v1/query?"+urllib.parse.urlencode(params)\n'
        'opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))\n'
        'print(opener.open(url,timeout=5).read().decode())\n')
    return client(code)

def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')

def snapshot(out):
    data = {'at': time.time()}
    for key, kind, name, ns in [('application','application','account-release',CONTROL),
        ('rollout','rollout','account-release',NS),('vs','virtualservice','account-release',NS),
        ('dr','destinationrule','account-release',NS),('pods','pods','',NS),('rs','replicasets','',NS),
        ('endpoints','endpointslices','',NS)]:
        data[key] = get(kind, name, ns)
    routes=run(*K, '-n', NS, 'exec', 'deployment/account-release-client', '-c', 'istio-proxy', '--',
        'pilot-agent', 'request', 'GET', 'config_dump?resource=dynamic_route_configs')
    data['envoy_routes']=json.loads(routes)
    data['up']=prom('up{job=~"account-release-envoy|otel-application"}')
    save(out/'snapshot.json',data)
    return data

def sync(revision, out):
    app=get('application','account-release',CONTROL)
    assert app['spec']['source']['targetRevision'] == revision and len(revision)==40
    assert not app.get('operation')
    operation={'operation': {'initiatedBy': {'username': 'PLAT-170-approved-trial'}, 'sync': {
        'revision': revision, 'prune': False, 'syncOptions': ['RespectIgnoreDifferences=true']}}}
    run(*K,'-n',CONTROL,'patch','application','account-release','--type=merge','-p',json.dumps(operation))
    deadline=time.time()+55
    while time.time()<deadline:
        app=get('application','account-release',CONTROL)
        phase=app.get('status',{}).get('operationState',{}).get('phase')
        if not app.get('operation') and phase in ('Succeeded','Failed','Error'):
            save(out/'sync.json',app)
            assert phase=='Succeeded', str(app.get('status',{}).get('operationState',{}))
            print('Sync Succeeded '+revision,flush=True)
            return
        time.sleep(2)
    save(out/'sync.json',app)
    raise RuntimeError('sync still running; inspect before any new operation')

def load(phase, weight):
    return json.loads(run(*K,'-n',NS,'exec','deployment/account-release-client','-c','client','--',
        'python3','/work/load.py','--phase',phase,'--weight',str(weight)))

def traffic(weight,out):
    before_state=snapshot(out)
    start_identity=verify_state(before_state,weight)
    ro=before_state['rollout']
    stable, candidate=ro['status']['stableRS'],ro['status']['currentPodHash']
    assert stable!=candidate
    weights={r['destination']['subset']:r['weight'] for r in before_state['vs']['spec']['http'][0]['route']}
    assert weights=={'stable':100-weight,'canary':weight}
    images={}
    for role, h in [('stable',stable),('candidate',candidate)]:
        rs=next(r for r in before_state['rs']['items'] if r['metadata']['labels'].get('rollouts-pod-template-hash')==h)
        images[role]=rs['spec']['template']['spec']['containers'][0]['image']
    warmup=load('warmup',weight)
    save(out/'warmup.json',warmup)
    assert warmup['client']['ok']==20
    time.sleep(12)
    start=time.time()
    before=prom(TRAFFIC_QUERY,start)
    bt=prom(TRAFFIC_TIMESTAMP_QUERY,start)
    receipt=load('measure',weight)
    save(out/'receipt.json',receipt)
    time.sleep(12)
    end=time.time()
    after=prom(TRAFFIC_QUERY,end)
    at=prom(TRAFFIC_TIMESTAMP_QUERY,end)
    expected_targets={p['ip']+':15090' for p in start_identity['identity'].values()}
    assert {r['metric']['instance'] for r in before['data']['result']}==expected_targets
    assert {r['metric']['instance'] for r in after['data']['result']}==expected_targets
    final_dir=out/'end'
    final_dir.mkdir()
    end_identity=verify_state(snapshot(final_dir),weight)
    assert start_identity==end_identity, 'runtime changed during measurement window'
    evidence=dict(target=TARGET,weight=weight,observed_weights={'stable':weights['stable'],'candidate':weights['canary']},
        images=images,start=start,end=end,client_window={'start':receipt['start'],'end':receipt['end']},
        client=receipt['client'],before=before,after=after,before_timestamps=bt,after_timestamps=at)
    save(out/'traffic.json',evidence)
    result=verify_traffic(evidence)
    save(out/'verdict.json',result)
    print(json.dumps(result),flush=True)

def sdk(out):
    state=snapshot(out)
    ro=state['rollout']
    stable,candidate=ro['status']['stableRS'],ro['status']['currentPodHash']
    weight=state['vs']['spec']['http'][0]['route'][1]['weight']
    start_identity=verify_state(state,weight)
    counts=start_identity['counts']
    pause=next(p for p in ro['status']['pauseConditions'] if p['reason']=='CanaryPauseStep')
    assert time.time()-datetime.datetime.fromisoformat(pause['startTime'].replace('Z','+00:00')).timestamp()>=300
    queries=sdk_query_set(stable,candidate,0 if weight==100 else counts[stable],counts[candidate])
    at=time.time()
    raw={name:prom(q,at) for name,q in queries.items()}
    scope='job="account-backend",account_namespace="learning-account-release",learning_service="account"'
    metadata=prom('target_info{'+scope+'}',at)
    heartbeat=prom('pe_go_goroutines{'+scope+'}',at)
    heartbeat_time=prom('timestamp(pe_go_goroutines{'+scope+'})',at)
    mappings={}
    expected_pods={uid:p for uid,p in start_identity['identity'].items() if weight!=100 or p['hash']==candidate}
    for row in metadata['data']['result']:
        labels=row['metric']
        if weight==100 and labels['rollout_revision']!=candidate:
            continue
        uid=labels['k8s_pod_uid']
        assert uid in expected_pods and labels['rollout_revision']==expected_pods[uid]['hash']
        entry=next(e for e in CATALOG if e['image']==expected_pods[uid]['image'])
        assert labels['service_version']==entry['source_commit'] and labels['instance'] not in mappings
        mappings[labels['instance']]=uid
    assert set(mappings.values())==set(expected_pods) and len(mappings)==len(expected_pods)
    for payload in (heartbeat,heartbeat_time):
        rows=[r for r in payload['data']['result'] if weight!=100 or r['metric']['rollout_revision']==candidate]
        assert {r['metric']['instance'] for r in rows}==set(mappings) and len(rows)==len(mappings)
        if payload is heartbeat_time:
            assert all(0<=at-float(r['value'][1])<90 for r in rows)
    save(out/'sdk.json',dict(at=at,window_start=at-300,window_end=at,counts=counts,queries=queries,responses=raw,
        target_info=metadata,heartbeat=heartbeat,heartbeat_time=heartbeat_time,instance_pod_map=mappings))
    result={}
    for name,data in raw.items():
        assert data.get('status')=='success' and not data.get('warnings') and not data.get('infos')
        rows=data['data']['result']
        assert len(rows)==1, 'SDK query missing/coverage gate: '+name
        value=float(rows[0]['value'][1])
        if name.endswith('requests'): assert value>=20
        elif name.endswith('availability'): assert .99<=value<=1
        elif name.endswith('latency'): assert .95<=value<=1
        result[name]=value
    final_dir=out/'end'
    final_dir.mkdir()
    assert verify_state(snapshot(final_dir),weight)==start_identity, 'runtime changed during SDK query'
    save(out/'sdk-verdict.json',result)
    print(json.dumps(result),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['snapshot','sync','traffic','sdk'])
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--revision')
    p.add_argument('--weight',type=int,choices=[0,10,50,100])
    a=p.parse_args()
    guard()
    a.out.mkdir(parents=True,exist_ok=False)
    if a.action=='snapshot': snapshot(a.out); print('snapshot saved',flush=True)
    elif a.action=='sync': sync(a.revision,a.out)
    elif a.action=='traffic': traffic(a.weight,a.out)
    elif a.action=='sdk': sdk(a.out)
