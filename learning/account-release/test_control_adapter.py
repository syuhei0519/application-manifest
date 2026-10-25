# 操作用 adapter が固定対象と実行条件を守ることを、外部処理を差し替えて確認します。
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from contract import DefinitiveNoWriteRejected, Rejected
from control_adapter import LiveAdapter, PROFILE, REPOSITORY
from operations import Coordinator
from deployment_target import CONTEXT, CONTROL_NAMESPACE, SERVER, DEFINITION

ROOT=Path(__file__).parent
CATALOG=json.loads((ROOT/'image-catalog.reviewed.json').read_text())
FIXTURE=json.loads((ROOT/'fixtures/control-observation.json').read_text())
# Retain historical receipts on disk; bind this synthetic fixture to the active
# fixed target in memory. No old trial success is reclassified as acceptance.
FIXTURE['application']['metadata']['namespace']=CONTROL_NAMESPACE
FIXTURE['application']['spec']['source']['targetRevision']=FIXTURE['application']['status']['sync']['revision']
FIXTURE['application']['spec']['destination']['server']=SERVER
FIXTURE['definition']=copy.deepcopy(FIXTURE['application'])
FIXTURE['definition']['metadata'].update(name=DEFINITION,uid='definition-uid')
FIXTURE['definition']['spec']={'project':'learning-existing','destination':{'server':SERVER,'namespace':CONTROL_NAMESPACE},
    'source':{'repoURL':'https://github.com/syuhei0519/application-manifest.git','targetRevision':'main',
              'path':'learning/account-release/application-chart','helm':{'releaseName':DEFINITION,'valueFiles':['../runtime-profile.yaml'],
                    'parameters':[{'name':'manifestRevision','value':'$ARGOCD_APP_REVISION','forceString':True}]}},
    'syncPolicy':{'syncOptions':['RespectIgnoreDifferences=true']}}
FIXTURE['definition']['status']['operationState']['phase']='Succeeded'
FIXTURE['definition']['status']['sync']['status']='Synced'
FIXTURE['definition'].pop('operation',None)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.data=copy.deepcopy(FIXTURE);self.calls=[]
        self.now=FIXTURE['at']
        self.config=dict(kubeconfig=str(Path(self.tmp.name)/'config'),context=CONTEXT,
            rollout_uid=self.data['rollout']['metadata']['uid'],application_uid=self.data['application']['metadata']['uid'],
            definition_application_uid='definition-uid',session_started_at=self.now,session_expires_at=self.now+86400,
            token_expires_at=self.now+3600,git_base_branch='main',
            gh_wrapper=r'C:\Users\espoi\.codex\bin\gh.ps1',powershell='/bin/powershell',kubectl='/bin/kubectl',
            state_file=str(Path(self.tmp.name)/'state.json'),approved_bootstrap_revisions=['a'*40])
        self.adapter=LiveAdapter(self.config,CATALOG,runner=self.command,clock=lambda:self.now)
        self.adapter.get=lambda kind,name=None,namespace=None:copy.deepcopy(self.data[{
            'rollout':'rollout','application':'application','definition':'definition','replicasets':'replicasets','analysisruns':'analysis'}[kind]])
        image=self.data['rollout']['spec']['template']['spec']['containers'][0]['image']
        entry=next(e for e in CATALOG if e['image']==image)
        from integration import image_revision
        self.profile=dict(owner='plat170-account',image=image,sourceCommit=entry['source_commit'],
                          imageRevision=image_revision(image),strategy='Canary',promotionMode='auto',analysisPluginEnabled=True)
        self.adapter.profile=lambda revision:copy.deepcopy(self.profile)

    def command(self,args,**kw):
        self.calls.append((args,kw));return SimpleNamespace(returncode=0,stdout='{}')

    def test_real_observation_current_analysis_does_not_reuse_previous_pass(self):
        current=self.adapter.observe()
        self.assertEqual(current['analysis_stage'],'50')
        self.assertIsNone(self.adapter.verdict(current))

    def completed_first(self):
        ro=self.data['rollout'];ro['status']['currentStepIndex']=1
        ro['status']['canary']['currentStepAnalysisRunStatus']['name']=self.data['analysis']['items'][0]['metadata']['name']
        return self.adapter.observe()

    def test_metric_identity_window_policy_and_staleness(self):
        current=self.completed_first();value=self.adapter.verdict(current)
        self.assertEqual(value['phase'],'passed');self.assertEqual(value['roles']['candidate']['availability'],1)
        self.now+=600;self.assertEqual(self.adapter.verdict(current)['phase'],'stale')
        measurement=self.data['analysis']['items'][0]['status']['metricResults'][0]['measurements'][0]
        data=json.loads(measurement['value']);data['analysis_run_uid']='foreign';measurement['value']=json.dumps(data)
        with self.assertRaisesRegex(Rejected,'verdict-identity'):self.adapter.verdict(current)

    def test_manual_pause_uses_previous_analysis_as_explicit_history(self):
        self.profile['promotionMode']='manual';ro=self.data['rollout'];steps=ro['spec']['strategy']['canary']['steps']
        ro['spec']['strategy']['canary']['steps']=[item for i in range(0,len(steps),2) for item in [steps[i],steps[i+1],{'pause':{}}]]
        ro['status'].update(currentStepIndex=2,pauseConditions=[{'reason':'CanaryPauseStep'}])
        ro['status']['canary'].pop('currentStepAnalysisRunStatus')
        value=self.adapter.verdict(self.adapter.observe())
        self.assertEqual(value['phase'],'historical');self.assertEqual(value['observed_verdict'],'passed')
        self.data['analysis']['items'][0]['metadata']['annotations']['rollout.argoproj.io/revision']='1'
        self.assertIsNone(self.adapter.verdict(self.adapter.observe()))

    def test_application_foreign_target_multiple_source_and_auto_sync_rejected(self):
        for mutation in [lambda s:s.update(sources=[{}]),lambda s:s.update(sourceHydrator={}),
                         lambda s:s['destination'].update(namespace='default'),
                         lambda s:s['source'].update(repoURL='https://example.com'),
                         lambda s:s['syncPolicy'].update(automated={ 'selfHeal':True })]:
            self.data=copy.deepcopy(FIXTURE);mutation(self.data['application']['spec'])
            with self.assertRaises(Rejected):self.adapter.observe()
        self.assertEqual(self.calls,[])

    def test_out_of_sync_and_runtime_step_drift_stop_operations(self):
        self.data['application']['status']['sync']['status']='OutOfSync'
        self.assertTrue(self.adapter.observe()['git_runtime_drift'])
        self.data['rollout']['spec']['strategy']['canary']['steps'][0]['setWeight']=100
        with self.assertRaisesRegex(Rejected,'runtime-mode-drift'):self.adapter.observe()

    def test_token_expiry_and_foreign_namespace_never_execute(self):
        self.now=self.config['token_expires_at']
        with self.assertRaisesRegex(Rejected,'trial-token-expired'):self.adapter.kube(['get','pods'])
        with self.assertRaisesRegex(Rejected,'read-out-of-scope'):LiveAdapter.get(self.adapter,'pods',namespace='default')
        with self.assertRaises(Rejected):self.adapter.gh('POST','repos/another/repo/pulls',{})
        with self.assertRaises(Rejected):self.adapter._update_branch('main','a'*40,'b'*40)
        self.assertEqual(self.calls,[])

    def test_unchanged_profile_main_advance_is_typed_and_performs_zero_writes(self):
        calls=[]
        def gh(method,endpoint,payload=None):
            calls.append((method,endpoint,payload))
            return {'object':{'sha':'b'*40}}
        self.adapter.gh=gh
        self.adapter.profile=lambda revision:{'image':'same-profile'}
        with self.assertRaisesRegex(DefinitiveNoWriteRejected,'git-base-advanced-profile-unchanged'):
            self.adapter.propose('request-001-phase-0','a'*40,{})
        self.assertEqual(calls,[('GET',f'repos/{REPOSITORY}/git/ref/heads/main',None)])
        self.assertFalse(any(method=='POST' for method,_,_ in calls))

    def test_profile_change_rejects_before_first_write(self):
        calls=[]
        def gh(method,endpoint,payload=None):
            calls.append((method,endpoint,payload))
            return {'object':{'sha':'b'*40}}
        self.adapter.gh=gh
        self.adapter.profile=lambda revision:{'image':revision}
        with self.assertRaisesRegex(DefinitiveNoWriteRejected,'git-profile-changed'):
            self.adapter.propose('request-001-phase-0','a'*40,{})
        self.assertEqual([method for method,_,_ in calls],['GET'])

    def test_response_loss_after_first_post_is_not_typed_zero_write(self):
        calls=[]
        def gh(method,endpoint,payload=None):
            calls.append((method,endpoint,payload))
            if method=='GET':return {'object':{'sha':'a'*40}}
            raise TimeoutError('synthetic response loss after accepted POST')
        self.adapter.gh=gh
        with self.assertRaises(TimeoutError):
            self.adapter.propose('request-001-phase-0','a'*40,{})
        self.assertEqual([method for method,_,_ in calls],['GET','POST'])

    def test_deleted_context_and_unbounded_session_are_rejected(self):
        for change in [dict(context='kind-plat171-account'),dict(git_base_branch='trial/plat171-account-20261022'),
                       dict(session_expires_at=self.now+86401)]:
            with self.assertRaises(Rejected):LiveAdapter(dict(self.config,**change),CATALOG,runner=self.command,clock=lambda:self.now)
        self.assertEqual(self.calls,[])

    def test_definition_then_workload_sync_never_patches_git_owned_spec(self):
        current=self.adapter.observe();new='b'*40
        phase=dict(image=current['current_image'],name='activate-mode',strategy='Canary',promotion_mode='auto')
        expected={'identity':Coordinator.identity(current),'phase':0,'phases':[phase]}
        self.adapter.reviewed=lambda *args:{'html_url':'https://github.com/syuhei0519/application-manifest/pull/99'}
        self.adapter.verified_merge=lambda *args:(new,phase)
        self.data['application']['status']['operationState']['phase']='Succeeded'
        self.data['application'].pop('operation',None)
        prepared=self.adapter.prepare_reviewed('request-001-phase-0',expected)
        self.adapter.dispatch_reviewed(prepared)
        self.assertFalse(self.adapter.sync_workload_after_definition(new,phase,expected))
        self.assertEqual(len(self.calls),1)
        self.data['definition']['status']['sync']['revision']=new
        self.data['application']['spec']['source']['targetRevision']=new
        # Real Argo comparison advances before the workload operation is issued.
        self.data['application']['status']['sync'].update(revision=new,status='OutOfSync')
        self.assertFalse(self.adapter.sync_workload_after_definition(new,phase,expected))
        self.assertEqual(len(self.calls),2)
        for index,(args,kwargs) in enumerate(self.calls):
            patch=json.loads(args[args.index('-p')+1])
            self.assertTrue(all(x['op']=='test' or x['path']=='/operation' for x in patch))
            self.assertFalse(patch[-1]['value']['sync']['prune'])
            self.assertEqual(patch[-1]['value']['sync']['revision'],'main' if index==0 else new)
        self.assertIn(DEFINITION,self.calls[0][0]);self.assertIn('account-release',self.calls[1][0])
        self.data['application']['operation']=copy.deepcopy(prepared['patch'][-1]['value'])
        self.data['application']['operation']['sync']['revision']=new
        self.assertFalse(self.adapter.sync_workload_after_definition(new,phase,expected))
        self.assertEqual(len(self.calls),2)
        self.data['application'].pop('operation')
        prior=copy.deepcopy(self.data['application']['status']['operationState'])
        self.data['application']['status']['operationState']['syncResult']['revision']='c'*40
        with self.assertRaisesRegex(Rejected,'prior-sync-not-successful'):
            self.adapter.sync_workload_after_definition(new,phase,expected)
        self.assertEqual(len(self.calls),2)
        self.data['application']['status']['operationState']=prior
        self.data['application']['status']['operationState']['syncResult']['revision']=new
        self.data['application']['status']['sync']['status']='Synced'
        self.data['rollout']['metadata']['generation']+=1
        self.assertTrue(self.adapter.sync_workload_after_definition(new,phase,expected))
        self.assertEqual(len(self.calls),2)

    def test_restore_proof_requires_full_identity_and_fresh_receipt(self):
        current=self.adapter.observe();proof={k:current[k] for k in ('rollout_uid','generation','git_revision','requested_image','revision')}
        proof.update(verified_at=self.now,receipt_sha256='a'*64,traffic_health_verified=True)
        # Identity and booleans alone are not measured evidence.
        self.assertFalse(self.adapter.proof_matches(proof,current))
        for field,value in [('generation',999),('verified_at',self.now-91),('receipt_sha256','')]:
            bad=dict(proof);bad[field]=value;self.assertFalse(self.adapter.proof_matches(bad,current))

    def test_successful_release_restore_uses_fresh_completion_before_dispatch(self):
        from test_evidence_verifier import receipt
        from evidence_verifier import verify
        data,current=receipt();self.now=1111
        self.data=copy.deepcopy(data['after'])
        self.data['replicasets']['items'][0]['spec']['template']=copy.deepcopy(self.data['rollout']['spec']['template'])
        proof={k:current[k] for k in ('rollout_uid','generation','git_revision','requested_image','revision')}
        proof.update(verify(data,current,CATALOG,self.now),receipt=data)
        path=Path(self.config['state_file']);path.write_text(json.dumps({'completion':proof}))
        current=self.adapter.observe();self.assertEqual(current['state'],'succeeded')
        self.assertTrue(current['stable_traffic_verified'])
        phase=dict(image=next(e['image'] for e in CATALOG if e['image']!=current['current_image']),
                   name='restore-image',strategy='Canary',promotion_mode='auto')
        expected={'identity':Coordinator.identity(current),'phase':0,'phases':[phase]}
        self.adapter.reviewed=lambda *args:{'html_url':'https://github.com/syuhei0519/application-manifest/pull/99'}
        self.adapter.verified_merge=lambda *args:('b'*40,phase)
        prepared=self.adapter.prepare_reviewed('restore-001-phase-0',expected)
        self.assertEqual(prepared['commit'],'b'*40);self.assertEqual(self.calls,[])
        # Preview and apply both fail closed if a receipt ages out during review.
        self.now+=91
        self.assertFalse(self.adapter.observe()['stable_traffic_verified'])
        with self.assertRaisesRegex(Rejected,'stable-traffic-receipt-required'):
            self.adapter.prepare_reviewed('restore-001-phase-0',expected)
        self.assertEqual(self.calls,[])

    def test_restore_receipts_cannot_cross_success_and_abort_states(self):
        from test_evidence_verifier import receipt
        from evidence_verifier import verify
        data,current=receipt();self.now=1111
        proof={k:current[k] for k in ('rollout_uid','generation','git_revision','requested_image','revision')}
        proof.update(verify(data,current,CATALOG,self.now),receipt=data)
        current['state']='recovering'
        self.assertFalse(self.adapter.restore_ready({'completion':proof,'stable_recovery':proof},current))
        data['kind']='recovery'
        for snapshot in (data['before'],data['after']):snapshot['rollout']['status']['abort']=True
        proof.update(verify(data,current,CATALOG,self.now),receipt=data)
        self.assertTrue(self.adapter.restore_ready({'stable_recovery':proof},current))
        current['state']='succeeded'
        self.assertFalse(self.adapter.restore_ready({'completion':proof,'stable_recovery':proof},current))

    def test_promote_only_status_patch_and_stale_resource_version_is_rejected(self):
        self.profile['promotionMode']='manual';ro=self.data['rollout'];steps=ro['spec']['strategy']['canary']['steps']
        ro['spec']['strategy']['canary']['steps']=[item for i in range(0,len(steps),2) for item in [steps[i],steps[i+1],{'pause':{}}]]
        ro['status'].update(currentStepIndex=2,pauseConditions=[{'reason':'CanaryPauseStep'}])
        expected=Coordinator.identity(self.adapter.observe());self.adapter.act('promote',expected)
        args=self.calls[-1][0];self.assertIn('--subresource=status',args)
        patch=json.loads(args[args.index('-p')+1]);self.assertTrue(all(p['path'].startswith(('/metadata/','/status/')) for p in patch))
        ro['metadata']['resourceVersion']='999'
        with self.assertRaisesRegex(Rejected,'stale-runtime-identity'):self.adapter.act('promote',expected)
        self.assertEqual(len(self.calls),1)

    def test_reviewed_apply_rejects_unrelated_merged_tree_change(self):
        current=self.adapter.observe();base=current['git_revision'];new='b'*40
        self.adapter.reviewed=lambda *args:dict(merged=True,merge_commit_sha=new,number=99,html_url='https://github.com/syuhei0519/application-manifest/pull/99')
        self.adapter.diff=lambda *args:{'after':self.profile}
        def gh(method,endpoint,payload=None):
            self.assertEqual(method,'GET')
            if '/git/ref/heads/' in endpoint:return {'object':{'sha':new}}
            if '/pulls/' in endpoint:return [{'filename':PROFILE,'status':'modified'}]
            if '/git/commits/' in endpoint:return {'tree':{'sha':endpoint.rsplit('/',1)[1]}}
            if '/git/trees/' in endpoint:
                sha=endpoint.split('/git/trees/')[1].split('?')[0]
                return {'truncated':False,'tree':[{'path':PROFILE,'mode':'100644','type':'blob','sha':sha},
                    {'path':'learning/account-release/chart/templates/release.yaml','mode':'100644','type':'blob','sha':sha}]}
            self.fail(endpoint)
        self.adapter.gh=gh
        expected={'identity':Coordinator.identity(current),'phase':0,'phases':[{}]}
        with self.assertRaisesRegex(Rejected,'merged-tree-out-of-scope'):self.adapter.apply_reviewed('proposal-001',expected)
        with self.assertRaisesRegex(Rejected,'merged-tree-out-of-scope'):self.adapter.proposal_outcome('proposal-001',expected,{})
        self.assertEqual(self.calls,[])

    def test_reconcile_rejects_another_image_mode_or_replaced_rollout(self):
        current=self.adapter.observe();desired=current['git_revision']
        phase=dict(image=current['requested_image'],strategy=current['strategy'],promotion_mode=current['promotion_mode'],name='restore-image')
        expected={'identity':Coordinator.identity(current),'phase':0,'phases':[phase]}
        self.adapter.reviewed=lambda *args:dict(merged=True,merge_commit_sha=desired)
        self.adapter.verified_merge=lambda *args:(desired,phase)
        self.adapter.proof_matches=lambda *args,**kwargs:True
        Path(self.config['state_file']).write_text(json.dumps({'completion':dict(traffic_health_verified=True,resync_restart_verified=True)}))
        for field,value in [('requested_image',next(e['image'] for e in CATALOG if e['image']!=phase['image'])),
                            ('strategy','Rolling'),('promotion_mode','manual'),('rollout_uid','foreign')]:
            observed=dict(current,state='succeeded',git_runtime_drift=False);observed[field]=value
            self.adapter.observe=lambda:observed
            with self.subTest(field=field),self.assertRaisesRegex(Rejected,'reviewed-runtime-mismatch'):
                self.adapter.proposal_outcome('proposal-001',expected,{})
        self.assertEqual(self.calls,[])

    def test_canary_activation_installs_routes_only_after_verified_old_image_seed(self):
        current=dict(self.adapter.observe(),strategy='Rolling',promotion_mode=None,state='succeeded',
                     git_runtime_drift=False,stable_traffic_verified=True)
        phase=dict(image=current['current_image'],name='activate-mode',strategy='Canary',
                   promotion_mode='auto',bootstrap_hash=current['revision'])
        self.adapter.observe=lambda:copy.deepcopy(current)
        self.adapter.reviewed=lambda *args:{'html_url':'https://github.com/syuhei0519/application-manifest/pull/99'}
        self.adapter.verified_merge=lambda *args:('b'*40,phase)
        self.data['application']['status']['operationState']['phase']='Succeeded'
        self.data['application'].pop('operation',None)
        expected={'identity':Coordinator.identity(current),'phase':0,'phases':[phase]}
        prepared=self.adapter.prepare_reviewed('activate-001-phase-0',expected)
        sync=prepared['patch'][-1]['value']['sync']
        self.assertEqual(prepared['workload_sync_options'],['RespectIgnoreDifferences=false'])
        self.assertTrue(all(p['op']=='test' or p['path']=='/operation' for p in prepared['patch']))
        self.assertFalse(sync['prune']);self.assertEqual(self.calls,[])
        self.assertEqual(self.data['application']['spec']['syncPolicy']['syncOptions'],['RespectIgnoreDifferences=true'])
        for key,value in [('stable_traffic_verified',False),('git_runtime_drift',True),('state','rolling')]:
            original=current[key];current[key]=value
            status=self.data['application']['status']['sync']['status']
            if key=='git_runtime_drift':self.data['application']['status']['sync']['status']='Unknown'
            with self.subTest(key=key),self.assertRaisesRegex(Rejected,'canary-activation-receipt-required'):
                self.adapter.prepare_reviewed('activate-001-phase-0',expected)
            self.data['application']['status']['sync']['status']=status
            current[key]=original
        for key,value in [('name','start-image'),('image','foreign'),('bootstrap_hash','foreign')]:
            original=phase[key];phase[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(Rejected,'canary-activation-receipt-required'):
                self.adapter.prepare_reviewed('activate-001-phase-0',expected)
            phase[key]=original
        current['strategy']='Canary'
        prepared=self.adapter.prepare_reviewed('activate-001-phase-0',expected)
        self.assertEqual(prepared['workload_sync_options'],['RespectIgnoreDifferences=true'])

    def test_apply_persists_managed_identity_and_previous_before_dispatch(self):
        current=self.adapter.observe();new='b'*40
        phase=dict(image=next(e['image'] for e in CATALOG if e['image']!=current['current_image']),name='start-image',strategy='Canary',promotion_mode='auto')
        expected={'identity':Coordinator.identity(current),'phase':0,'phases':[phase]}
        self.adapter.reviewed=lambda *args:{'html_url':'https://github.com/syuhei0519/application-manifest/pull/99'}
        self.adapter.verified_merge=lambda *args:(new,phase)
        self.adapter.proof_matches=lambda *args,**kwargs:True
        self.data['application']['status']['operationState']['phase']='Succeeded'
        self.data['application'].pop('operation',None)
        proof={'receipt_sha256':'c'*64}
        Path(self.config['state_file']).write_text(json.dumps({'verified_current':{'current':current,'proof':proof}}))
        original=self.command
        def dispatch(args,**kwargs):
            state=json.loads(Path(self.config['state_file']).read_text())
            self.assertEqual(state['managed_revisions'][new],'request-001')
            self.assertEqual(state['verified_previous']['image'],current['current_image'])
            return original(args,**kwargs)
        self.adapter.runner=dispatch
        self.adapter.apply_reviewed('request-001-phase-0',expected)
        self.assertEqual(len(self.calls),1)
        patch=json.loads(self.calls[0][0][self.calls[0][0].index('-p')+1])
        self.assertEqual(patch[-1]['value']['sync']['syncOptions'],['RespectIgnoreDifferences=true'])

if __name__=='__main__':unittest.main()
