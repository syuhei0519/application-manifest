# 操作の予約・重複・中断・結果保存を確認し、二重実行や不明な結果の再送を防ぎます。
import copy
import tempfile
from pathlib import Path
import unittest

from contract import DefinitiveNoWriteRejected, Rejected
from operations import Coordinator
from test_integration import CATALOG, IMAGES
from test_release_modes import current, request

ACTOR='user:default/account-trial'


class Adapter:
    def __init__(self):
        self.current=dict(current(),release_id='release-001',rollout_uid='rollout-uid',resource_version='100',
                          current_step=1,requested_image=IMAGES['stable'],actions=[],release_managed=True)
        self.calls=[];self.timeout=False;self.outcome='review-pending';self.terminal=False
    def observe(self):return copy.deepcopy(self.current)
    def diff(self, revision, phase):return {'before':revision,'after':phase}
    def propose(self, request_id, revision, phase):
        self.calls.append(('propose',request_id))
        if self.timeout: raise TimeoutError('must never appear in stored audit: token=private')
        return dict(pr_url='https://github.com/syuhei0519/application-manifest/pull/99',commit='d'*40,base_revision=revision)
    def proposal_outcome(self,*args):
        return dict(state=self.outcome,git_runtime_verified=self.terminal,traffic_health_verified=self.terminal,
                    resync_restart_verified=self.terminal,git_change_absent=False,
                    phase_verified=self.terminal,phase_git_revision=self.current['git_revision'])
    def act(self, operation, expected):
        self.calls.append((operation,expected))
        if self.timeout: raise TimeoutError('private adapter detail')
    def action_outcome(self,*args):return {'state':'unknown' if self.timeout else 'confirmed'}
    def prepare_reviewed(self,*args):
        if not self.terminal:raise Rejected('previous-live-receipt-required')
        return {'prepared':True}
    def dispatch_reviewed(self,prepared):
        self.calls.append(('apply',prepared))
        if self.timeout:raise TimeoutError('unknown apply')


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'operations.sqlite'
        self.adapter=Adapter();self.core=Coordinator(self.path,CATALOG,self.adapter,[ACTOR])
    def tearDown(self):self.core.db.close();self.tmp.cleanup()
    def test_expired_preflight_does_not_consume_dispatch_and_unknown_is_not_retried(self):
        self.core.request(ACTOR,request());self.adapter.outcome='apply-pending'
        self.core.reconcile(ACTOR,'request-001')
        self.assertEqual(self.core.apply_reviewed(ACTOR,'request-001')['state'],'apply-pending')
        self.assertEqual([c for c in self.adapter.calls if c[0]=='apply'],[])
        self.assertFalse(any(e['type']=='apply-dispatched' for e in self.core.history(ACTOR)))
        self.adapter.terminal=True;self.adapter.timeout=True
        self.assertEqual(self.core.apply_reviewed(ACTOR,'request-001')['state'],'outcome-unknown')
        self.core.reconcile(ACTOR,'request-001')
        with self.assertRaisesRegex(Rejected,'apply-already-dispatched'):self.core.apply_reviewed(ACTOR,'request-001')
        self.assertEqual(len([c for c in self.adapter.calls if c[0]=='apply']),1)
    def test_pre_dispatch_rejection_survives_reload_and_allows_fresh_request(self):
        stale=dict(request(),expected_generation=999)
        result=self.core.submit(ACTOR,stale)
        self.assertEqual(result['state'],'rejected');self.assertEqual(self.adapter.calls,[])
        self.core.db.close();self.core=Coordinator(self.path,CATALOG,self.adapter,[ACTOR])
        self.assertEqual(self.core.reconcile(ACTOR,stale['request_id'])['state'],'rejected')
        self.assertEqual(self.core.submit(ACTOR,stale)['state'],'rejected')
        self.assertEqual(self.core.submit(ACTOR,dict(request(),request_id='fresh-request-002'))['state'],'review-pending')
        self.assertEqual(len(self.adapter.calls),1)
    def test_duplicate_after_restart_does_not_create_second_pr(self):
        first=self.core.request(ACTOR,request());self.core.db.close()
        self.core=Coordinator(self.path,CATALOG,self.adapter,[ACTOR])
        self.assertEqual(self.core.request(ACTOR,request()),first)
        self.assertEqual(len(self.adapter.calls),1)
        with self.assertRaisesRegex(Rejected,'request-id-reused'):
            self.core.request(ACTOR,dict(request(),promotion_mode='auto'))
    def test_unknown_external_outcome_survives_restart_and_reconcile_is_read_only(self):
        self.adapter.timeout=True
        self.assertEqual(self.core.request(ACTOR,request())['state'],'outcome-unknown')
        self.core.db.close();self.core=Coordinator(self.path,CATALOG,self.adapter,[ACTOR])
        self.adapter.outcome='outcome-unknown'
        self.core.reconcile(ACTOR,'request-001')
        self.core.request(ACTOR,request())
        self.assertEqual(len(self.adapter.calls),1)

    def test_absent_pr_observation_never_releases_reservation(self):
        self.assertEqual(self.core.request(ACTOR,request())['state'],'review-pending')
        self.adapter.proposal_outcome=lambda *args:{'state':'outcome-unknown','reason':'proposal-not-found'}
        result=self.core.reconcile(ACTOR,'request-001')
        self.assertEqual(result['state'],'outcome-unknown')
        self.assertEqual(self.core.db.execute('SELECT request_id FROM exclusion').fetchone()[0],'request-001')
        with self.assertRaisesRegex(Rejected,'service-environment-busy'):
            self.core.request(ACTOR,dict(request(),request_id='request-002'))
        self.assertNotIn('private',str(self.core.history(ACTOR)))
        with self.assertRaisesRegex(Rejected,'service-environment-busy'):
            self.core.request(ACTOR,dict(request(),request_id='request-002'))

    def test_wrong_git_base_is_unknown_and_keeps_reservation(self):
        original=self.adapter.propose
        self.adapter.propose=lambda *args:dict(original(*args),base_revision='e'*40)
        self.assertEqual(self.core.request(ACTOR,request())['state'],'outcome-unknown')
        self.assertEqual(self.core.db.execute('SELECT count(*) FROM exclusion').fetchone()[0],1)

    def test_typed_pre_write_main_conflict_rejects_and_releases_reservation(self):
        def conflict(*args):
            self.adapter.calls.append(('propose',args[0]))
            raise DefinitiveNoWriteRejected('git-base-changed')
        self.adapter.propose=conflict
        result=self.core.request(ACTOR,request())
        self.assertEqual(result['state'],'rejected')
        self.assertEqual(result['result'],{'state':'rejected','reason':'git-base-changed','git_change_absent':True})
        self.assertEqual(self.core.db.execute('SELECT count(*) FROM exclusion').fetchone()[0],0)
        self.assertEqual(len(self.adapter.calls),1)
        self.assertEqual(self.core.request(ACTOR,request()),result)
        self.assertEqual(len(self.adapter.calls),1)

    def test_all_derived_phase_ids_are_validated_before_reservation(self):
        accepted=dict(request(),request_id='a'*72)
        self.assertEqual(self.core.request(ACTOR,accepted)['state'],'review-pending')
        self.assertEqual(self.adapter.calls,[('propose','a'*72+'-phase-0')])

        self.core.db.close();self.tmp.cleanup()
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'operations.sqlite'
        self.adapter=Adapter();self.core=Coordinator(self.path,CATALOG,self.adapter,[ACTOR])
        rejected=self.core.submit(ACTOR,dict(request(),request_id='b'*73))
        self.assertEqual(rejected['state'],'rejected')
        self.assertEqual(rejected['result']['reason'],'derived-proposal-id-invalid')
        self.assertEqual(self.adapter.calls,[])
        self.assertEqual(self.core.db.execute('SELECT count(*) FROM exclusion').fetchone()[0],0)

    def test_adapter_evidence_cannot_leak_arbitrary_reason_or_identity(self):
        self.core.request(ACTOR,request())
        for reason in ({'token':'synthetic-private'},'HTTP error Authorization: synthetic-private'):
            self.adapter.proposal_outcome=lambda *args: {'state':'review-pending','reason':reason}
            result=self.core.reconcile(ACTOR,'request-001')
            self.assertEqual(result['result']['reason'],'observation-unavailable')
            self.assertNotIn('synthetic-private',str(self.core.history(ACTOR)))
        self.adapter.proposal_outcome=lambda *args: {'state':'review-pending','commit':'token=synthetic-private'}
        with self.assertRaisesRegex(Rejected,'unsafe-observation-identity'):
            self.core.reconcile(ACTOR,'request-001')
    def test_history_links_actions_using_saved_git_identity_not_current_runtime(self):
        self.core.request(ACTOR,request())
        self.core.event('request-001',ACTOR,'running',{'commit':self.adapter.current['git_revision']},'git-runtime')
        self.core.action(ACTOR,self.action())
        self.adapter.current.update(release_id='different-release',generation=99)
        history=self.core.history(ACTOR)
        actions=[e['detail'] for e in history if e['request_id']=='action-001']
        self.assertTrue(actions)
        for detail in actions:
            self.assertEqual(detail['release_id'],'release-001')
            self.assertEqual(detail['generation'],3)
            self.assertEqual(detail['release_request_id'],'request-001')

    def test_history_never_guesses_ambiguous_or_missing_release_request(self):
        self.core.request(ACTOR,request())
        self.core.db.execute("INSERT INTO requests SELECT 'request-002',payload,fingerprint,actor,kind,state,expected,result,created FROM requests WHERE id='request-001'")
        for owner in ('request-001','request-002'):
            self.core.event(owner,ACTOR,'running',{'commit':self.adapter.current['git_revision']},'git-runtime')
        self.core.action(ACTOR,self.action())
        for event in self.core.history(ACTOR):
            if event['request_id']=='action-001':self.assertNotIn('release_request_id',event['detail'])

    def test_public_observed_generation_rejects_non_integer_or_invalid_values(self):
        self.core.request(ACTOR,request())
        for value in (True,-1,'1'):
            self.adapter.proposal_outcome=lambda *args:dict(state='review-pending',generation=value)
            with self.assertRaisesRegex(Rejected,'unsafe-observation-generation'):
                self.core.reconcile(ACTOR,'request-001')

    def test_second_connection_cannot_take_active_target(self):
        self.core.request(ACTOR,request())
        other=Coordinator(self.path,CATALOG,self.adapter,[ACTOR])
        try:
            with self.assertRaisesRegex(Rejected,'service-environment-busy'):
                other.request(ACTOR,dict(request(),request_id='request-002'))
        finally:other.db.close()
    def test_terminal_requires_actual_git_runtime_traffic_evidence(self):
        self.core.request(ACTOR,request());self.adapter.outcome='succeeded'
        with self.assertRaisesRegex(Rejected,'terminal-evidence-incomplete'):
            self.core.reconcile(ACTOR,'request-001')
        self.adapter.terminal=True
        self.assertEqual(self.core.reconcile(ACTOR,'request-001')['state'],'succeeded')
        self.assertEqual(self.core.db.execute('SELECT count(*) FROM exclusion').fetchone()[0],0)
    def test_foreign_actor_stale_request_wrong_target_never_reach_adapter(self):
        for actor,payload in [('user:default/other',request()),(ACTOR,dict(request(),expected_generation=2)),
                              (ACTOR,dict(request(),environment='production'))]:
            with self.assertRaises(Rejected):self.core.request(actor,payload)
        self.assertEqual(self.adapter.calls,[])
    def action(self):
        self.adapter.current.update(state='paused',strategy='Canary',promotion_mode='manual',
                                    actions=['promote','abort'],pause_reasons=['CanaryPauseStep'])
        return dict(request_id='action-001',operation='promote',entity='component:default/account',
                    environment='local',expected=self.core.identity(self.adapter.current))
    def test_actions_reject_stale_rolling_auto_and_duplicate(self):
        payload=self.action()
        self.adapter.current['resource_version']='101'
        with self.assertRaisesRegex(Rejected,'stale-runtime'):self.core.action(ACTOR,payload)
        self.adapter.current['resource_version']='100';self.adapter.current['strategy']='Rolling'
        with self.assertRaises(Rejected):self.core.action(ACTOR,payload)
        self.adapter.current.update(strategy='Canary',promotion_mode='auto')
        with self.assertRaises(Rejected):self.core.action(ACTOR,payload)
        self.adapter.current['promotion_mode']='manual'
        self.assertEqual(self.core.action(ACTOR,payload)['state'],'confirmed')
        self.core.action(ACTOR,payload)
        self.assertEqual(len(self.adapter.calls),1)
    def test_timeout_action_blocks_next_and_never_blindly_repeats(self):
        payload=self.action();self.adapter.timeout=True
        self.assertEqual(self.core.action(ACTOR,payload)['state'],'outcome-unknown')
        self.core.action(ACTOR,payload)
        with self.assertRaisesRegex(Rejected,'runtime-operation-unresolved'):
            self.core.action(ACTOR,dict(payload,request_id='action-002'))
        self.core.reconcile(ACTOR,'action-001')
        self.assertEqual(len(self.adapter.calls),1)
    def test_restore_requires_verified_previous_and_abort_recovery(self):
        self.adapter.current.update(state='recovering',requested_image=IMAGES['candidate'],
            stable_traffic_verified=True,verified_previous={'verified':True,'image':IMAGES['stable'],'git_revision':'b'*40})
        req=dict(request(),image=IMAGES['stable'])
        payload=dict(request=req,reason='前版へ復元',previous_git_revision='b'*40)
        preview=self.core.preview(ACTOR,payload,'restore')
        self.assertEqual(preview['phases'][0]['name'],'restore-image')
        self.adapter.current['stable_traffic_verified']=False
        with self.assertRaises(Rejected):self.core.preview(ACTOR,payload,'restore')

    def test_restore_after_success_uses_verified_previous_and_reserves_once(self):
        self.adapter.current.update(state='succeeded',current_image=IMAGES['candidate'],
            requested_image=IMAGES['candidate'],stable_traffic_verified=True,
            verified_previous={'verified':True,'image':IMAGES['stable'],'git_revision':'b'*40})
        payload=dict(request=dict(request(),image=IMAGES['stable']),
                     reason='Restore previous verified version',previous_git_revision='b'*40)
        first=self.core.request(ACTOR,payload,'restore')
        self.assertEqual(first['state'],'review-pending')
        self.assertEqual(self.core.request(ACTOR,payload,'restore'),first)
        self.assertEqual(self.adapter.calls,[('propose','request-001-phase-0')])
    def test_multi_phase_cannot_claim_terminal_after_first_phase(self):
        self.core.request(ACTOR,request('Rolling',None))
        self.adapter.outcome='succeeded';self.adapter.terminal=True
        with self.assertRaisesRegex(Rejected,'terminal-evidence-incomplete'):
            self.core.reconcile(ACTOR,'request-001')

    def test_switch_requires_verified_phase_then_new_review(self):
        self.core.request(ACTOR,request('Rolling',None))
        self.adapter.outcome='phase-review-required'
        self.core.reconcile(ACTOR,'request-001')
        expected=self.core.identity(self.adapter.current)
        with self.assertRaisesRegex(Rejected,'phase-evidence-incomplete'):
            self.core.advance(ACTOR,'request-001',expected)
        self.adapter.terminal=True
        result=self.core.advance(ACTOR,'request-001',expected)
        self.assertEqual(result['state'],'review-pending')
        self.assertEqual(result['expected']['phase'],1)
        self.assertEqual(self.adapter.calls,[('propose','request-001-phase-0'),('propose','request-001-phase-1')])
        with self.assertRaisesRegex(Rejected,'phase-not-ready'):
            self.core.advance(ACTOR,'request-001',expected)

    def test_restore_transfers_aborted_reservation_without_unlocked_gap(self):
        self.core.request(ACTOR,request())
        self.adapter.outcome='recovering';self.core.reconcile(ACTOR,'request-001')
        self.adapter.current.update(state='recovering',requested_image=IMAGES['candidate'],
            release_request_id='request-001',stable_traffic_verified=True,
            verified_previous={'verified':True,'image':IMAGES['stable'],'git_revision':'b'*40})
        payload=dict(request=dict(request(),request_id='restore-001',image=IMAGES['stable']),
                     reason='前版へ復元',previous_git_revision='b'*40)
        result=self.core.request(ACTOR,payload,'restore')
        self.assertEqual(result['state'],'review-pending')
        self.assertEqual(self.core.record('request-001')['state'],'recovery-superseded')
        self.assertEqual(self.core.db.execute('SELECT request_id FROM exclusion').fetchone()[0],'restore-001')
        self.adapter.outcome='phase-review-required';self.adapter.terminal=True
        self.assertEqual(self.core.reconcile(ACTOR,'request-001')['state'],'recovery-superseded')
        with self.assertRaises(Rejected):
            self.core.advance(ACTOR,'request-001',self.core.identity(self.adapter.current))
        self.adapter.outcome='succeeded'
        with self.assertRaisesRegex(Rejected,'wrong-terminal-kind'):
            self.core.reconcile(ACTOR,'restore-001')
        self.assertEqual(self.core.db.execute('SELECT request_id FROM exclusion').fetchone()[0],'restore-001')
        self.adapter.outcome='restored'
        self.assertEqual(self.core.reconcile(ACTOR,'restore-001')['state'],'restored')


if __name__=='__main__':unittest.main()
