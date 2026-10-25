# リリース操作の調整処理から、固定した配備対象への読み書きを引き受ける接続部分です。
"""Fixed-target adapters. All mutating requests use expected identity/CAS.

Configuration is server-only, prepared for an explicitly approved trial. No
credential, command, repository, path, branch or namespace is accepted from UI.
"""
import base64
from copy import deepcopy
import datetime
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

import yaml

from contract import require, validate_catalog
from integration import REPO, POINTERS, image_revision
from operations import ID, SHA
from release_modes import runtime_status
from deployment_target import CONTEXT, CONTROL_NAMESPACE, SERVER, GIT_BRANCH, DEFINITION

REPOSITORY='syuhei0519/application-manifest'
PROFILE='learning/account-release/runtime-profile.yaml'
NS='learning-account-release'
CONTROL=CONTROL_NAMESPACE
NAME='account-release'


class LiveAdapter:
    def __init__(self,config,catalog,runner=subprocess.run,clock=time.time):
        allowed={'kubeconfig','context','rollout_uid','application_uid','definition_application_uid','token_expires_at','session_started_at','session_expires_at','git_base_branch',
                 'gh_wrapper','powershell','kubectl','state_file','approved_bootstrap_revisions'}
        require(set(config)==allowed,'invalid-server-config-fields')
        require(config['context']==CONTEXT,'wrong-runtime-context')
        require(config['git_base_branch']==GIT_BRANCH,'wrong-git-base')
        require(all(type(config[k]) in (int,float) for k in ('session_started_at','session_expires_at','token_expires_at')) and
                0<config['session_expires_at']-config['session_started_at']<=86400 and
                config['session_started_at']<=clock()<config['session_expires_at'] and
                config['token_expires_at']<=config['session_expires_at'],'invalid-session-window')
        require(config['gh_wrapper']==r'C:\Users\espoi\.codex\bin\gh.ps1','unapproved-github-wrapper')
        require(all(Path(config[k]).is_absolute() for k in ('kubeconfig','state_file','kubectl','powershell')),
                'absolute-server-path-required')
        require(config['approved_bootstrap_revisions'] and all(SHA.fullmatch(s) for s in config['approved_bootstrap_revisions']),
                'invalid-bootstrap-revisions')
        self.config,self.catalog,self.runner,self.clock=config,catalog,runner,clock
        validate_catalog(catalog)
        self.images={x['image']:x for x in catalog}

    def command(self,args,payload=None):
        # Never expose subprocess stderr or command output through exceptions.
        require(self.clock()<self.config['token_expires_at'],'trial-token-expired')
        result=self.runner(args,input=None if payload is None else json.dumps(payload),capture_output=True,text=True,timeout=15)
        require(result.returncode==0,'external-operation-failed')
        require(len(result.stdout)<8*1024*1024,'external-response-too-large')
        return json.loads(result.stdout)

    def kube(self,args,payload=None):
        require(self.clock()<self.config['token_expires_at'],'trial-token-expired')
        command=[self.config['kubectl'],'--kubeconfig',self.config['kubeconfig'],'--context',self.config['context'],*args]
        return self.command(command,payload)

    def get(self,kind,name=None,namespace=NS):
        scopes={'rollout':(NS,NAME),'application':(CONTROL,NAME),'virtualservice':(NS,NAME),
                'definition':(CONTROL,DEFINITION),
                'destinationrule':(NS,NAME),'pods':(NS,None),'replicasets':(NS,None),'analysisruns':(NS,None)}
        require(kind in scopes and (namespace,name)==scopes[kind],'kubernetes-read-out-of-scope')
        args=['-n',namespace,'get','application' if kind=='definition' else kind,*([name] if name else []),'-o','json']
        if kind in ('pods','replicasets'):args+=['-l','app=account-release']
        return self.kube(args)

    def gh(self,method,endpoint,payload=None):
        require(method in ('GET','POST'),'github-method-denied')
        require(endpoint.startswith('repos/'+REPOSITORY+'/'),'github-target-denied')
        args=[self.config['powershell'],'-NoProfile','-File',self.config['gh_wrapper'],'api',endpoint,'--method',method]
        if payload is not None:args+=['--input','-']
        return self.command(args,payload)

    def profile(self,revision):
        require(isinstance(revision,str) and SHA.fullmatch(revision),'exact-git-revision-required')
        data=self.gh('GET',f'repos/{REPOSITORY}/contents/{PROFILE}?ref={revision}')
        require(data.get('type')=='file' and data.get('path')==PROFILE and data.get('encoding')=='base64',
                'invalid-profile-object')
        raw=base64.b64decode(data['content']);require(len(raw)<8192,'profile-too-large')
        profile=yaml.safe_load(raw)
        require(isinstance(profile,dict) and set(profile) in (
            {'owner','image','sourceCommit','imageRevision','strategy','promotionMode','analysisPluginEnabled'},
            {'owner','image','sourceCommit','imageRevision','strategy','promotionMode','analysisPluginEnabled','bootstrapHash'}),
            'explicit-git-profile-required')
        require(profile['owner']=='plat170-account' and profile['image'] in self.images,'profile-target-unverified')
        entry=self.images[profile['image']]
        require(profile['sourceCommit']==entry['source_commit'] and profile['imageRevision']==image_revision(profile['image']),
                'profile-image-source-mismatch')
        from release_modes import validate_mode
        validate_mode(profile['strategy'],profile['promotionMode'])
        require(profile['analysisPluginEnabled'] is True,'reviewed-analysis-required')
        return profile

    def application(self):
        app=self.get('application',NAME,CONTROL)
        require(app['metadata']['uid']==self.config['application_uid'] and
                app['metadata']['namespace']==CONTROL and app['metadata']['name']==NAME,'application-replaced')
        spec=app['spec'];source=spec['source']
        require(set(spec)=={'project','source','destination','syncPolicy','ignoreDifferences'},
                'application-spec-drift')
        require(spec['project']==NAME and spec['destination']=={'server':SERVER,'namespace':NS},
                'application-target-drift')
        require(source['repoURL']==REPO and source['path']=='learning/account-release/chart' and
                source['helm']=={'releaseName':NAME,'valueFiles':['../runtime-profile.yaml']} and
                set(source)=={'repoURL','path','targetRevision','helm'} and
                isinstance(source['targetRevision'],str) and SHA.fullmatch(source['targetRevision']),'application-source-drift')
        require(not spec.get('syncPolicy',{}).get('automated') and
                spec.get('syncPolicy',{}).get('syncOptions')==['RespectIgnoreDifferences=true'],
                'application-sync-policy-drift')
        require(not app['metadata'].get('deletionTimestamp'),'application-deleting')
        return app

    def definition(self):
        app=self.get('definition',DEFINITION,CONTROL);spec=app['spec']
        require(app['metadata']['uid']==self.config['definition_application_uid'] and
                app['metadata']['namespace']==CONTROL and app['metadata']['name']==DEFINITION and
                not app['metadata'].get('deletionTimestamp'),'definition-identity-drift')
        require(spec=={'project':'learning-existing','destination':{'server':SERVER,'namespace':CONTROL},
            'source':{'repoURL':REPO,'targetRevision':GIT_BRANCH,'path':'learning/account-release/application-chart',
                      'helm':{'releaseName':DEFINITION,'valueFiles':['../runtime-profile.yaml'],
                              'parameters':[{'name':'manifestRevision','value':'$ARGOCD_APP_REVISION','forceString':True}]}},
            'syncPolicy':{'syncOptions':['RespectIgnoreDifferences=true']}},'definition-source-drift')
        return app

    @staticmethod
    def ignored(strategy):
        return [] if strategy=='Rolling' else [dict(group='networking.istio.io',kind=k,name=NAME,
            namespace=NS,jsonPointers=p) for k,p in POINTERS.items()]

    def observe(self):
        app=self.application();revision=app.get('status',{}).get('sync',{}).get('revision')
        require(isinstance(revision,str) and SHA.fullmatch(revision),'observed-manifest-revision-required')
        profile=self.profile(revision)
        require(app['spec'].get('ignoreDifferences')==self.ignored(profile['strategy']),'application-ignore-drift')
        ro=self.get('rollout',NAME)
        self.runtime_binding(ro,profile)
        status=runtime_status(ro,profile['strategy'],profile['promotionMode'],expected_uid=self.config['rollout_uid'])
        require(status['state']!='unavailable','runtime-unavailable')
        rs=self.get('replicasets')['items'];stable=ro.get('status',{}).get('stableRS')
        stable_rs=[r for r in rs if r['metadata'].get('labels',{}).get('rollouts-pod-template-hash')==stable and
                   any(o.get('uid')==ro['metadata']['uid'] and o.get('controller') is True for o in r['metadata'].get('ownerReferences',[]))]
        require(len(stable_rs)==1,'stable-revision-unverified')
        image=next(c['image'] for c in stable_rs[0]['spec']['template']['spec']['containers'] if c['name']=='api')
        require(image in self.images,'stable-image-unverified')
        status.update(current_image=image,git_revision=revision,version=int(ro['metadata']['resourceVersion']),
            application_uid=app['metadata']['uid'],
            rollout_uid=ro['metadata']['uid'],release_id=ro['metadata']['uid']+'-'+str(ro['metadata']['generation']),
            pause_reasons=sorted(p['reason'] for p in ro.get('status',{}).get('pauseConditions',[])),
            git_runtime_drift=(app.get('status',{}).get('sync',{}).get('status')!='Synced' or
                app.get('status',{}).get('sync',{}).get('revision')!=revision or
                status['requested_image']!=profile['image']),
            release_managed=revision in self.config['approved_bootstrap_revisions'])
        status['analysis_run']=ro.get('status',{}).get('canary',{}).get('currentStepAnalysisRunStatus',{}).get('name')
        status['rollout_revision']=ro['metadata'].get('annotations',{}).get('rollout.argoproj.io/revision')
        status['analysis_index']=status['current_step']
        status['analysis_historical']=False
        steps=ro['spec']['strategy']['canary'].get('steps',[])
        if not status['analysis_run'] and status['current_step'] and steps:
            index=min(status['current_step'],len(steps))-1
            if index>=0 and 'analysis' in steps[index] and (
                status['state']=='succeeded' or (status['state']=='paused' and status['pause_reasons']==['CanaryPauseStep'])):
                status['analysis_index']=index;status['analysis_historical']=True
        status['analysis_stage']=None
        for step in ro['spec']['strategy']['canary'].get('steps',[])[:(status['current_step'] or 0)+1]:
            if 'setWeight' in step:status['analysis_stage']=str(step['setWeight'])
        # Previous identities come only from server-owned verified history, never
        # from browser inputs or a guessed ReplicaSet age.
        history_path=Path(self.config['state_file'])
        history=json.loads(history_path.read_text()) if history_path.exists() else {}
        previous=history.get('verified_previous')
        if previous and previous.get('image') in self.images and previous.get('verified') is True:
            require(previous.get('rollout_uid')==status['rollout_uid'] and
                    isinstance(previous.get('receipt_sha256'),str) and re.fullmatch('[a-f0-9]{64}',previous['receipt_sha256']) and
                    self.profile(previous.get('git_revision'))['image']==previous['image'],'previous-evidence-unverified')
            from evidence_verifier import verify,digest
            prior=history.get('previous_observation',{})
            require(prior.get('proof',{}).get('receipt_sha256')==previous['receipt_sha256'],
                    'previous-receipt-required')
            oldproof=prior['proof']
            require(digest(oldproof['receipt'])==previous['receipt_sha256'],'previous-receipt-hash-mismatch')
            verify(oldproof['receipt'],prior['current'],self.catalog,oldproof['verified_at'])
            status['verified_previous']=previous
        status['stable_traffic_verified']=False
        # A healthy completed release can be restored without an Abort. Aborted
        # releases instead require a recovery receipt for their stable revision.
        # Both receipts must be fresh and bound to this live identity.
        status['stable_traffic_verified']=self.restore_ready(history,status)
        request_map=history.get('managed_revisions',{})
        if revision in request_map:
            status['release_request_id']=request_map[revision]
            status['release_managed']=True
        return status

    def restore_ready(self,history,current):
        kind={'succeeded':'completion','restored':'completion','recovering':'recovery'}.get(current['state'])
        if kind is None:return False
        proof=history.get('stable_recovery' if kind=='recovery' else 'completion',{})
        return (proof.get('receipt',{}).get('kind')==kind and
                proof.get('traffic_health_verified') is True and self.proof_matches(proof,current))

    @staticmethod
    def runtime_binding(ro,profile):
        canary=ro['spec'].get('strategy',{}).get('canary',{})
        require(ro['spec'].get('replicas')==2 and canary.get('maxSurge')==1 and canary.get('maxUnavailable')==0,
                'runtime-capacity-drift')
        if profile['strategy']=='Rolling':
            require(not any(canary.get(k) for k in ('steps','trafficRouting','analysis')),'runtime-mode-drift')
            return
        steps=[]
        for weight in (10,50,100):
            steps += [{'setWeight':weight},{'analysis':{'args':[
                {'name':'stable-hash','valueFrom':{'podTemplateHashValue':'Stable'}},
                {'name':'candidate-hash','valueFrom':{'podTemplateHashValue':'Latest'}},
                {'name':'stage','value':str(weight)}],'templates':[{'templateName':'account-release-policy'}]}}]
            if profile['promotionMode']=='manual':steps.append({'pause':{}})
        require(canary.get('steps')==steps and canary.get('trafficRouting')=={'istio':{
            'destinationRule':{'canarySubsetName':'canary','name':NAME,'stableSubsetName':'stable'},
            'virtualService':{'name':NAME,'routes':['primary']}}},'runtime-mode-drift')

    def proof_matches(self,proof,current,phase=None):
        # Only the session verifier writes this server-owned file. Proof is bound
        # to the live identity and expires; a boolean from a browser is never proof.
        keys=('rollout_uid','generation','git_revision','requested_image','revision')
        at=proof.get('verified_at')
        identity_ok=(all(proof.get(k)==current.get(k) for k in keys) and
                type(at) in (int,float) and 0<=self.clock()-at<=90 and
                isinstance(proof.get('receipt_sha256'),str) and
                bool(re.fullmatch('[a-f0-9]{64}',proof['receipt_sha256'])))
        if not identity_ok:return False
        from evidence_verifier import verify,digest
        try:
            receipt=proof['receipt']
            require(digest(receipt)==proof['receipt_sha256'],'receipt-hash-mismatch')
            verified=verify(receipt,current,self.catalog,self.clock(),phase=phase)
            return (verified['verified_at']==at and verified['traffic_health_verified'] is True and
                    proof.get('resync_restart_verified',False)==verified['resync_restart_verified'])
        except (KeyError,TypeError,ValueError,StopIteration):return False

    def diff(self,revision,phase):
        before=self.profile(revision);after=deepcopy(before)
        after.update(image=phase['image'],sourceCommit=self.images[phase['image']]['source_commit'],
                     imageRevision=image_revision(phase['image']),strategy=phase['strategy'],
                     promotionMode=phase['promotion_mode'],analysisPluginEnabled=True)
        after.pop('bootstrapHash',None)
        if phase['bootstrap_hash']:after['bootstrapHash']=phase['bootstrap_hash']
        return {'path':PROFILE,'base_revision':revision,'before':before,'after':after}

    def propose(self,proposal_id,expected_revision,phase):
        require(ID.fullmatch(proposal_id) and len(proposal_id)<=80,'invalid-proposal-id')
        root='repos/'+REPOSITORY
        current=self.gh('GET',root+'/git/ref/heads/'+self.config['git_base_branch'])
        require(current['object']['sha']==expected_revision,'git-base-changed')
        branch='release/account-'+proposal_id
        # A unique deterministic branch makes partial success discoverable. No
        # force push, main update or merge API exists in this adapter.
        self.gh('POST',root+'/git/refs',{'ref':'refs/heads/'+branch,'sha':expected_revision})
        commit=self.gh('GET',root+'/git/commits/'+expected_revision)
        diff=self.diff(expected_revision,phase)
        tree=self.gh('POST',root+'/git/trees',{'base_tree':commit['tree']['sha'],'tree':[
            {'path':PROFILE,'mode':'100644','type':'blob','content':yaml.safe_dump(diff['after'],sort_keys=False)}]})
        new=self.gh('POST',root+'/git/commits',{'message':'Account release request '+proposal_id,'tree':tree['sha'],'parents':[expected_revision]})
        # The only branch update is non-forced and follows a re-read. Its commit
        # has the expected parent, so a competing fast-forward is rejected.
        self._update_branch(branch,expected_revision,new['sha'])
        pr=self.gh('POST',root+'/pulls',{'title':'Account release '+proposal_id,'head':branch,
            'base':self.config['git_base_branch'],'draft':True,'body':'Account 専用 profile 変更要求。固定 digest と方式をレビューしてください。自動 merge・配備は行いません。'})
        return {'pr_url':pr['html_url'],'commit':new['sha'],'base_revision':expected_revision}

    def _update_branch(self,branch,expected,new):
        require(re.fullmatch(r'release/account-[A-Za-z0-9_-]{8,80}',branch),'branch-out-of-scope')
        require(SHA.fullmatch(expected) and SHA.fullmatch(new),'invalid-branch-commit')
        endpoint=f'repos/{REPOSITORY}/git/refs/heads/{branch}'
        current=self.gh('GET',f'repos/{REPOSITORY}/git/ref/heads/{branch}')
        require(current['object']['sha']==expected,'proposal-branch-changed')
        args=[self.config['powershell'],'-NoProfile','-File',self.config['gh_wrapper'],'api',endpoint,
              '--method','PATCH','--input','-']
        self.command(args,{'sha':new,'force':False})

    def act(self,operation,expected):
        require(operation in ('promote','abort'),'unsupported-runtime-operation')
        from operations import Coordinator
        current=self.observe()
        require(Coordinator.identity(current)==expected,'stale-runtime-identity')
        require(current['strategy']=='Canary' and operation in current['actions'],'runtime-operation-not-allowed')
        ro=self.get('rollout',NAME);status=ro['status']
        require(ro['metadata']['resourceVersion']==expected['resource_version'],'runtime-resource-changed')
        patch=[{'op':'test','path':'/metadata/uid','value':expected['rollout_uid']},
               {'op':'test','path':'/metadata/resourceVersion','value':expected['resource_version']},
               {'op':'test','path':'/metadata/generation','value':expected['generation']}]
        if operation=='abort':patch.append({'op':'add','path':'/status/abort','value':True})
        else:
            require(current['promotion_mode']=='manual' and current['pause_reasons']==['CanaryPauseStep'],
                    'manual-pause-required')
            steps=ro['spec']['strategy']['canary']['steps'];index=status['currentStepIndex']
            require(index<len(steps) and steps[index]=={'pause':{}},'manual-step-mismatch')
            patch += [{'op':'test','path':'/status/currentStepIndex','value':index},
                      {'op':'replace','path':'/status/currentStepIndex','value':index+1},
                      {'op':'add','path':'/status/pauseConditions','value':[]},
                      {'op':'add','path':'/status/controllerPause','value':False}]
        return self.kube(['-n',NS,'patch','rollout',NAME,'--subresource=status','--type=json','-p',json.dumps(patch),'-o','json'])

    def action_outcome(self,operation,expected):
        current=self.observe()
        if any(current[k]!=expected[k] for k in ('rollout_uid','generation','git_revision','requested_image')):
            return {'state':'superseded','reason':'identity-changed'}
        ro=self.get('rollout',NAME)
        confirmed=(ro['status'].get('abort') is True if operation=='abort' else
                   ro['status'].get('currentStepIndex',-1)>expected['current_step'])
        return {'state':'confirmed' if confirmed else 'unknown','reason':'runtime-verified' if confirmed else 'operation-not-observed'}

    def verdict(self,current,runs=None):
        import math
        if runs is None:runs=self.get('analysisruns')['items']
        candidates=[a for a in runs if a['metadata'].get('namespace')==NS and any(
            o.get('uid')==current['rollout_uid'] and o.get('controller') is True
            for o in a['metadata'].get('ownerReferences',[]))]
        candidates.sort(key=lambda a:a['metadata']['creationTimestamp'],reverse=True)
        for run in candidates:
            args={a['name']:a.get('value') for a in run['spec'].get('args',[])}
            if ((not current.get('analysis_historical') and run['metadata']['name']!=current.get('analysis_run')) or
                run['metadata'].get('labels',{}).get('step-index')!=str(current.get('analysis_index')) or
                run['metadata'].get('annotations',{}).get('rollout.argoproj.io/revision')!=current.get('rollout_revision') or
                args.get('candidate-hash')!=current['revision'] or
                args.get('stage')!=current.get('analysis_stage')):continue
            metrics=run.get('status',{}).get('metricResults',[])
            measurements=[m for metric in metrics if metric['name']=='account-policy' for m in metric.get('measurements',[])]
            if not measurements:return None
            measurement=measurements[-1]
            if not measurement.get('value'):
                return {'phase':'判定不能','reasons':['analysis-result-unavailable']}
            data=json.loads(measurement['value'])
            require(data.get('schema')=='account-verdict-v1' and data.get('analysis_run_uid')==run['metadata']['uid'] and
                    data.get('analysis_run')==run['metadata']['name'],
                    'verdict-identity-mismatch')
            # Return only known public metric fields. No arbitrary AnalysisRun
            # message or plugin exception is forwarded to the browser.
            require(data.get('window_seconds')==300 and type(data.get('window_start')) is int and
                    type(data.get('window_end')) is int and data['window_end']-data['window_start']==300 and
                    0<=self.clock()-data['window_end'],'invalid-observation-window')
            policy={'minimum_requests':20,'availability_min':.99,'latency_fraction_min':.95,
                    'latency_bucket_seconds':.25,'query_timeout_seconds':5}
            require(data.get('policy')==policy,'verdict-policy-mismatch')
            historical=current.get('analysis_historical') is True
            stale=self.clock()-data['window_end']>90
            public={'phase':'historical' if historical else ('stale' if stale else data['verdict']),
                    'observed_verdict':data['verdict'],'historical':historical,'reasons':[],
                    'window':{'start':data['window_start'],'end':data['window_end']},'roles':{},
                    'analysis_run':run['metadata']['name'],'analysis_run_uid':run['metadata']['uid'],
                    'candidate_revision':args['candidate-hash'],'stage':args.get('stage'),
                    'source_time':measurement.get('finishedAt'),'policy':policy,
                    'age_seconds':self.clock()-data['window_end'],'stale':stale}
            require(data['verdict'] in ('passed','failed','inconclusive'),'invalid-verdict-result')
            for reason in data.get('reasons',[]):
                public['reasons'].append(reason if isinstance(reason,str) and re.fullmatch('[a-z0-9-]{1,100}',reason) else 'observation-unavailable')
            for role,values in data.get('roles',{}).items():
                require(role in ('stable','candidate'),'mixed-verdict-role')
                require(values.get('revision_hash')==args.get(role+'-hash') and values.get('image') in self.images,
                        'mixed-verdict-revision')
                safe={'revision':values['revision_hash'],'image':values['image']}
                for key in ('requests','availability','latency_fraction'):
                    value=values.get(key)
                    if value is not None:require(type(value) in (int,float) and math.isfinite(value),'invalid-metric-value')
                    safe[key]=value
                public['roles'][role]=safe
            return public
        return None

    def verdict_history(self,current):
        runs=self.get('analysisruns')['items']
        runs.sort(key=lambda a:a['metadata']['creationTimestamp'],reverse=True)
        history=[]
        for run in runs[:20]:
            args={a['name']:a.get('value') for a in run['spec'].get('args',[])}
            labels=run['metadata'].get('labels',{})
            index=labels.get('step-index','')
            if not index.isdigit():continue
            context=dict(current,analysis_run=run['metadata']['name'],analysis_index=int(index),
                rollout_revision=run['metadata'].get('annotations',{}).get('rollout.argoproj.io/revision'),
                revision=args.get('candidate-hash'),analysis_stage=args.get('stage'),analysis_historical=True)
            result=self.verdict(context,[run])
            if result:
                result.update(release_revision=context['rollout_revision'],source='rollouts-controller')
                history.append(result)
        return history

    def reviewed(self,proposal_id,expected):
        require(isinstance(proposal_id,str) and ID.fullmatch(proposal_id),'invalid-proposal-id')
        root='repos/'+REPOSITORY;branch='release/account-'+proposal_id
        rows=self.gh('GET',root+'/pulls?state=all&head=syuhei0519:'+branch+'&base='+self.config['git_base_branch']+'&per_page=100')
        require(len(rows)<=1,'duplicate-proposal-pr')
        if not rows:return None
        full=self.gh('GET',root+'/pulls/'+str(rows[0]['number']))
        require(full['head']['ref']==branch and full['base']['ref']==self.config['git_base_branch'] and
                full['head']['repo']['full_name']==REPOSITORY and full['base']['repo']['full_name']==REPOSITORY,
                'proposal-target-drift')
        return full

    def verified_merge(self,pr,expected):
        """Repeat the same Git boundary for apply and read-only reconciliation."""
        require(pr is not None and pr['merged'] is True,'git-review-not-merged')
        revision=pr['merge_commit_sha'];require(SHA.fullmatch(revision),'merge-revision-unverified')
        branch=self.gh('GET',f'repos/{REPOSITORY}/git/ref/heads/'+self.config['git_base_branch'])
        require(branch['object']['sha']==revision,'git-base-changed')
        phase=expected['phases'][expected['phase']]
        require(self.profile(revision)==self.diff(expected['identity']['git_revision'],phase)['after'],
                'reviewed-profile-mismatch')
        # Limit the reviewed Git change to the profile and reject unrelated code
        # changes even when they were included in the same merged PR.
        files=self.gh('GET',f'repos/{REPOSITORY}/pulls/{pr["number"]}/files?per_page=100')
        require(len(files)==1 and files[0]['filename']==PROFILE and files[0]['status']=='modified',
                'reviewed-change-out-of-scope')
        trees=[]
        for sha in (expected['identity']['git_revision'],revision):
            commit=self.gh('GET',f'repos/{REPOSITORY}/git/commits/{sha}')
            tree=self.gh('GET',f'repos/{REPOSITORY}/git/trees/{commit["tree"]["sha"]}?recursive=1')
            require(tree.get('truncated') is False,'incomplete-git-tree')
            trees.append({e['path']:(e['mode'],e['type'],e['sha']) for e in tree['tree'] if e['type']!='tree'})
        changed={p for p in set(trees[0])|set(trees[1]) if trees[0].get(p)!=trees[1].get(p)}
        require(changed=={PROFILE},'merged-tree-out-of-scope')
        return revision,phase

    def prepare_reviewed(self,proposal_id,expected):
        pr=self.reviewed(proposal_id,expected)
        revision,phase=self.verified_merge(pr,expected)
        current=self.observe()
        require(all(current[k]==expected['identity'][k] for k in
                    ('rollout_uid','generation','git_revision','requested_image')),'stale-runtime-identity')
        app=self.application()
        require(app['status']['sync']['revision']==expected['identity']['git_revision'] and not app.get('operation'),
                'application-operation-active')
        # An old failed sync must be examined rather than replaced implicitly.
        require(app.get('status',{}).get('operationState',{}).get('phase')=='Succeeded','prior-sync-not-successful')
        from verified_state import update
        match=re.fullmatch(r'([A-Za-z0-9_-]{8,80})-phase-([0-9]+)',proposal_id)
        require(match is not None and int(match[2])==expected['phase'],'proposal-phase-mismatch')
        def remember(history):
            if phase['name']=='restore-image':
                require(self.restore_ready(history,current),'stable-traffic-receipt-required')
            elif phase['image']!=current['current_image']:
                prior=history.get('verified_current',{})
                require(self.proof_matches(prior.get('proof',{}),current),'previous-live-receipt-required')
                history['previous_observation']=prior
                history['verified_previous']={'verified':True,'image':current['current_image'],
                    'git_revision':current['git_revision'],'rollout_uid':current['rollout_uid'],
                    'receipt_sha256':prior['proof']['receipt_sha256']}
            revisions=history.setdefault('managed_revisions',{})
            require(revision not in revisions or revisions[revision]==match[1],'managed-revision-conflict')
            revisions[revision]=match[1]
        sync_options=['RespectIgnoreDifferences=true']
        if current['strategy']=='Rolling' and phase['strategy']=='Canary':
            # For custom resources Argo's ignored-field merge patch replaces
            # arrays wholesale. Preserving the old single full route would
            # erase Canary's two destinations. Only the verified old-image
            # activation installs the reviewed 100/0 route as-is; subsequent
            # image updates/resyncs retain controller-owned weights/selectors.
            require(phase['name']=='activate-mode' and phase['image']==current['current_image'] and
                    phase.get('bootstrap_hash')==current['revision'] and current['state']=='succeeded' and
                    (current['git_runtime_drift'] is False or (
                        app['status']['sync']['status']=='OutOfSync' and
                        current['requested_image']==self.profile(expected['identity']['git_revision'])['image'])) and
                    current['stable_traffic_verified'] is True,
                    'canary-activation-receipt-required')
            sync_options=['RespectIgnoreDifferences=false']
        # Persist the verified target before dispatch so a lost patch response
        # still leaves its managed identity available for read-only recovery.
        def remember_sync(history):
            remember(history)
            history.setdefault('reviewed_sync_options',{})[revision]=sync_options
        update(self.config['state_file'],remember_sync)
        definition=self.definition()
        require(not definition.get('operation') and definition.get('status',{}).get('operationState',{}).get('phase')=='Succeeded',
                'definition-operation-active-or-failed')
        # Git owns the child spec, including mode-specific ignore pointers. Only
        # sync operations are patched; no source/ignore spec is written by UI.
        patch=[{'op':'test','path':'/metadata/uid','value':self.config['definition_application_uid']},
               {'op':'test','path':'/metadata/resourceVersion','value':definition['metadata']['resourceVersion']},
               {'op':'test','path':'/spec','value':definition['spec']},
               {'op':'add','path':'/operation','value':{'initiatedBy':{'username':'account-release'},
                  'sync':{'revision':GIT_BRANCH,'prune':False,'syncOptions':['RespectIgnoreDifferences=true']}}}]
        return {'patch':patch,'commit':revision,'pr_url':pr['html_url'],'workload_sync_options':sync_options}

    def dispatch_reviewed(self,prepared):
        # Only server-internal preparation can reach this method. The patch has
        # UID/resourceVersion/spec tests, so changed runtime rejects the dispatch.
        require(set(prepared)=={'patch','commit','pr_url','workload_sync_options'},'invalid-prepared-operation')
        self.kube(['-n',CONTROL,'patch','application',DEFINITION,'--type=json','-p',json.dumps(prepared['patch']),'-o','json'])
        return {k:prepared[k] for k in ('commit','pr_url')}

    def apply_reviewed(self,proposal_id,expected):
        return self.dispatch_reviewed(self.prepare_reviewed(proposal_id,expected))

    def sync_workload_after_definition(self,revision,phase,expected):
        definition=self.definition()
        op=definition.get('status',{}).get('operationState',{})
        require(not (op.get('operation',{}).get('sync',{}).get('revision')==GIT_BRANCH and op.get('phase') in ('Failed','Error')),
                'definition-sync-failed')
        if (definition.get('operation') or definition.get('status',{}).get('sync',{}).get('revision')!=revision or
                definition['status']['sync'].get('status')!='Synced' or op.get('phase')!='Succeeded'):
            return False
        app=self.application()
        require(app['spec']['ignoreDifferences']==self.ignored(phase['strategy']) and
                app['spec']['source']['targetRevision']==revision,'git-definition-not-observed')
        # status.sync.revision is the comparison target and can advance before
        # any apply. Only the completed operation proves which revision ran.
        applied=app.get('status',{}).get('operationState',{})
        if not app.get('operation') and applied.get('phase')=='Succeeded' and applied.get('syncResult',{}).get('revision')==revision:
            return True
        if app.get('operation'):
            require(app['operation'].get('sync',{}).get('revision')==revision,'foreign-workload-operation')
            return False
        ro=self.get('rollout',NAME)
        require(ro['metadata']['uid']==expected['identity']['rollout_uid'] and
                ro['metadata']['generation']==expected['identity']['generation'],'stale-runtime-identity')
        require(applied.get('syncResult',{}).get('revision')==expected['identity']['git_revision'] and
                applied.get('phase')=='Succeeded','prior-sync-not-successful')
        history=json.loads(Path(self.config['state_file']).read_text())
        options=history.get('reviewed_sync_options',{}).get(revision)
        require(options in (['RespectIgnoreDifferences=true'],['RespectIgnoreDifferences=false']),
                'reviewed-sync-options-required')
        patch=[{'op':'test','path':'/metadata/uid','value':self.config['application_uid']},
               {'op':'test','path':'/metadata/resourceVersion','value':app['metadata']['resourceVersion']},
               {'op':'test','path':'/spec','value':app['spec']},
               {'op':'add','path':'/operation','value':{'initiatedBy':{'username':'account-release'},
                'sync':{'revision':revision,'prune':False,'syncOptions':options}}}]
        self.kube(['-n',CONTROL,'patch','application',NAME,'--type=json','-p',json.dumps(patch),'-o','json'])
        return False

    def proposal_outcome(self,proposal_id,expected,result):
        full=self.reviewed(proposal_id,expected)
        if not full:return {'state':'outcome-unknown','reason':'proposal-not-found'}
        if not full['merged']:
            # A closed PR may still have an applied branch commit. Never release
            # its reservation just because its review has closed.
            return {'state':'review-pending' if full['state']=='open' else 'outcome-unknown',
                    'pr_url':full['html_url'],'commit':full['head']['sha'],'reason':'git-not-merged'}
        desired,phase=self.verified_merge(full,expected)
        if not self.sync_workload_after_definition(desired,phase,expected):
            return {'state':'apply-pending','commit':desired,'pr_url':full['html_url'],'reason':'definition-or-workload-sync-pending'}
        app=self.application()
        if app['status']['sync']['revision']!=desired or app.get('operation'):
            return {'state':'apply-pending','commit':desired,'pr_url':full['html_url'],'reason':'desired-not-synced'}
        current=self.observe()
        require(current['rollout_uid']==expected['identity']['rollout_uid'] and
                current['git_revision']==desired and current['requested_image']==phase['image'] and
                current['strategy']==phase['strategy'] and current['promotion_mode']==phase['promotion_mode'],
                'reviewed-runtime-mismatch')
        binding={'release_id':current['release_id'],'generation':current['generation']}
        operation=app.get('status',{}).get('operationState',{})
        if operation.get('operation',{}).get('sync',{}).get('revision')==desired and operation.get('phase') in ('Failed','Error'):
            return {'state':'sync-failed','reason':'sync-failed','commit':desired,'pr_url':full['html_url'],**binding}
        if current['git_runtime_drift']:
            return {'state':'apply-pending','reason':'desired-not-synced',**binding}
        # Full completion requires measured traffic/health and (restore) restart
        # evidence. These are deliberately not inferred from a Healthy badge.
        state_path=Path(self.config['state_file'])
        history=json.loads(state_path.read_text()) if state_path.exists() else {}
        proof=history.get('completion',{})
        if current['state']=='succeeded' and self.proof_matches(proof,current,phase=phase) and proof.get('traffic_health_verified') is True:
            require(current['current_image']==phase['image'],'reviewed-stable-image-mismatch')
            if expected['phase']<len(expected['phases'])-1 and proof.get('phase')==phase['name']:
                return {'state':'phase-review-required','phase_verified':True,'phase_git_revision':desired,'reason':'phase-verified',**binding}
            restore=phase['name']=='restore-image'
            if not restore or proof.get('resync_restart_verified') is True:
                return {'state':'restored' if restore else 'succeeded','git_runtime_verified':True,
                        'traffic_health_verified':True,'resync_restart_verified':restore,
                        'commit':desired,'pr_url':full['html_url'],'reason':'durable-restore-verified' if restore else 'runtime-verified',**binding}
        return {'state':'recovering' if current['state']=='recovering' else 'running',
                'commit':desired,'pr_url':full['html_url'],'reason':'runtime-progressing',**binding}
